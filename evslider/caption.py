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

SYSTEM = """Du schreibst Social-Media-Content für Engel & Völkers im Rheinland.

Perspektive: Du schreibst ALS dieser Shop, in der Wir-Form ("wir haben", "unser
Team", "sprechen Sie uns an"), und sprichst die Leser:innen mit Sie an. Der
Shop vermarktet die Immobilie selbst - schreibe nie über Engel & Völkers in der
dritten Person und nie so, als würdest du ein fremdes Angebot weiterempfehlen.

Tonalität: hochwertig, klar, zurückhaltend - kein Werbe-Superlativ, keine Emojis
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
2. "ausschliessen": eine Liste der Positionen (0-basiert) aus bild_alt_texte, die
   KEIN reines Objektfoto sind. Ausschliessen: alles, worauf Menschen zu sehen
   sind (Paare, Familien, Makler, Interessenten, Beratungsszenen), ausserdem
   Logos, Grafiken, Karten und Stimmungsbilder ohne Objektbezug.
   Ein Objektfoto zeigt Raeume, Gebaeude, Garten oder Aussicht.
   Schliesse NUR aus, wenn Personen eindeutig das Motiv sind (Werbe- oder
   Stockbilder) oder das Bild klar nichts mit der Immobilie zu tun hat.
   Leere Raeume, Baustellen, unscharfe oder schlichte Aufnahmen bleiben drin.
   Im Zweifel BEHALTEN.
3. "keyword": EIN einzelnes, gut merkbares Stichwort in Grossbuchstaben, das zum Objekt
   passt (z.B. "LOGGIA", "WIEMELHAUSEN", "AUFZUG"). Keine Umlaute, keine Leerzeichen.
   Steht im Datensatz "vorgegebenes_cta_stichwort", nutze genau dieses.
4. "hook": eine einzelne Zeile als Aufhaenger, max. 90 Zeichen, ohne Hashtags.
5. "body": 3-5 Saetze zum Objekt und zur Lage, als Fliesstext. Maximal 2 dezente Emojis.
   Keine Preisangabe erfinden - nur nutzen, wenn im Objektdatensatz vorhanden.
   KEIN Call-to-Action im body, der wird separat gesetzt.
6. "hashtags": 8-12 passende Hashtags auf Deutsch/Englisch inkl. Ort und Objektart.

Antworte als JSON:
{{"image_titles": [...], "ausschliessen": [...], "keyword": "...", "hook": "...",
 "body": "...", "hashtags": [...]}}"""


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


def generate(ex, cfg, keyword: str | None = None) -> dict:
    """Ergänzt ex.photos[*].title und liefert Bausteine für den Posttext.

    keyword: vom Nutzer vorgegebenes CTA-Stichwort. Ist es gesetzt, hat es
    Vorrang vor dem, was die KI vorschlagen würde.
    """
    key = cfg.anthropic_key
    n = len(ex.photos)
    if not key:
        # Ohne API-Key: alt-Texte als Bildtitel, simpler Fallback-Text
        for p in ex.photos:
            if not p.caption:
                p.title = p.alt[:60]
        return {"hook": ex.title, "body": ex.description,
                "keyword": keyword or _fallback_keyword(ex),
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
        "bildunterschriften_der_website": [p.caption for p in ex.photos],
    }
    if keyword:
        payload["vorgegebenes_cta_stichwort"] = keyword
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
        # Die Unterschrift von der Website hat immer Vorrang - der KI-Titel
        # ist nur die Notloesung, wenn die Seite nichts hergibt.
        if not p.caption:
            p.title = str(t).strip()
    for p in ex.photos:
        if not (p.caption or p.title):
            p.title = p.alt[:60]

    ex.photos = _ohne_werbung(ex.photos, out.get("ausschliessen", []))

    return {
        "hook": out.get("hook", ex.title).strip(),
        "body": out.get("body", "").strip(),
        "keyword": keyword or re.sub(r"[^A-Z0-9]", "",
                                     (out.get("keyword") or "").upper())
                   or _fallback_keyword(ex),
        "hashtags": out.get("hashtags") or cfg.get("caption.hashtags", []),
    }


# Bilder, die nie in den Slider gehoeren
# E&V blendet in die Galerien Stock- und Werbebilder ein, auf denen Menschen
# das Motiv sind (Paare, Makler, Beratungsszenen). Die gehoeren nie in den
# Slider. Objektfotos zeigen Raeume - dort kommen diese Woerter nicht vor.
# Nur eindeutige Begriffe. Zu allgemeine Woerter ("person", "people", "logo")
# treffen sonst harmlose Raumbeschreibungen und leeren den Slider.
WERBUNG = (
    # Personen eindeutig als Motiv
    "homebuyer", "home buyer", "house hunter", "realtor", "real estate agent",
    "estate agent", "advisor", "consultant", "couple", "family",
    "man and woman", "woman and man", "handshake", "shaking hands",
    "talking to a", "smiling at",
    # Beratung und Werbung
    "finanzberatung", "beratungsgespräch", "consultation",
    "shop image", "wavy pattern", "engel & völkers logo",
)


def _is_werbung(alt: str) -> bool:
    a = (alt or "").lower()
    return any(w in a for w in WERBUNG)


def _ohne_werbung(photos: list, ki_liste) -> list:
    """Werbebilder entfernen - aber mit Notbremse.

    Sortiert die KI zu viel aus, ist der Slider halb leer. Deshalb: Wenn mehr
    als ein Drittel wegfallen wuerde, gilt nur noch die Stichwortpruefung,
    denn die belegt den Ausschluss nachweisbar am alt-Text.
    """
    ki = {int(i) for i in ki_liste if str(i).isdigit()}
    per_stichwort = {i for i, p in enumerate(photos) if _is_werbung(p.alt)}

    raus = ki | per_stichwort
    if len(raus) > max(1, len(photos) // 3):
        print(f"[i] KI wollte {len(ki)} Bild(er) aussortieren - zu viele. "
              f"Es gilt nur die Stichwortprüfung.")
        raus = per_stichwort

    keep = [p for i, p in enumerate(photos) if i not in raus]
    if len(keep) < 2:               # nie den ganzen Slider leeren
        return photos
    if len(keep) < len(photos):
        print(f"[i] {len(photos) - len(keep)} Bild(er) als Werbung aussortiert, "
              f"{len(keep)} bleiben.")
    return keep


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
                  "Kommentieren Sie \"{keyword}\" und Sie erhalten das komplette "
                  "Exposé per DM.").format(keyword=kw)
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in result.get("hashtags", []))
    blocks = [result.get("hook", "").strip(), cta,
              result.get("body", "").strip(), cta, tags]
    return "\n\n".join(b for b in blocks if b).strip()
