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
    """Erste vier Slides als Vorschau schicken."""
    files = {}
    media = []
    for i, p in enumerate(paths[:4]):
        key = f"f{i}"
        files[key] = (p.name, p.read_bytes(), "image/jpeg")
        media.append({"type": "photo", "media": f"attach://{key}"})
    if not media:
        return
    import json
    requests.post(API.format(token=_token(), method="sendMediaGroup"),
                  data={"chat_id": chat_id, "media": json.dumps(media)},
                  files=files, timeout=180)


def allowed(chat_id: int) -> bool:
    """Nur die eigene Chat-ID darf den Bot benutzen."""
    allow = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "")
    if not allow:
        return True
    return str(chat_id) in allow.split(",")


def process(chat_id: int, urls: list[str], cfg) -> None:
    renderer = Renderer(cfg)
    slots = publish_mod.plan_slots(cfg, len(urls))
    out_root = cfg.path(cfg.get("output_dir", "out"))

    send(chat_id, f"Alles klar – {len(urls)} Objekt(e). Ich lege los, "
                  f"das dauert ein bis zwei Minuten pro Objekt.")

    for i, url in enumerate(urls):
        try:
            ex = scrape(url, browser=cfg.get("scrape.browser", "auto"))
            if not ex.photos:
                send(chat_id, f"❌ Keine Bilder gefunden:\n{url}")
                continue

            texts = caption_mod.generate(ex, cfg)
            text = caption_mod.full_text(texts, cfg)
            slug = f"{slugify(ex.location or ex.title)}-{ex.ev_id or i}"
            paths = save_all(renderer.build(ex), out_root / slug, slug)

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
        if not allowed(chat_id):
            print(f"Nachricht von nicht freigegebener Chat-ID {chat_id} ignoriert.")
            continue
        if text.strip() in ("/start", "/hilfe", "/help"):
            send(chat_id, "Schick mir einfach einen oder mehrere Exposé-Links "
                          "von engelvoelkers.com. Ich baue den Slider und plane "
                          "den Post in Metricool ein.\n\n"
                          f"Deine Chat-ID: {chat_id}")
            continue
        found = URL_RE.findall(text)
        if found:
            jobs.setdefault(chat_id, []).extend(found)
        elif text.strip():
            send(chat_id, "Darin war kein Exposé-Link von engelvoelkers.com.")

    try:
        for chat_id, urls in jobs.items():
            seen, uniq = set(), []
            for u in urls:
                if u not in seen:
                    seen.add(u)
                    uniq.append(u)
            process(chat_id, uniq, cfg)
    finally:
        # Nachrichten als abgeholt bestätigen - auch im Fehlerfall, sonst
        # würde derselbe Link beim nächsten Lauf erneut verarbeitet.
        call("getUpdates", offset=last_id + 1, timeout=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
