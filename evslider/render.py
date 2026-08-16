"""Slides rendern - Layout nach dem E&V-Beispiel-Slider.

Slide 1  Titelbild, roter Balken links, Headline (Serif), Ortszeile
Slide 2  Foto oben mit Bildtitel, darunter 2x2-Raster mit Icon/Label/Wert
Slide 3+ Vollflaechiges Foto mit Bildtitel unten links
"""
from __future__ import annotations

import io
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from . import icons as icon_mod
from .fonts import FontSet
from .scrape import Expose

UA = {"User-Agent": "Mozilla/5.0"}


# ---------- Hilfsfunktionen ----------

def hexc(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def download(url: str, timeout: int = 60) -> Image.Image:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    ratio = max(w / img.width, h / img.height)
    nw, nh = int(img.width * ratio + 1), int(img.height * ratio + 1)
    img = img.resize((nw, nh), Image.LANCZOS)
    return img.crop(((nw - w) // 2, (nh - h) // 3,
                     (nw - w) // 2 + w, (nh - h) // 3 + h))


def gradient(w: int, h: int, frac: float = 0.42, end: float = 0.62) -> Image.Image:
    """Dezenter Verlauf im unteren Bildteil - nur so viel, dass Text lesbar bleibt."""
    grad = Image.new("L", (1, h))
    start_y = int(h * (1 - frac))
    for y in range(h):
        if y < start_y:
            grad.putpixel((0, y), 0)
        else:
            t = (y - start_y) / max(h - start_y - 1, 1)
            grad.putpixel((0, y), int(255 * end * (t ** 1.5)))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.putalpha(grad.resize((w, h)))
    return layer


def wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def fit_text(draw, text, font_factory, max_w, max_lines, start, min_size):
    size = start
    while size > min_size:
        f = font_factory(size)
        lines = wrap(draw, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
        size -= 3
    f = font_factory(min_size)
    return f, wrap(draw, text, f, max_w)[:max_lines]


def tracked(draw, text, font, x, y, fill, spacing: float):
    """Text mit erhoehter Laufweite (Labels auf der Faktenseite)."""
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing
    return x


def tracked_width(draw, text, font, spacing: float) -> float:
    return sum(draw.textlength(c, font=font) + spacing for c in text) - spacing


def fact_key(label: str) -> str:
    """'Wohnfläche' -> 'wohnflaeche'. Umlaute VOR jeder Normalisierung ersetzen."""
    s = label.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return "".join(c for c in s if c.isalnum())


# ---------- Renderer ----------

class Renderer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.f = FontSet(cfg)
        self.W = cfg.get("slides.width", 1080)
        self.H = cfg.get("slides.height", 1350)
        self.red = hexc(cfg.get("brand.red", "#C8102E"))
        self.dark = hexc(cfg.get("brand.dark", "#1A1A1A"))
        self.light = hexc(cfg.get("brand.light", "#FFFFFF"))
        self.M = int(self.W * 0.072)          # Aussenrand ~78 px

    # -- Slide 1: Titel --
    def title_slide(self, ex: Expose, photo: Image.Image) -> Image.Image:
        canvas = cover(photo, self.W, self.H).convert("RGBA")
        canvas.alpha_composite(gradient(self.W, self.H, frac=0.46, end=0.58))
        d = ImageDraw.Draw(canvas)

        text_x = self.M + 34
        max_w = self.W - text_x - self.M
        fh, lines = fit_text(d, ex.title, self.f.H, max_w, 5, 62, 40)
        lh = int(fh.size * 1.20)

        loc = self.location_line(ex)
        f_loc = self.f.Tl(36)
        loc_h = int(f_loc.size * 1.4) if loc else 0

        block_h = lh * len(lines) + (loc_h + 18 if loc else 0)
        y0 = self.H - self.M - block_h

        # roter Balken links
        d.rectangle([self.M, y0 - 8, self.M + 6, y0 + block_h + 6], fill=self.red)

        y = y0
        for line in lines:
            d.text((text_x, y), line, font=fh, fill=self.light)
            y += lh
        if loc:
            d.text((text_x, y + 18), loc, font=f_loc, fill=self.light)
        return canvas.convert("RGB")

    @staticmethod
    def location_line(ex: Expose) -> str:
        """'Wiemelhausen, Bochum' -> 'Bochum Wiemelhausen'."""
        if not ex.location:
            return ""
        parts = [p.strip() for p in ex.location.split(",") if p.strip()]
        return " ".join(reversed(parts[:2])) if len(parts) >= 2 else parts[0]

    # -- Slide 2: Fakten --
    def facts_slide(self, ex: Expose, photo: Image.Image | None,
                    photo_caption: str = "") -> Image.Image:
        canvas = Image.new("RGB", (self.W, self.H), self.light)
        d = ImageDraw.Draw(canvas)
        y = self.M

        if photo is not None:
            pw = self.W - 2 * self.M
            ph = int(pw * 0.62)
            block = cover(photo, pw, ph).convert("RGBA")
            if photo_caption:
                block.alpha_composite(gradient(pw, ph, frac=0.34, end=0.55))
                bd = ImageDraw.Draw(block)
                fc, lines = fit_text(bd, photo_caption, self.f.T, pw - 60, 1, 32, 22)
                bd.text((30, ph - 30 - fc.size * 1.2), lines[0], font=fc, fill=self.light)
            canvas.paste(block.convert("RGB"), (self.M, y))
            y += ph

        facts = ex.facts(self.cfg.get("slides.facts"))[:4]
        if not facts:
            return canvas

        grid_top = y + 62
        cell_h = (self.H - grid_top - self.M) // 2
        col_w = self.W // 2
        f_label = self.f.T(28)
        icon_px = 92

        for i, (label, value) in enumerate(facts):
            cx = col_w * (i % 2) + col_w // 2
            cy = grid_top + cell_h * (i // 2)

            ic = icon_mod.icon(fact_key(label), icon_px, self.dark, self.cfg)
            if ic is not None:
                canvas.paste(ic, (cx - ic.width // 2, cy), ic)

            ly = cy + icon_px + 22
            lw = tracked_width(d, label, f_label, 2.5)
            tracked(d, label, f_label, cx - lw / 2, ly, self.dark, 2.5)

            fv, vlines = fit_text(d, value, self.f.H, col_w - 70, 1, 60, 32)
            vw = d.textlength(vlines[0], font=fv)
            d.text((cx - vw / 2, ly + 48), vlines[0], font=fv, fill=self.dark)
        return canvas

    # -- Slide 3+: Foto mit Bildtitel --
    def photo_slide(self, photo: Image.Image, caption: str) -> Image.Image:
        canvas = cover(photo, self.W, self.H).convert("RGBA")
        if caption:
            canvas.alpha_composite(gradient(self.W, self.H, frac=0.30, end=0.55))
            d = ImageDraw.Draw(canvas)
            f, lines = fit_text(d, caption, self.f.T, self.W - 2 * self.M, 2, 38, 26)
            lh = int(f.size * 1.3)
            y = self.H - self.M - lh * len(lines)
            for line in lines:
                d.text((self.M, y), line, font=f, fill=self.light)
                y += lh
        return canvas.convert("RGB")

    # -- Slider zusammenbauen --
    def build(self, ex: Expose, images: list[Image.Image] | None = None) -> list[Image.Image]:
        max_total = self.cfg.get("slides.max_total", 10)
        needed = max_total
        if images is None:
            images = [download(p.url) for p in ex.photos[:needed]]
        if not images:
            raise RuntimeError("Keine Bilder gefunden.")

        def cap(i: int) -> str:
            if i < len(ex.photos):
                return ex.photos[i].title or ex.photos[i].alt
            return ""

        slides = [self.title_slide(ex, images[0])]
        # Slide 2 nutzt das zweite Foto als Aufmacher
        second = images[1] if len(images) > 1 else images[0]
        slides.append(self.facts_slide(ex, second, cap(1) if len(images) > 1 else ""))

        for i, img in enumerate(images[2:], start=2):
            if len(slides) >= max_total:
                break
            slides.append(self.photo_slide(img, cap(i)))
        return slides


def save_all(slides: list[Image.Image], out_dir: Path, slug: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, s in enumerate(slides, 1):
        p = out_dir / f"{slug}_{i:02d}.jpg"
        s.save(p, "JPEG", quality=92, optimize=True)
        paths.append(p)
    return paths
