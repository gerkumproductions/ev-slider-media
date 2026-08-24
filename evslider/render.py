"""Slides rendern - Layout Heuser Immobilien (redaktionelle Info-Slider).

Getrennt von render.py, weil die Datenquelle eine andere ist: Der E&V-Renderer
baut aus einem gescrapten Expose, dieser hier aus einem Text-Briefing. Gemeinsam
genutzt werden nur die Hilfsfunktionen aus render.py - Renderer, title_slide,
facts_slide und photo_slide werden nicht angefasst.

Slide-Typen
  cover    Vollflaechiges Foto, weisse Typo (Eyebrow / Headline / Subline)
  content  Cremeflaeche, Eyebrow, Serif-Headline, Haarlinie, Subline, Foto unten
  cta      wie cover, anderer Text

Maße stammen aus einer Vermessung der Referenz-Slides und liegen relativ in
GEOM. Abweichungen pro Shop ueber slides.geom in der config.yaml.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .render import cover, fit_text, hexc, wrap

# ---------- Maße, relativ zur Canvas-Hoehe (bzw. -Breite bei *_width) ----------

GEOM = {
    "eyebrow_top": 0.129,
    "eyebrow_size": 0.0225,
    "eyebrow_track": 0.26,        # em
    "headline_top": 0.183,
    "headline_size": 0.077,
    "headline_leading": 1.03,
    "rule_gap_above": 0.053,
    "rule_gap_below": 0.053,
    "rule_width": 0.66,           # Anteil der Breite
    "rule_thickness": 2,
    "body_size": 0.0295,
    "body_leading": 1.42,
    "photo_top": 0.525,
    "photo_min_gap": 0.055,       # Mindestabstand Subline -> Fotokante
    "photo_fade": 0.16,           # Ausblendhoehe, Anteil der Fotohoehe
    "photo_inset_side": 0.11,     # Rand bei photo_inset
    "side_margin": 0.10,
    "cover_eyebrow_top": 0.150,
    "cover_headline_top": 0.183,
    "cover_headline_size": 0.105,
    "cover_sub_gap": 0.082,
    # Textseite: Fliesstext statt Schlagzeile
    "text_top": 0.115,
    "text_size": 0.0335,
    "text_leading": 1.55,
    "text_absatz": 0.55,          # Zusatzabstand zwischen Absaetzen, in Zeilen
    "cover_scrim": 82,            # Alpha des Schleiers oben, 0 = aus
    "cover_scrim_band": 0.48,
}

PALETTE = {
    "bg": "#F5F0E8",
    "accent": "#90806D",
    "headline": "#2B2721",
    "body": "#3D3933",
    "rule": "#A89A8A",
    "on_photo": "#FFFFFF",
}


def fade_mask(w: int, h: int, fade: float, inset: bool = False) -> Image.Image:
    """Alphamaske: Oberkante weich auslaufend, bei inset auch Seiten und Unterkante.

    Der Exponent 1.4 ist der Unterschied zwischen 'sichtbarer Verlauf' und
    'Foto schmilzt in die Flaeche' - linear sieht nach Verlauf aus.
    """
    mask = Image.new("L", (w, h), 255)
    md = ImageDraw.Draw(mask)
    n = max(int(h * fade), 1)
    for i in range(n):
        md.line([(0, i), (w, i)], fill=int(255 * (i / n) ** 1.4))
    if inset:
        side = max(int(w * 0.05), 1)
        edge = Image.new("L", (w, h), 255)
        ed = ImageDraw.Draw(edge)
        for i in range(side):
            a = int(255 * (i / side))
            ed.line([(i, 0), (i, h)], fill=a)
            ed.line([(w - 1 - i, 0), (w - 1 - i, h)], fill=a)
            ed.line([(0, h - 1 - i), (w, h - 1 - i)], fill=a)
        edge = edge.filter(ImageFilter.GaussianBlur(8))
        mask = ImageChops.darker(mask, edge)
    return mask


class HeuserRenderer:
    """Einstieg: HeuserRenderer(cfg).build(briefing) -> list[Image]"""

    layout = "heuser"

    def __init__(self, cfg):
        from .fonts import FontSet
        self.cfg = cfg
        self.f = FontSet(cfg)
        self.W = cfg.get("slides.width", 1080)
        self.H = cfg.get("slides.height", 1350)
        self.g = dict(GEOM)
        self.g.update(cfg.get("slides.geom", {}) or {})
        pal = dict(PALETTE)
        pal.update(cfg.get("slides.palette", {}) or {})
        self.c = {k: hexc(v) for k, v in pal.items()}
        self.M = int(self.W * self.g["side_margin"])

    # ---------- Slides ----------

    def cover_slide(self, s: dict) -> Image.Image:
        canvas = self._full_bleed(s.get("photo"))
        d = ImageDraw.Draw(canvas)
        g, col = self.g, self.c["on_photo"]
        inner = self.W - 2 * self.M

        if s.get("eyebrow"):
            f = self.f.T(int(self.H * g["eyebrow_size"]))
            self._tracked_centered(d, s["eyebrow"].upper(), f,
                                   self.H * g["cover_eyebrow_top"], col)

        y = self.H * g["cover_headline_top"]
        fh, lines = self._fit_headline(d, s["headline"].upper(),
                                       g["cover_headline_size"], inner, 2)
        for line in lines:
            self._centered(d, line, fh, y, col)
            y += fh.size * 0.98

        if s.get("subline"):
            f = self.f.T(int(self.H * g["eyebrow_size"]))
            self._tracked_centered(d, s["subline"].upper(), f,
                                   y + self.H * g["cover_sub_gap"], col)
        return canvas.convert("RGB")

    cta_slide = cover_slide

    def content_slide(self, s: dict) -> Image.Image:
        canvas = Image.new("RGB", (self.W, self.H), self.c["bg"])
        d = ImageDraw.Draw(canvas)
        g = self.g
        inner = self.W - 2 * self.M

        if s.get("eyebrow"):
            f = self.f.T(int(self.H * g["eyebrow_size"]))
            self._tracked_centered(d, s["eyebrow"].upper(), f,
                                   self.H * g["eyebrow_top"], self.c["accent"])

        y = self.H * g["headline_top"]
        fh, lines = self._fit_headline(d, s["headline"], g["headline_size"], inner, 3)
        lh = fh.size * g["headline_leading"]
        for line in lines:
            self._centered(d, line, fh, y, self.c["headline"])
            y += lh
        y -= lh - fh.size * 0.95

        y += self.H * g["rule_gap_above"]
        rw = self.W * g["rule_width"]
        d.rectangle([(self.W - rw) / 2, y,
                     (self.W + rw) / 2, y + g["rule_thickness"] - 1],
                    fill=self.c["rule"])
        y += self.H * g["rule_gap_below"]

        if s.get("subline"):
            fb = self.f.T(int(self.H * g["body_size"]))
            for line in wrap(d, s["subline"], fb, inner * 0.92):
                self._centered(d, line, fb, y, self.c["body"])
                y += fb.size * g["body_leading"]

        if s.get("photo"):
            top = int(max(self.H * g["photo_top"], y + self.H * g["photo_min_gap"]))
            self._place_photo(canvas, s["photo"], top, s.get("photo_inset", False))
        return canvas

    def text_slide(self, s: dict) -> Image.Image:
        """Seite mit Fliesstext: keine Schlagzeile, dafuer Platz zum Lesen.

        Fuer Briefings, die einen Gedanken ausformulieren statt ihn auf drei
        Woerter einzudampfen. Absaetze werden mit || getrennt.
        """
        canvas = Image.new("RGB", (self.W, self.H), self.c["bg"])
        d = ImageDraw.Draw(canvas)
        g = self.g
        inner = self.W - 2 * self.M

        y = self.H * g["text_top"]
        if s.get("eyebrow"):
            f = self.f.T(int(self.H * g["eyebrow_size"]))
            self._tracked_centered(d, s["eyebrow"].upper(), f, y, self.c["accent"])
            y += self.H * 0.045

        fb = self.f.T(int(self.H * g["text_size"]))
        lh = fb.size * g["text_leading"]
        absaetze = [a.strip() for a in s.get("subline", "").split("||") if a.strip()]
        for i, absatz in enumerate(absaetze):
            for zeile in wrap(d, absatz, fb, inner):
                self._centered(d, zeile, fb, y, self.c["body"])
                y += lh
            if i < len(absaetze) - 1:
                y += lh * g["text_absatz"]

        if s.get("photo"):
            top = int(y + self.H * g["photo_min_gap"])
            if self.H - top > self.H * 0.12:      # nur wenn noch Platz bleibt
                self._place_photo(canvas, s["photo"], top,
                                  s.get("photo_inset", False))
        return canvas

    # ---------- Bausteine ----------

    def _place_photo(self, canvas, photo, top: int, inset: bool):
        g = self.g
        if inset:
            pw = int(self.W * (1 - 2 * g["photo_inset_side"]))
            ph = self.H - top - int(self.H * 0.03)
        else:
            pw, ph = self.W, self.H - top
        if ph <= 0:
            return
        block = cover(self._as_image(photo), pw, ph)
        canvas.paste(block, ((self.W - pw) // 2, top),
                     fade_mask(pw, ph, g["photo_fade"], inset))

    def _full_bleed(self, photo) -> Image.Image:
        if not photo:
            return Image.new("RGBA", (self.W, self.H), self.c["bg"] + (255,))
        canvas = cover(self._as_image(photo), self.W, self.H).convert("RGBA")
        alpha = self.g["cover_scrim"]
        if alpha:
            band = int(self.H * self.g["cover_scrim_band"])
            scrim = Image.new("RGBA", (self.W, self.H), (58, 52, 43, 0))
            m = Image.new("L", (self.W, self.H), 0)
            md = ImageDraw.Draw(m)
            for i in range(band):
                md.line([(0, i), (self.W, i)], fill=int(alpha * (1 - i / band) ** 1.1))
            scrim.putalpha(m)
            canvas.alpha_composite(scrim)
        return canvas

    @staticmethod
    def _as_image(p) -> Image.Image:
        return p if isinstance(p, Image.Image) else Image.open(p).convert("RGB")

    def _fit_headline(self, d, text, rel_size, max_w, max_lines):
        """`|` erzwingt Umbruch, `~` markiert eine erlaubte Trennstelle."""
        if "|" in text:
            segs = [t.strip().replace("~", "") for t in text.split("|")]
            size = int(self.H * rel_size)
            while size > 30 and any(d.textlength(t, font=self.f.H(size)) > max_w
                                    for t in segs):
                size -= 2
            return self.f.H(size), segs
        text = self._soft_hyphen(d, text, max_w, int(self.H * rel_size))
        return fit_text(d, text, self.f.H, max_w, max_lines,
                        int(self.H * rel_size), 30)

    def _soft_hyphen(self, d, text, max_w, size) -> str:
        f = self.f.H(size)
        out = []
        for w in text.split():
            if "~" in w and d.textlength(w.replace("~", ""), font=f) > max_w * 0.55:
                head, tail = w.split("~", 1)
                out += [head + "-", tail]
            else:
                out.append(w.replace("~", ""))
        return " ".join(out)

    def _centered(self, d, text, font, y, fill):
        w = d.textlength(text, font=font)
        d.text(((self.W - w) / 2, y), text, font=font, fill=fill)

    def _tracked_centered(self, d, text, font, y, fill):
        """Gesperrter Text, zentriert. PIL kennt kein letter-spacing, also
        zeichenweise mit Vorschub - und so lange verkleinert, bis die Zeile
        in die Textspalte passt."""
        if not text:
            return
        max_w = self.W - 2 * self.M
        groesse = font.size
        while groesse > 12:
            f = self.f.T(groesse)
            sp = f.size * self.g["eyebrow_track"]
            widths = [d.textlength(c, font=f) for c in text]
            gesamt = sum(widths) + sp * (len(text) - 1)
            if gesamt <= max_w:
                break
            groesse -= 1
        x = (self.W - gesamt) / 2
        for c, w in zip(text, widths):
            d.text((x, y), c, font=f, fill=fill)
            x += w + sp

    # ---------- Slider zusammenbauen ----------

    def build(self, briefing: dict) -> list[Image.Image]:
        """briefing: {"slides": [{kind, eyebrow, headline, subline, photo}, ...]}"""
        max_total = self.cfg.get("slides.max_total", 10)
        out = []
        for s in briefing.get("slides", [])[:max_total]:
            kind = s.get("kind", "content")
            if kind == "cover":
                out.append(self.cover_slide(s))
            elif kind == "cta":
                out.append(self.cta_slide(s))
            elif kind == "text":
                out.append(self.text_slide(s))
            else:
                out.append(self.content_slide(s))
        if not out:
            raise RuntimeError("Briefing enthaelt keine Slides.")
        return out
