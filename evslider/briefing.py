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


# --------------------------------------------------------------- Markdown
# Zweites Format: so sehen Slider aus, die anderswo ausgearbeitet wurden.
#   ## SLIDE 1 - Cover
#   Headline:
#   > Energieklasse G oder H -
#   > Schnaeppchen oder Kostenfalle?
#   Subline:
#   > Was Kaeufer jetzt wissen muessen
#   Visual Prompt:
#   *Close-up eines Energieausweises ...*

MD_SLIDE = re.compile(r"^#{1,6}\s*SLIDE\b(.*)$", re.IGNORECASE)
MD_FELD = re.compile(
    r"^\**\s*(headline|subline|text|visual\s*prompt|bild|motiv)\s*\**\s*:\s*(.*)$",
    re.IGNORECASE)
MD_FELDER = {"headline": "headline", "subline": "subline", "text": "text",
             "visualprompt": "motiv", "bild": "motiv", "motiv": "motiv"}


def _md_zeile(z: str) -> str:
    """Zitatpfeile, Sternchen und Aufzaehlungspunkte abraeumen."""
    z = re.sub(r"^\s*>+\s?", "", z)
    z = z.strip().strip("*").strip()
    return z


def ist_markdown(text: str) -> bool:
    return bool(MD_SLIDE.search(text) or
                re.search(r"^\**\s*(headline|visual\s*prompt)\s*\**\s*:",
                          text, re.IGNORECASE | re.MULTILINE))


def _md_lesen(text: str) -> list[dict]:
    bloecke, aktuell = [], None
    for roh in text.splitlines():
        kopf = MD_SLIDE.match(roh.strip())
        if kopf:
            if aktuell:
                bloecke.append(aktuell)
            aktuell = {"art": kopf.group(1).lower(), "felder": {}, "feld": None}
            continue
        if aktuell is None:
            continue
        if re.match(r"^\s*-{3,}\s*$", roh):        # Trennlinie beendet nichts
            continue
        m = MD_FELD.match(roh.strip())
        if m:
            name = re.sub(r"\s", "", m.group(1).lower())
            aktuell["feld"] = MD_FELDER.get(name)
            rest = _md_zeile(m.group(2))
            if aktuell["feld"] and rest:
                aktuell["felder"].setdefault(aktuell["feld"], []).append(rest)
            continue
        if aktuell["feld"]:
            zeile = _md_zeile(roh)
            if zeile:
                aktuell["felder"].setdefault(aktuell["feld"], []).append(zeile)
    if aktuell:
        bloecke.append(aktuell)

    slides = []
    for b in bloecke:
        f = b["felder"]
        art = ("cover" if "cover" in b["art"] else
               "cta" if "cta" in b["art"] else "")
        # Mehrzeilige Headline: die Umbrueche waren so gewollt.
        headline = "|".join(f.get("headline", []))
        # Jede Zeile unter "Text:" ist ein eigener Absatz. Erst saeubern,
        # dann mit || verbinden - sonst frisst _sauber die Trennung.
        absaetze = [_sauber(t) for t in f.get("text", []) if _sauber(t)]
        sub_direkt = _sauber(" ".join(f.get("subline", [])))

        if not art:
            # Ohne Headline ist es eine Textseite, keine Schlagzeile.
            art = "content" if headline else "text"

        if art in ("cover", "cta") and not headline and absaetze:
            # Cover und CTA brauchen eine Schlagzeile. Der erste Absatz ist
            # sie - vollstaendig, nicht mitten im Wort abgeschnitten.
            headline = absaetze[0]
            absaetze = absaetze[1:]

        subline = sub_direkt or "||".join(absaetze)
        if sub_direkt and absaetze:
            subline = "||".join([sub_direkt] + absaetze)

        slides.append({"kind": art, "eyebrow": "",
                       "headline": headline.strip(),
                       "subline": subline,
                       "motiv": _sauber(" ".join(f.get("motiv", [])))})
    return [s for s in slides if s["headline"] or s["subline"]]


def erzeuge(text: str, cfg, keyword: str = "") -> Briefing:
    """Telegram-Text -> Briefing. Kein Sprachmodell, keine Erfindung."""
    max_slides = int(cfg.get("slides.max_total", 10))

    # Kopfzeile "CTA: WORT" darf ueberall stehen und zaehlt nicht als Slide.
    m = re.search(r"^\s*CTA\s*:\s*([0-9A-Za-zÄÖÜäöüß_-]{2,30})\s*$",
                  text, re.MULTILINE)
    if m:
        keyword = keyword or m.group(1).strip()
        text = text[:m.start()] + text[m.end():]

    if ist_markdown(text):
        gelesen = _md_lesen(text)[:max_slides]
        if not gelesen:
            raise ValueError("Kein Slide erkannt.\n\n" + BEISPIEL)
        return Briefing(slides=gelesen, keyword=keyword,
                        thema=_sauber(text)[:200])

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
