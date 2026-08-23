"""Bilder fuer die Heuser-Slides erzeugen.

Zwei Dinge, die ueber die Qualitaet des Sliders entscheiden:

1. STIL-PRAEFIX. Sechs unabhaengige Prompts liefern sechs verschiedene
   Wohnungen und der Slider faellt visuell auseinander. Deshalb liefert das
   Sprachmodell nur das MOTIV ("leeres Wohnzimmer mit Sofa am Fenster"), und
   der Look kommt aus der config.yaml. So bleibt er ueber Monate stabil und
   laesst sich an einer Stelle nachjustieren.

2. CACHE. Laeuft die Action zweimal ueber dasselbe Briefing, kostet der
   zweite Lauf nichts. Schluessel ist der Hash des fertigen Prompts.
"""
from __future__ import annotations

import hashlib
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


def prompt_bauen(motiv: str, cfg) -> str:
    stil = cfg.get("slides.bild.stil", STIL_VORGABE)
    return f"{motiv.strip().rstrip('.')}. {stil}"


def _cache_pfad(prompt: str, cfg) -> Path:
    ordner = cfg.path(cfg.get("slides.bild.cache", "out/_bilder"))
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner / f"{hashlib.sha256(prompt.encode()).hexdigest()[:16]}.jpg"


def erzeuge(motiv: str, cfg) -> Path:
    """Motiv -> Bilddatei. Aus dem Cache, wenn der Prompt schon einmal lief."""
    prompt = prompt_bauen(motiv, cfg)
    ziel = _cache_pfad(prompt, cfg)
    if ziel.exists() and ziel.stat().st_size > 1000:
        return ziel

    provider = cfg.get("slides.bild.provider", "")
    fn = PROVIDER.get(provider)
    if fn is None:
        raise RuntimeError(
            f"Unbekannter Bild-Provider {provider!r}. "
            f"Verfuegbar: {', '.join(PROVIDER) or '(noch keiner eingebaut)'}")

    daten = fn(prompt, cfg)
    ziel.write_bytes(daten)
    return ziel


def erzeuge_alle(slides: list[dict], cfg) -> list[dict]:
    """Fuegt jedem Slide den Pfad seines Fotos hinzu.

    Schlaegt ein Bild fehl, bleibt der Slide ohne Foto - der Renderer zeichnet
    dann die reine Cremeflaeche. Besser ein schlichter Slide als ein Abbruch
    des ganzen Laufs.
    """
    out = []
    for s in slides:
        s = dict(s)
        motiv = s.pop("motiv", "")
        if motiv:
            try:
                s["photo"] = str(erzeuge(motiv, cfg))
            except Exception as exc:                          # noqa: BLE001
                print(f"[!] Bild fehlgeschlagen ({motiv[:40]}): {exc}")
        out.append(s)
    return out


# ---------------------------------------------------------------- Provider
#
# Jede Funktion bekommt (prompt, cfg) und gibt die Bilddatei als bytes zurueck.
# Sobald der Provider feststeht, hier eine Funktion ergaenzen und in PROVIDER
# eintragen. Alles darueber bleibt unveraendert.
#
# def _beispiel(prompt: str, cfg) -> bytes:
#     r = requests.post(URL, headers=..., json={"prompt": prompt}, timeout=180)
#     r.raise_for_status()
#     bild_url = r.json()["..."]
#     return requests.get(bild_url, timeout=120).content

PROVIDER: dict[str, callable] = {}
