"""Bilder fuer die Heuser-Slides erzeugen.

Zwei Dinge, die ueber die Qualitaet des Sliders entscheiden:

1. STIL-PRAEFIX. Sechs unabhaengige Prompts liefern sechs verschiedene
   Wohnungen und der Slider faellt visuell auseinander. Deshalb liefert das
   Sprachmodell nur das MOTIV ("leeres Wohnzimmer mit Sofa am Fenster"), und
   der Look kommt aus der config.yaml. So bleibt er ueber Monate stabil und
   laesst sich an einer Stelle nachjustieren.

2. CACHE. Laeuft die Action zweimal ueber dasselbe Briefing, kostet der
   zweite Lauf nichts. Schluessel ist der Hash aus Prompt, Modell, Format
   und Aufloesung.

Zugangsdaten fuer Higgsfield kommen aus der Umgebung, nie aus der YAML:
  HIGGSFIELD_API_KEY_ID
  HIGGSFIELD_API_KEY_SECRET
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import requests

# Voreinstellung, passend zu den Referenz-Slides: heller Eichenboden, warmes
# Seitenlicht, Leinenmoebel, ueberbelichtete Fenster. Ueber slides.bild.stil
# pro Shop ueberschreibbar.
STIL_VORGABE = (
    "Architekturfotografie eines hellen, minimalistisch eingerichteten Neubaus. "
    "Warmes, weiches Tageslicht von der Seite, leicht ueberbelichtete Fenster. "
    "Helle Eichendielen, cremeweisse Waende, Leinenmoebel in Beige. "
    "Ruhige, aufgeraeumte Komposition, keine Menschen, keine Schrift, "
    "keine Logos. Natuerliche Farben, kein HDR, kein Weitwinkel-Verzug."
)

HF_BASE = "https://platform.higgsfield.ai"


def prompt_bauen(motiv: str, cfg) -> str:
    stil = cfg.get("slides.bild.stil") or STIL_VORGABE
    return f"{motiv.strip().rstrip('.')}. {stil}"


def _cache_pfad(schluessel: str, cfg) -> Path:
    ordner = cfg.path(cfg.get("slides.bild.cache", "out/_bilder"))
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner / f"{hashlib.sha256(schluessel.encode()).hexdigest()[:16]}.jpg"


def erzeuge(motiv: str, cfg, aspect: str = "3:4") -> Path:
    """Motiv -> Bilddatei. Aus dem Cache, wenn derselbe Auftrag schon lief."""
    prompt = prompt_bauen(motiv, cfg)
    provider = cfg.get("slides.bild.provider", "")
    modell = cfg.get("slides.bild.model", "higgsfield-ai/soul/standard")
    aufloesung = cfg.get("slides.bild.resolution", "720p")

    ziel = _cache_pfad(f"{provider}|{modell}|{aspect}|{aufloesung}|{prompt}", cfg)
    if ziel.exists() and ziel.stat().st_size > 1000:
        print(f"  [i] Bild aus dem Zwischenspeicher: {ziel.name}")
        return ziel

    fn = PROVIDER.get(provider)
    if fn is None:
        raise RuntimeError(
            f"Unbekannter Bild-Provider {provider!r}. "
            f"Verfuegbar: {', '.join(PROVIDER)}")

    ziel.write_bytes(fn(prompt, cfg, aspect))
    return ziel


def erzeuge_alle(slides: list[dict], cfg) -> list[dict]:
    """Fuegt jedem Slide den Pfad seines Fotos hinzu.

    Cover und CTA sind vollflaechig hochkant, Inhaltsslides zeigen nur ein
    breites Band im unteren Bereich - deshalb zwei Formate.

    Schlaegt ein Bild fehl, bleibt der Slide ohne Foto; der Renderer zeichnet
    dann die reine Cremeflaeche. Besser ein schlichter Slide als ein Abbruch
    des ganzen Laufs.
    """
    hoch = cfg.get("slides.bild.aspect_cover", "3:4")
    quer = cfg.get("slides.bild.aspect_content", "4:3")
    out = []
    for s in slides:
        s = dict(s)
        motiv = s.pop("motiv", "")
        if motiv:
            aspect = hoch if s.get("kind") in ("cover", "cta") else quer
            try:
                s["photo"] = str(erzeuge(motiv, cfg, aspect))
            except Exception as exc:                          # noqa: BLE001
                print(f"[!] Bild fehlgeschlagen ({motiv[:40]}): {exc}")
        out.append(s)
    return out


# ---------------------------------------------------------------- Provider
#
# Jede Funktion bekommt (prompt, cfg, aspect) und gibt die Bilddatei als
# bytes zurueck.

def _hf_kopf() -> dict:
    schluessel = os.environ.get("HIGGSFIELD_API_KEY_ID")
    geheim = os.environ.get("HIGGSFIELD_API_KEY_SECRET")
    if not schluessel or not geheim:
        raise RuntimeError("HIGGSFIELD_API_KEY_ID oder ..._SECRET fehlt.")
    return {"Authorization": f"Key {schluessel}:{geheim}",
            "Content-Type": "application/json",
            "Accept": "application/json"}


def higgsfield(prompt: str, cfg, aspect: str) -> bytes:
    """Auftrag abschicken, auf das Ergebnis warten, Bild herunterladen.

    Die API arbeitet asynchron: Der erste Aufruf liefert nur eine Auftrags-
    nummer, das fertige Bild kommt erst beim Nachfragen.
    """
    modell = cfg.get("slides.bild.model", "higgsfield-ai/soul/standard")
    kopf = _hf_kopf()

    r = requests.post(
        f"{HF_BASE}/{modell.strip('/')}",
        headers=kopf,
        json={"prompt": prompt,
              "aspect_ratio": aspect,
              "resolution": cfg.get("slides.bild.resolution", "720p")},
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Higgsfield {r.status_code}: {r.text[:300]}")

    auftrag = r.json()
    status_url = auftrag.get("status_url")
    if not status_url:
        raise RuntimeError(f"Keine status_url erhalten: {str(auftrag)[:300]}")

    frist = time.time() + float(cfg.get("slides.bild.timeout_s", 240))
    wartezeit = 3.0
    while True:
        if time.time() > frist:
            raise RuntimeError("Higgsfield: Zeitueberschreitung beim Warten.")
        time.sleep(wartezeit)
        wartezeit = min(wartezeit * 1.3, 10.0)   # sanft langsamer nachfragen

        s = requests.get(status_url, headers=kopf, timeout=60)
        if s.status_code >= 400:
            raise RuntimeError(f"Higgsfield Status {s.status_code}: {s.text[:200]}")
        stand = s.json()
        zustand = stand.get("status")

        if zustand == "completed":
            bilder = stand.get("images") or []
            if not bilder or not bilder[0].get("url"):
                raise RuntimeError("Higgsfield meldet fertig, liefert aber kein Bild.")
            bild = requests.get(bilder[0]["url"], timeout=120)
            bild.raise_for_status()
            return bild.content

        if zustand in ("failed", "canceled", "nsfw"):
            grund = stand.get("error") or zustand
            # nsfw heisst hier fast immer: der Prompt wurde falsch verstanden,
            # nicht dass etwas Anstoessiges gewollt war.
            raise RuntimeError(f"Higgsfield abgebrochen ({zustand}): {grund}")


PROVIDER: dict[str, callable] = {
    "higgsfield": higgsfield,
}
