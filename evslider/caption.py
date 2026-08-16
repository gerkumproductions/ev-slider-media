"""Bildtitel & Posttext per Claude API erzeugen.

Die alt-Texte auf engelvoelkers.com sind englisch und beschreibend
("Modern living room with a stone accent wall ..."). Für den Slider brauchen
wir kurze deutsche Bildtitel ("Wohnbereich mit Natursteinwand").
"""
from __future__ import annotations

import json
import re

import requests

API = "https://api.anthropic.com/v1/messages"

SYSTEM = """Du schreibst Social-Media-Content für einen Engel & Völkers Immobilienshop.
Tonalität: hochwertig, klar, zurückhaltend – kein Werbe-Superlativ, keine Emojis
in den Bildtiteln, keine erfundenen Fakten. Du antwortest ausschließlich mit
gültigem JSON, ohne Markdown-Backticks und ohne Vorrede."""

PROMPT = """Objektdaten:
{data}

Erzeuge:
1. "image_titles": eine Liste mit genau {n} kurzen deutschen Bildtiteln (max. 5 Wörter,
   z.B. "Küche mit Einbauküche", "Visualisierung Loggia", "Treppenaufgang"). Nutze die
   englischen alt-Texte als Vorlage, in derselben Reihenfolge. Ist ein Bild erkennbar
   eine Visualisierung oder ein Homestaging-Rendering, beginne den Titel mit
   "Visualisierung".
2. "keyword": EIN einzelnes, gut merkbares Stichwort in Grossbuchstaben, das zum Objekt
   passt (z.B. "LOGGIA", "WIEMELHAUSEN", "AUFZUG"). Keine Umlaute, keine Leerzeichen.
3. "hook": eine einzelne Zeile als Aufhaenger, max. 90 Zeichen, ohne Hashtags.
4. "body": 3-5 Saetze zum Objekt und zur Lage, als Fliesstext. Maximal 2 dezente Emojis.
   Keine Preisangabe erfinden - nur nutzen, wenn im Objektdatensatz vorhanden.
   KEIN Call-to-Action im body, der wird separat gesetzt.
5. "hashtags": 8-12 passende Hashtags auf Deutsch/Englisch inkl. Ort und Objektart.

Antworte als JSON:
{{"image_titles": [...], "keyword": "...", "hook": "...", "body": "...", "hashtags": [...]}}"""


def _call(api_key: str, model: str, prompt: str, max_tokens: int = 2000) -> str:
    r = requests.post(
        API,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def generate(ex, cfg) -> dict:
    """Ergänzt ex.photos[*].title und liefert {caption, hashtags}."""
    key = cfg.anthropic_key
    n = len(ex.photos)
    if not key:
        # Ohne API-Key: alt-Texte als Bildtitel, simpler Fallback-Text
        for p in ex.photos:
            p.title = p.alt[:60]
        return {"hook": ex.title, "body": ex.description,
                "keyword": _fallback_keyword(ex),
                "hashtags": cfg.get("caption.hashtags", [])}

    payload = {
        "titel": ex.title,
        "ort": ex.location,
        "preis": ex.price,
        "zimmer": ex.rooms,
        "wohnflaeche": ex.living_area,
        "baujahr": ex.year_built,
        "energieklasse": ex.energy_class,
        "beschreibung": ex.description[:2500],
        "lage": ex.location_text[:1500],
        "shop": cfg.get("brand.shop_name"),
        "bild_alt_texte": [p.alt for p in ex.photos],
    }
    prompt = PROMPT.format(
        data=json.dumps(payload, ensure_ascii=False, indent=2),
        n=n,
        max_chars=cfg.get("caption.max_chars", 1400),
    )
    raw = _call(key, cfg.get("caption.model", "claude-sonnet-5"), prompt)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        out = json.loads(m.group(0)) if m else {}

    titles = out.get("image_titles", [])
    for p, t in zip(ex.photos, titles):
        p.title = str(t).strip()
    for p in ex.photos:
        if not p.title:
            p.title = p.alt[:60]

    return {
        "hook": out.get("hook", ex.title).strip(),
        "body": out.get("body", "").strip(),
        "keyword": re.sub(r"[^A-Z0-9]", "", (out.get("keyword") or "").upper())
                   or _fallback_keyword(ex),
        "hashtags": out.get("hashtags") or cfg.get("caption.hashtags", []),
    }


def _fallback_keyword(ex) -> str:
    base = (ex.location.split(",")[0] if ex.location else ex.title).upper()
    base = (base.replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE")
                .replace("ß", "SS"))
    return re.sub(r"[^A-Z0-9]", "", base)[:14] or "EXPOSE"


def full_text(result: dict, cfg) -> str:
    """Aufbau: Hook / CTA / Fliesstext / CTA / Hashtags.

    Der Call-to-Action steht bewusst an zweiter und an letzter Stelle - oben faengt
    er die Leser ab, die nur die ersten zwei Zeilen sehen, unten die, die den Post
    zu Ende lesen.
    """
    kw = result.get("keyword", "EXPOSE")
    cta = cfg.get("caption.cta_template",
                  "Kommentiere \"{keyword}\" und du bekommst das komplette "
                  "Exposé per DM.").format(keyword=kw)
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in result.get("hashtags", []))
    blocks = [result.get("hook", "").strip(), cta,
              result.get("body", "").strip(), cta, tags]
    return "\n\n".join(b for b in blocks if b).strip()
