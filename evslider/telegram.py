"""Telegram-Eingang: Link schicken, Slider kommt zurück.

Läuft als GitHub Action alle paar Minuten. Kein Server nötig.

Zustand: Telegram merkt sich selbst, welche Nachrichten schon abgeholt wurden -
ein getUpdates mit offset=<letzte_id + 1> bestätigt alle vorherigen. Deshalb
braucht dieses Skript keine Datenbank.
"""
from __future__ import annotations

import os
import re
import sys
import traceback
from pathlib import Path

import requests

from . import caption as caption_mod
from . import config as config_mod
from . import publish as publish_mod
from .cli import slugify
from .render import Renderer, save_all
from .scrape import scrape

API = "https://api.telegram.org/bot{token}/{method}"
URL_RE = re.compile(r"https?://[^\s]+engelvoelkers\.com[^\s]*")


def parse_message(text: str, shops: dict[str, list[str]] | None = None
                  ) -> list[tuple[str, str, str]]:
    """Links, CTA-Stichwort und Shop aus einer Nachricht lesen.

    Alles darf in einer Zeile stehen, in beliebiger Reihenfolge:
      <link> SUEDGARTEN                 -> Standard-Shop
      <link> NIEDERRHEIN SUEDGARTEN     -> Shop + Stichwort
      NIEDERRHEIN\n<link> A\n<link> B    -> Shop gilt für die ganze Nachricht

    Ein Wort, das zu einem Shop passt, wird als Shop erkannt. Alles andere
    neben dem Link ist das CTA-Stichwort.

    Rückgabe: [(url, stichwort, shop_oder_leer)]
    """
    shops = shops or {}
    triples: list[list] = []
    lose_kw: list[str] = []
    lose_shop: list[str] = []

    for line in text.splitlines():
        urls = URL_RE.findall(line)
        rest = URL_RE.sub(" ", line)
        shop, keyword = _split_shop_and_keyword(rest, shops)
        if urls:
            for u in urls:
                triples.append([u, keyword, shop])
        else:
            if shop:
                lose_shop.append(shop)
            if keyword:
                lose_kw.append(keyword)

    kw_fallback = lose_kw[0] if lose_kw else ""
    shop_fallback = lose_shop[0] if lose_shop else ""

    out, seen = [], set()
    for url, kw, shop in triples:
        if url in seen:
            continue
        seen.add(url)
        out.append((url, kw or kw_fallback, shop or shop_fallback))
    return out


def _split_shop_and_keyword(text: str, shops: dict[str, list[str]]) -> tuple[str, str]:
    """Trennt ein Shop-Wort vom CTA-Stichwort."""
    woerter = [w for w in re.split(r"[\s,;]+", text) if w.strip()]
    shop, rest = "", []
    for w in woerter:
        treffer = _match_shop(w, shops) if not shop else ""
        if treffer:
            shop = treffer
        else:
            rest.append(w)
    return shop, _clean_keyword(" ".join(rest))


def _match_shop(wort: str, shops: dict[str, list[str]]) -> str:
    w = re.sub(r"[^0-9a-zäöüß]", "", wort.lower())
    if not w:
        return ""
    for key, aliase in shops.items():
        kandidaten = [key.lower()] + [a.lower() for a in (aliase or [])]
        if w in [re.sub(r"[^0-9a-zäöüß]", "", k) for k in kandidaten]:
            return key
    return ""


def _clean_keyword(text: str) -> str:
    text = text.strip().strip('"\'„“”‚‘’').strip()
    text = re.sub(r"[^0-9A-Za-zÄÖÜäöüß _-]", "", text).strip()
    if not text or len(text) > 30:
        return ""
    return (text.upper().replace("Ä", "AE").replace("Ö", "OE")
                .replace("Ü", "UE").replace("ß", "SS").replace(" ", ""))


def _token() -> str:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        raise RuntimeError("TELEGRAM_BOT_TOKEN fehlt.")
    return tok


def call(method: str, **params):
    r = requests.post(API.format(token=_token(), method=method), json=params, timeout=90)
    if r.status_code >= 400:
        print(f"[!] Telegram {method}: {r.status_code} {r.text[:200]}")
        return None
    return r.json().get("result")


def send(chat_id: int, text: str) -> None:
    call("sendMessage", chat_id=chat_id, text=text[:4000],
         disable_web_page_preview=True)


def send_slides(chat_id: int, paths: list[Path]) -> None:
    """Alle Slides schicken - Telegram nimmt max. 10 pro Sendung."""
    import json

    for start in range(0, len(paths), 10):
        chunk = paths[start:start + 10]
        files, media = {}, []
        for i, p in enumerate(chunk):
            key = f"f{i}"
            files[key] = (p.name, p.read_bytes(), "image/jpeg")
            media.append({"type": "photo", "media": f"attach://{key}"})
        if not media:
            continue
        requests.post(API.format(token=_token(), method="sendMediaGroup"),
                      data={"chat_id": chat_id, "media": json.dumps(media)},
                      files=files, timeout=180)


def shop_alias_map(cfg) -> dict[str, list[str]]:
    """{shop_key: [alias, ...]} aus der Konfiguration."""
    return {k: (v.get("aliases") or [])
            for k, v in (cfg.get("shops") or {}).items()}


