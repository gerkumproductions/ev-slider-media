"""Icons für die Fakten-Seite.

Liegt unter assets/icons/<key>.png eine Datei, wird die verwendet (bevorzugt:
das offizielle E&V-Icon-Set). Sonst zeichnet dieses Modul eine schlichte
Linien-Grafik im gleichen Stil.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

STROKE = 4


def _canvas(size: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _flaeche(size: int, c) -> Image.Image:
    """L-förmiger Grundriss mit Schraffur."""
    img, d = _canvas(size)
    s = size
    pts = [(s * .12, s * .12), (s * .62, s * .12), (s * .62, s * .58),
           (s * .88, s * .58), (s * .88, s * .88), (s * .12, s * .88)]
    d.polygon(pts, outline=c, width=STROKE)
    # Schraffur
    for i in range(-int(s), int(s * 2), 22):
        d.line([(i, s * .12), (i + s, s * 1.12)], fill=c + (90,), width=2)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    out, _ = _canvas(s)
    out.paste(img, (0, 0), mask)
    d2 = ImageDraw.Draw(out)
    d2.polygon(pts, outline=c, width=STROKE)
    return out


def _bad(size: int, c) -> Image.Image:
    """WC + Waschbecken, stark abstrahiert."""
    img, d = _canvas(size)
    s = size
    # WC
    d.rounded_rectangle([s * .13, s * .34, s * .40, s * .62], radius=int(s * .09),
                        outline=c, width=STROKE)
    d.line([(s * .20, s * .62), (s * .20, s * .84)], fill=c, width=STROKE)
    d.line([(s * .33, s * .62), (s * .33, s * .84)], fill=c, width=STROKE)
    d.line([(s * .14, s * .84), (s * .39, s * .84)], fill=c, width=STROKE)
    d.rectangle([s * .16, s * .18, s * .37, s * .32], outline=c, width=STROKE)
    # Waschbecken
    d.arc([s * .55, s * .40, s * .90, s * .70], 0, 180, fill=c, width=STROKE)
    d.line([(s * .55, s * .55), (s * .90, s * .55)], fill=c, width=STROKE)
    d.line([(s * .725, s * .70), (s * .725, s * .86)], fill=c, width=STROKE)
    d.line([(s * .63, s * .86), (s * .82, s * .86)], fill=c, width=STROKE)
    d.line([(s * .78, s * .40), (s * .78, s * .24)], fill=c, width=STROKE)
    d.arc([s * .60, s * .16, s * .78, s * .32], 180, 320, fill=c, width=STROKE)
    return img


def _kalender(size: int, c) -> Image.Image:
    img, d = _canvas(size)
    s = size
    d.rounded_rectangle([s * .12, s * .22, s * .88, s * .88], radius=int(s * .05),
                        outline=c, width=STROKE)
    d.line([(s * .12, s * .40), (s * .88, s * .40)], fill=c, width=STROKE)
    for x in (.28, .50, .72):
        d.line([(s * x, s * .12), (s * x, s * .28)], fill=c, width=STROKE)
    for row in range(3):
        for col in range(4):
            cx = s * (.22 + col * .19)
            cy = s * (.50 + row * .14)
            d.rectangle([cx, cy, cx + s * .07, cy + s * .07], fill=c)
    return img


def _zimmer(size: int, c) -> Image.Image:
    """Grundriss mit zwei Räumen und Tür - klarer als eine 3D-Ansicht."""
    img, d = _canvas(size)
    s = size
    aussen = [s * .14, s * .20, s * .86, s * .80]
    d.rectangle(aussen, outline=c, width=STROKE)
    # Trennwand mit Türöffnung
    x = s * .50
    d.line([(x, s * .20), (x, s * .40)], fill=c, width=STROKE)
    d.line([(x, s * .60), (x, s * .80)], fill=c, width=STROKE)
    # Türblatt: kleiner Viertelkreis in der Öffnung
    r = s * .20
    d.arc([x, s * .40, x + 2 * r, s * .40 + 2 * r], 90, 180, fill=c, width=3)
    d.line([(x, s * .40), (x, s * .60)], fill=c, width=3)
    return img


def _euro(size: int, c) -> Image.Image:
    img, d = _canvas(size)
    s = size
    d.ellipse([s * .12, s * .12, s * .88, s * .88], outline=c, width=STROKE)
    d.arc([s * .32, s * .30, s * .74, s * .70], 40, 320, fill=c, width=STROKE)
    d.line([(s * .26, s * .44), (s * .60, s * .44)], fill=c, width=STROKE)
    d.line([(s * .26, s * .56), (s * .60, s * .56)], fill=c, width=STROKE)
    return img


def _haken(size: int, c) -> Image.Image:
    """Schlichter Haken - neutral und immer passend."""
    img, d = _canvas(size)
    s = size
    d.line([(s * .18, s * .52), (s * .40, s * .74)], fill=c, width=STROKE + 2)
    d.line([(s * .40, s * .74), (s * .84, s * .26)], fill=c, width=STROKE + 2)
    # Ecken abrunden
    for punkt in ((s * .18, s * .52), (s * .40, s * .74), (s * .84, s * .26)):
        r = (STROKE + 2) / 2
        d.ellipse([punkt[0] - r, punkt[1] - r, punkt[0] + r, punkt[1] + r], fill=c)
    return img


DRAWERS = {
    "haken": _haken,
    "wohnflaeche": _flaeche,
    "grundstueck": _flaeche,
    "badezimmer": _bad,
    "baujahr": _kalender,
    "zimmer": _zimmer,
    "kaufpreis": _euro,
}


def icon(key: str, size: int, color: tuple[int, int, int], cfg=None) -> Image.Image | None:
    if cfg is not None:
        p = cfg.path(f"assets/icons/{key}.png")
        if p.exists():
            im = Image.open(p).convert("RGBA")
            ratio = size / max(im.width, im.height)
            return im.resize((int(im.width * ratio), int(im.height * ratio)), Image.LANCZOS)
    fn = DRAWERS.get(key)
    return fn(size, color) if fn else None
