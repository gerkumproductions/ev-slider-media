"""Konfiguration laden (config.yaml + Umgebungsvariablen)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "brand": {
        # E&V Rot – bitte im Brand Manual gegenchecken und hier fixieren.
        "red": "#C8102E",
        "dark": "#1A1A1A",
        "light": "#FFFFFF",
        "muted": "#8C8C8C",
        "shop_name": "Engel & Völkers Bochum",
        "handle": "@engelvoelkers.bochum",
    },
    "fonts": {
        "head_regular": "assets/fonts/EngelVoelkersHead_Rg.ttf",
        "head_bold": "assets/fonts/EngelVoelkersHead_Bd.ttf",
        "text_regular": "assets/fonts/EngelVoelkersText_Rg.ttf",
        "text_light": "assets/fonts/EngelVoelkersText_Lt.ttf",
        "text_bold": "assets/fonts/EngelVoelkersText_Bd.ttf",
    },
    "slides": {
        "width": 1080,
        "height": 1350,          # 4:5 – bester Feed-Platz auf Instagram
        "max_total": 10,         # Instagram-Limit für Karussells
        "facts": ["Wohnfläche", "Badezimmer", "Baujahr", "Zimmer"],
    },
    "caption": {
        "model": "claude-sonnet-5",
        "hashtags": ["#engelvoelkers", "#immobilien", "#zuhause"],
        "max_chars": 1400,
        "cta_template": 'Kommentiere "{keyword}" und du bekommst das komplette Exposé per DM.',
    },
    "schedule": {
        "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"],
        "times": ["09:30", "11:30"],   # morgens; abends laufen die Reels
        "timezone": "Europe/Berlin",
        "min_lead_hours": 12,
    },
    "metricool": {
        "base_url": "https://app.metricool.com/api",
        "auto_publish": True,
        "networks": ["instagram"],
        "normalize_media": True,
    },
    "hosting": {
        "mode": "github",        # github | s3 | manual
        "github_owner": "",
        "github_repo": "",
        "github_branch": "main",
        "bucket": "",
        "endpoint_url": "",      # z.B. Cloudflare R2 Endpoint
        "public_base_url": "",   # z.B. https://cdn.deine-domain.de
        "prefix": "ev-slider/",
    },
    "scrape": {"browser": "auto"},
    "output_dir": "out",
}


def load_env(path: str | Path = ".env") -> None:
    """Zugangsdaten aus der .env-Datei in die Umgebung laden.

    Wird von setup.command angelegt, damit niemand Schlüssel ins Terminal
    tippen muss.
    """
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: DEFAULTS)
    root: Path = field(default_factory=Path.cwd)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, path: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    # --- Secrets kommen aus der Umgebung, nie aus der YAML ---
    @property
    def anthropic_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")

    @property
    def metricool_token(self) -> str | None:
        return os.environ.get("METRICOOL_USER_TOKEN")

    @property
    def metricool_user_id(self) -> str | None:
        return os.environ.get("METRICOOL_USER_ID")

    @property
    def metricool_blog_id(self) -> str | None:
        return os.environ.get("METRICOOL_BLOG_ID")


def load(path: str | Path = "config.yaml") -> Config:
    load_env()
    p = Path(path)
    data = DEFAULTS
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            data = _merge(DEFAULTS, yaml.safe_load(fh) or {})
    return Config(data=data, root=p.parent.resolve() if p.exists() else Path.cwd())
