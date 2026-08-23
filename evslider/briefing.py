"""Briefing -> Slides. Formloser Text aus Telegram wird zu Slide-Daten.

Gegenstueck zu scrape.py: Waehrend dort ein Expose aus einer Website geholt
wird, entsteht hier die Slide-Struktur aus einem kurzen Briefing. Beides
muendet danach in denselben Ablauf (rendern, hochladen, einplanen).

Beispiel-Briefing aus Telegram:
    heu 5 Fehler beim Immobilienkauf in Bonn, CTA Netzwerk

Ergebnis: Cover, vier Inhaltsslides, CTA - je mit Eyebrow, Headline, Subline
und einem Bildmotiv fuer die Bilderzeugung.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import requests

API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = """Du baust Instagram-Karussells für einen Makler in Bonn.

Antworte AUSSCHLIESSLICH mit JSON, ohne Vorrede und ohne Markdown-Zäune.

Format:
{"slides": [
  {"kind": "cover",   "eyebrow": "...", "headline": "...", "subline": "...",
   "motiv": "..."},
  {"kind": "content", "eyebrow": "...", "headline": "...", "subline": "...",
   "motiv": "..."},
  {"kind": "cta",     "eyebrow": "...", "headline": "...", "subline": "...",
   "motiv": "..."}
]}

Regeln:
- Erster Slide ist "cover", letzter ist "cta", dazwischen "content".
- eyebrow: max. 3 Wörter. Auf Inhaltsslides nur dann durchnummeriert
  ("Fehler 01"), wenn das Briefing wirklich eine Aufzählung ist.
- headline: 2 bis 5 Wörter, keine Satzzeichen am Ende. Auf dem Cover ein
  einzelnes starkes Wort oder eine Zahl plus Wort.
- subline: ein bis zwei kurze Sätze, zusammen höchstens 120 Zeichen.
  Auf Cover und CTA höchstens 4 Wörter.
- Bricht eine Headline schlecht um, setze `|` an die gewünschte Umbruchstelle
  oder `~` an eine erlaubte Trennstelle innerhalb eines langen Wortes.
- motiv: was auf dem Foto zu sehen ist, ein knapper Satz. NUR das Motiv,
  keine Angaben zu Stil, Licht, Kamera oder Farben.
- Sprache: Deutsch, Sie-Form, sachlich. Keine Ausrufezeichen, keine Emojis,
  keine Superlative.
- Inhaltlich korrekt bleiben. Keine erfundenen Zahlen, Gesetze oder Fristen.
"""


@dataclass
class Briefing:
    """Was aus dem Briefing entstanden ist."""
    slides: list[dict] = field(default_factory=list)
    keyword: str = ""
    thema: str = ""

    def caption(self, cfg) -> str:
        """Instagram-Text aus dem Briefing.

        Keine Erfindung neuer Inhalte: Es wird zusammengesetzt, was schon auf
        den Slides steht. Der Slider ist die Botschaft, der Text fuehrt hin.
        """
        cover = next((s for s in self.slides if s["kind"] == "cover"), None)
        cta = next((s for s in self.slides if s["kind"] == "cta"), None)
        zeilen = []
        if cover:
            kopf = " ".join(x for x in (cover.get("eyebrow"),
                                        cover.get("headline")) if x)
            if kopf:
                zeilen.append(kopf)
        for s in self.slides:
            if s["kind"] != "content":
                continue
            teil = s.get("headline", "").replace("|", " ").replace("~", "")
            sub = s.get("subline", "")
            zeilen.append(f"· {teil}" + (f" — {sub}" if sub else ""))
        if self.keyword:
            vorlage = cfg.get("caption.cta_template", "")
            if vorlage:
                zeilen.append("")
                zeilen.append(vorlage.format(keyword=self.keyword))
        elif cta and cta.get("subline"):
            zeilen.append("")
            zeilen.append(cta["subline"])
        tags = cfg.get("caption.hashtags") or []
        if tags:
            zeilen.append("")
            zeilen.append(" ".join(tags))
        text = "\n".join(zeilen).strip()
        grenze = int(cfg.get("caption.max_chars", 1400))
        return text[:grenze]

    def titel(self) -> str:
        """Kurztitel fuer Ordnernamen und Telegram-Rueckmeldung."""
        if self.slides:
            s = self.slides[0]
            return " ".join(x for x in (s.get("eyebrow"), s.get("headline")) if x)
        return self.thema or "slider"


def _key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if not k:
        raise RuntimeError("ANTHROPIC_API_KEY fehlt.")
    return k


def _json_aus_antwort(text: str) -> dict:
    """Robust gegen Markdown-Zaeune und Vorrede."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    if not text.startswith("{"):
        anfang = text.find("{")
        ende = text.rfind("}")
        if anfang < 0 or ende <= anfang:
            raise ValueError(f"Keine JSON-Antwort erhalten: {text[:200]}")
        text = text[anfang:ende + 1]
    return json.loads(text)


def _pruefen(daten: dict, max_slides: int) -> list[dict]:
    slides = daten.get("slides") or []
    if not slides:
        raise ValueError("Antwort enthaelt keine Slides.")
    out = []
    for i, s in enumerate(slides[:max_slides]):
        kind = s.get("kind", "content")
        if kind not in ("cover", "content", "cta"):
            kind = "content"
        if not (s.get("headline") or "").strip():
            continue                      # ohne Headline kein Slide
        out.append({
            "kind": kind,
            "eyebrow": (s.get("eyebrow") or "").strip(),
            "headline": (s.get("headline") or "").strip(),
            "subline": (s.get("subline") or "").strip(),
            "motiv": (s.get("motiv") or "").strip(),
        })
    if not out:
        raise ValueError("Keine verwertbaren Slides in der Antwort.")
    return out


def erzeuge(text: str, cfg, keyword: str = "") -> Briefing:
    """Briefing-Text -> Briefing. Wirft bei unbrauchbarer Antwort."""
    modell = cfg.get("caption.model", "claude-sonnet-5")
    max_slides = int(cfg.get("slides.max_total", 10))
    anweisung = cfg.get("briefing.zusatz", "") or ""

    frage = f"Briefing:\n{text.strip()}\n\nHöchstens {max_slides} Slides."
    if keyword:
        frage += (f"\nDer CTA-Slide fordert dazu auf, \"{keyword}\" zu "
                  f"kommentieren.")
    if anweisung:
        frage += f"\n\nZusätzlich beachten:\n{anweisung}"

    r = requests.post(
        API_URL,
        headers={"x-api-key": _key(),
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": modell,
              "max_tokens": 2000,
              "system": SYSTEM,
              "messages": [{"role": "user", "content": frage}]},
        timeout=120,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Anthropic {r.status_code}: {r.text[:300]}")

    antwort = "".join(b.get("text", "") for b in r.json().get("content", [])
                      if b.get("type") == "text")
    slides = _pruefen(_json_aus_antwort(antwort), max_slides)
    return Briefing(slides=slides, keyword=keyword, thema=text.strip()[:200])