def allowed(chat_id: int, cfg) -> bool:
    """Zugriff: entweder über die Shop-Zuordnung oder über die Freigabeliste."""
    if cfg.shop_for_chat(chat_id):
        return True
    allow = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "")
    return bool(allow) and str(chat_id) in allow.split(",")


def process(chat_id: int, jobs: list[tuple[str, str]], cfg) -> None:
    renderer = Renderer(cfg)
    slots = publish_mod.plan_slots(cfg, len(jobs))
    out_root = cfg.path(cfg.get("output_dir", "out"))

    send(chat_id, f"Alles klar – {len(jobs)} Objekt(e) für "
                  f"{cfg.get('brand.shop_name', 'den Shop')}. Ich lege los, "
                  f"das dauert ein bis zwei Minuten pro Objekt.")

    for i, (url, keyword) in enumerate(jobs):
        try:
            ex = scrape(url, browser=cfg.get("scrape.browser", "auto"))
            if not ex.photos:
                send(chat_id, f"❌ Keine Bilder gefunden:\n{url}")
                continue

            texts = caption_mod.generate(ex, cfg, keyword=keyword or None)
            text = caption_mod.full_text(texts, cfg)
            # Ordner je Shop, damit sich zwei Standorte nicht überschreiben.
            # Wichtig: Der Shop gehört in den ORDNERPFAD, nicht in den Dateinamen.
            shop_slug = slugify(cfg.get("brand.handle", "shop").lstrip("@"))
            slug = f"{slugify(ex.location or ex.title)}-{ex.ev_id or i}"
            ziel = out_root / shop_slug / slug
            paths = save_all(renderer.build(ex), ziel, slug)

            image_urls = publish_mod.upload_images(paths, cfg)
            publish_mod.schedule_post(cfg, text, image_urls, slots[i])

            fehlt = ""
            if ex.expected_images and len(ex.photos) < ex.expected_images:
                fehlt = (f"\n⚠️ Nur {len(ex.photos)} von {ex.expected_images} "
                         f"Bildern gefunden.")
            send(chat_id,
                 f"✅ {ex.title[:90]}\n"
                 f"{len(paths)} Slides · eingeplant für "
                 f"{slots[i]:%a %d.%m. %H:%M} Uhr{fehlt}\n\n{text}")
            send_slides(chat_id, paths)
        except Exception as exc:                                # noqa: BLE001
            traceback.print_exc()
            send(chat_id, f"❌ Fehler bei {url}\n{str(exc)[:400]}")


def main() -> int:
    cfg = config_mod.load(os.environ.get("EVSLIDER_CONFIG", "config.yaml"))
    updates = call("getUpdates", timeout=0) or []
    if not updates:
        print("Keine neuen Nachrichten.")
        return 0

    last_id = max(u["update_id"] for u in updates)
    jobs: dict[int, list[str]] = {}

    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = msg.get("text") or msg.get("caption") or ""
        if not chat_id:
            continue
        if not allowed(chat_id, cfg):
            print(f"Nachricht von nicht freigegebener Chat-ID {chat_id} ignoriert.")
            continue
        if text.strip() in ("/start", "/hilfe", "/help"):
            shop = cfg.shop_for_chat(chat_id)
            name = cfg.for_shop(shop).get("brand.shop_name", "?")
            liste = "\n".join(
                f"· {k} → {cfg.for_shop(k).get('brand.shop_name')}"
                for k in cfg.shop_keys())
            send(chat_id,
                 "Schick mir einen oder mehrere Exposé-Links von "
                 "engelvoelkers.com.\n\n"
                 "Stichwort für den Call-to-Action einfach dahinter schreiben:\n"
                 "<link> SUEDGARTEN\n\n"
                 "Anderer Shop? Namen dazuschreiben, Reihenfolge egal:\n"
                 "<link> NIEDERRHEIN SUEDGARTEN\n\n"
                 f"Verfügbare Shops:\n{liste}\n\n"
                 f"Ohne Angabe: {name}\nChat-ID: {chat_id}")
            continue
        found = parse_message(text, shop_alias_map(cfg))
        if found:
            jobs.setdefault(chat_id, []).extend(found)
        elif text.strip():
            send(chat_id, "Darin war kein Exposé-Link von engelvoelkers.com.")

    try:
        for chat_id, entries in jobs.items():
            # nach Shop trennen, damit jeder Shop eigene Termine bekommt
            nach_shop: dict[str, list[tuple[str, str]]] = {}
            seen = set()
            for url, kw, shop in entries:
                if url in seen:
                    continue
                seen.add(url)
                key = shop or cfg.shop_for_chat(chat_id) or cfg.get("default_shop")
                nach_shop.setdefault(key, []).append((url, kw))
            for shop_key, liste in nach_shop.items():
                process(chat_id, liste, cfg.for_shop(shop_key))
    finally:
        # Nachrichten als abgeholt bestätigen - auch im Fehlerfall, sonst
        # würde derselbe Link beim nächsten Lauf erneut verarbeitet.
        call("getUpdates", offset=last_id + 1, timeout=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
