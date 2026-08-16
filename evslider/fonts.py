"""Schriften auflösen: Engel & Völkers Head (Serif) + Text (Sans)."""
from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

FALLBACKS = [
    "/usr/share/fonts/truetype/google-fonts/Poppins-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

_warned: set[str] = set()


class FontSet:
    """Head = Serif (Headlines, Zahlenwerte). Text = Sans (Labels, Bildtitel)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.head = self._file("fonts.head_regular", "EngelVoelkersHead_Rg.ttf")
        self.head_bd = self._file("fonts.head_bold", "EngelVoelkersHead_Bd.ttf")
        self.text = self._file("fonts.text_regular", "EngelVoelkersText_Rg.ttf")
        self.text_lt = self._file("fonts.text_light", "EngelVoelkersText_Lt.ttf")
        self.text_bd = self._file("fonts.text_bold", "EngelVoelkersText_Bd.ttf")

    def _file(self, key: str, default_name: str) -> str:
        rel = self.cfg.get(key) or f"assets/fonts/{default_name}"
        p = self.cfg.path(rel)
        if p.exists():
            return str(p)
        for fb in FALLBACKS:
            if Path(fb).exists():
                if default_name not in _warned:
                    print(f"[!] {default_name} fehlt - Ersatzschrift wird verwendet.")
                    _warned.add(default_name)
                return fb
        raise RuntimeError("Keine nutzbare Schriftdatei gefunden.")

    def H(self, size: int) -> ImageFont.FreeTypeFont:      # Headline-Serif
        return ImageFont.truetype(self.head, size)

    def Hb(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.head_bd, size)

    def T(self, size: int) -> ImageFont.FreeTypeFont:      # Sans
        return ImageFont.truetype(self.text, size)

    def Tl(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.text_lt, size)

    def Tb(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.text_bd, size)
