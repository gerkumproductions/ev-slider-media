"""Briefing lesen. Ein Slide pro Absatz, kein Sprachmodell im Spiel.

Was in der Telegram-Nachricht steht, landet unveraendert auf den Slides. Das
Werkzeug setzt und veroeffentlicht nur - es denkt sich nichts aus.

Format (Leerzeile trennt die Slides):

    heu
    CTA: NETZWERK

    E: Immobilienkauf in Bonn
    H: 5 Fehler
    S: die teuer werden
    B: Villenstrasse in Bad Godesberg im Nachmittagslicht

    E: Fehler 01
    H: Die Neben~kosten unterschaetzen
    S: Grunderwerbsteuer 6,5 %, Notar und Grundbuch 2 % - bei 600.000 Euro
       sind das ueber 50.000 Euro obendrauf.
    B: Schreibtisch mit aufgeschlagenen Unterlagen am Fenster

    E: Sie suchen in Bad Godesberg?
    H: Netzwerk
    S: jetzt kommentieren
    B: Handschlag vor einer Villa

  E:  Kleinzeile ueber der Headline      (optional)
  H:  Headline                           (Pflicht)
  S:  Text unter der Trennlinie          (optional)
  B:  Bildmotiv fuer die Bilderzeugung   (optional, ohne B bleibt der Slide leer)

Der erste Absatz wird als Cover gesetzt, der letzte als CTA, alles dazwischen
als Inhaltsslide. Mit COVER, INHALT oder CTA als eigener Zeile im Absatz laesst
sich das ueberschreiben.

In der Headline steuern zwei Zeichen den Umbruch:
  |   erzwingt hier einen Zeilenumbruch
  ~   erlaubt hier eine Trennung innerhalb eines langen Wortes
In E: und S: werden beide Zeichen entfernt, dort bricht der Satz von selbst um.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MARKEN = {"e": "eyebrow", "h": "headline", "s": "subline", "b": "motiv"}
ARTEN = {"cover": "cover", "inhalt": "content", "content": "content", "cta": "cta"}

BEISPIEL = (
    "So sieht ein Briefing aus:\n\n"
    "heu\n"
    "CTA: NETZWERK\n\n"
    "E: Immobilienkauf in Bonn\n"
    "H: 5 Fehler\n"
    "S: die teuer werden\n"
    "B: Villenstrasse in Bad Godesberg\n\n"
    "E: Fehler 01\n"
    "H: Die Neben~kosten unterschaetzen\n"
    "S: Grunderwerbsteuer 6,5 %, Notar und Grundbuch 2 %.\n"
    "B: Schreibtisch mit Unterlagen\n\n"
    "Leerzeile trennt die Slides. H: ist Pflicht, der Rest optional."
)


@dataclass
class Briefing:
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
                zeilen.append(_sauber(kopf))
        for s in self.slides:
            if s["kind"] != "content":
                continue
            teil = _sauber(s.get("headline", ""))
            sub = s.get("subline", "")
            zeilen.append(f"· {teil}" + (f" — {sub}" if sub else ""))
        if self.keyword:
            vorlage = cfg.get("caption.cta_template", "")
            if vorlage:
                zeilen += ["", vorlage.format(keyword=self.keyword)]
        elif cta and cta.get("subline"):
            zeilen += ["", cta["subline"]]
        tags = cfg.get("caption.hashtags") or []
        if tags:
            zeilen += ["", " ".join(tags)]
        return "\n".join(zeilen).strip()[:int(cfg.get("caption.max_chars", 1400))]

    def titel(self) -> str:
        if self.slides:
            s = self.slides[0]
            return _sauber(" ".join(x for x in (s.get("eyebrow"),
                                                s.get("headline")) if x))
        return self.thema or "slider"


def _sauber(text: str) -> str:
    """Umbruchmarken raus, Mehrfachleerzeichen zusammenziehen."""
    return re.sub(r"\s+", " ", (text or "").replace("~", "").replace("|", " ")).strip()


def _absaetze(text: str) -> list[list[str]]:
    """Nach Leerzeilen trennen. Eingerueckte Folgezeilen gehoeren zur
    vorherigen Marke - so darf S: ueber mehrere Zeilen laufen."""
    bloecke, aktuell = [], []
    for roh in text.splitlines():
        if not roh.strip():
            if aktuell:
                bloecke.append(aktuell)
                aktuell = []
            continue
        if re.match(r"^\s*[EeHhSsBb]\s*:", roh) or not aktuell:
            aktuell.append(roh.strip())
        else:
            aktuell[-1] = f"{aktuell[-1]} {roh.strip()}"
    if aktuell:
        bloecke.append(aktuell)
    return bloecke


def _slide(zeilen: list[str]) -> tuple[dict, str]:
    """Ein Absatz -> Slide. Zweiter Rueckgabewert ist eine gesetzte Art."""
    slide, art = {}, ""
    for zeile in zeilen:
        m = re.match(r"^([EeHhSsBb])\s*:\s*(.*)$", zeile)
        if m:
            feld = MARKEN[m.group(1).lower()]
            wert = m.group(2).strip()
            slide[feld] = wert if feld == "headline" else _sauber(wert)
            continue
        schlicht = re.sub(r"[^a-zäöü]", "", zeile.lower())
        if schlicht in ARTEN:
            art = ARTEN[schlicht]
    return slide, art


def erzeuge(text: str, cfg, keyword: str = "") -> Briefing:
    """Telegram-Text -> Briefing. Kein Sprachmodell, keine Erfindung."""
    max_slides = int(cfg.get("slides.max_total", 10))

    # Kopfzeile "CTA: WORT" darf ueberall stehen und zaehlt nicht als Slide.
    m = re.search(r"^\s*CTA\s*:\s*([0-9A-Za-zÄÖÜäöüß_-]{2,30})\s*$",
                  text, re.MULTILINE)
    if m:
        keyword = keyword or m.group(1).strip()
        text = text[:m.start()] + text[m.end():]

    roh = [_slide(z) for z in _absaetze(text)]
    roh = [(s, a) for s, a in roh if s.get("headline")]
    if not roh:
        raise ValueError("Kein Slide erkannt - H: fehlt.\n\n" + BEISPIEL)

    slides = []
    for i, (s, art) in enumerate(roh[:max_slides]):
        if not art:
            if i == 0:
                art = "cover"
            elif i == len(roh[:max_slides]) - 1 and len(roh) > 2:
                art = "cta"
            else:
                art = "content"
        slides.append({"kind": art,
                       "eyebrow": s.get("eyebrow", ""),
                       "headline": s.get("headline", ""),
                       "subline": s.get("subline", ""),
                       "motiv": s.get("motiv", "")})

    return Briefing(slides=slides, keyword=keyword, thema=_sauber(text)[:200])
