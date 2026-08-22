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


# Umlaute im CTA-Stichwort bleiben erhalten: Im Post steht ja, was zu
# kommentieren ist, also tippen die Leute genau das. Wer lieber ASCII will
# (etwa weil ein Kommentar-Werkzeug daran scheitert), setzt in der
# config.yaml caption.keyword_umlaute auf false.
UMLAUTE_ERHALTEN = True


def _clean_keyword(text: str) -> str:
    text = text.strip().strip('"\'„“”‚‘’').strip()
    text = re.sub(r"[^0-9A-Za-zÄÖÜäöüß _-]", "", text).strip()
    if not text or len(text) > 30:
        return ""
    # .upper() macht aus ß von sich aus SS - das ist korrektes Deutsch.
    text = text.upper().replace(" ", "")
    if not UMLAUTE_ERHALTEN:
        text = (text.replace("Ä", "AE").replace("Ö", "OE")
                    .replace("Ü", "UE"))
    return text


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


def shop_hinweis(cfg) -> str:
    """Kurzliste 'rheinland (rhein, rl)' für Fehlermeldungen und Hilfe."""
    zeilen = []
    for key, aliase in shop_alias_map(cfg).items():
        kurz = ", ".join(aliase) if aliase else ""
        name = cfg.for_shop(key).get("brand.shop_name", key)
        zeilen.append(f"· {key}{f' ({kurz})' if kurz else ''} → {name}")
    return "\n".join(zeilen)


def allowed(chat_id: int, cfg) -> bool:
    """Zugriff: entweder über die Shop-Zuordnung oder über die Freigabeliste."""
    if cfg.shop_for_chat(chat_id):
        return True
    allow = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "")
    return bool(allow) and str(chat_id) in allow.split(",")


def blog_id_pruefen(cfg, chat_id: int) -> bool:
    """Landet der Post wirklich auf dem Profil dieses Shops?

    Der gefaehrlichste Fehler beim Mehrshop-Betrieb ist eine blogId, die
    nicht zum Shop gehoert - dann steht ein Niederrhein-Objekt auf dem
    Rheinland-Profil, und man merkt es erst, wenn der Post draussen ist.
    Deshalb wird hier verglichen, was in der config.yaml fuer DIESEN Shop
    steht und was tatsaechlich benutzt wird. Weichen beide ab, ist meist
    das Secret METRICOOL_BLOG_ID im Weg.
    """
    name = cfg.get("brand.shop_name", "diesen Shop")
    konfiguriert = str(cfg.get("metricool.blog_id", "") or "").strip()
    wirksam = str(getattr(cfg, "metricool_blog_id", "") or "").strip()
    mehrere = len(cfg.shop_keys()) > 1

    print(f"[i] Shop: {name} | blogId laut config.yaml: "
          f"{konfiguriert or '(leer)'} | tatsächlich verwendet: "
          f"{wirksam or '(leer)'}")

    if konfiguriert and wirksam and konfiguriert != wirksam:
        send(chat_id,
             f"❌ Abbruch, sonst landet der Post auf dem falschen Profil.\n\n"
             f"Für {name} steht in der config.yaml die blogId {konfiguriert}, "
             f"benutzt würde aber {wirksam}. Vermutlich überschreibt das "
             f"GitHub-Secret METRICOOL_BLOG_ID die Shop-Einstellung. "
             f"Das Secret löschen, die blogIds gehören in die config.yaml.")
        return False

    if mehrere and not konfiguriert:
        send(chat_id,
             f"❌ Für {name} ist in der config.yaml keine blogId hinterlegt.\n\n"
             f"Bei mehreren Shops muss jede blogId dort stehen, sonst kann "
             f"ich nicht sicher sagen, auf welchem Profil der Post landet. "
             f"Die Nummer steht in der Metricool-Adresszeile hinter blogId=.")
        return False

    return True


def process(chat_id: int, jobs: list[tuple[str, str]], cfg) -> None:
    if not blog_id_pruefen(cfg, chat_id):
        return

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
                 f"{cfg.get('brand.shop_name', '')} · {len(paths)} Slides · "
                 f"eingeplant für {slots[i]:%a %d.%m. %H:%M} Uhr{fehlt}\n\n{text}")
            send_slides(chat_id, paths)

            # Hat die Galerie geklemmt, den Screenshot mitschicken - daran
            # laesst sich der Seitenaufbau erkennen.
            if getattr(ex, "diagnose_bild", ""):
                from pathlib import Path as _P
                d = _P(ex.diagnose_bild)
                if d.exists():
                    send(chat_id, "Die Galerie war unvollständig. Screenshot "
                                  "zur Fehlersuche:")
                    send_slides(chat_id, [d])
        except Exception as exc:                                # noqa: BLE001
            traceback.print_exc()
            send(chat_id, f"❌ Fehler bei {url}\n{str(exc)[:400]}")


def main() -> int:
    global UMLAUTE_ERHALTEN
    cfg = config_mod.load(os.environ.get("EVSLIDER_CONFIG", "config.yaml"))
    UMLAUTE_ERHALTEN = bool(cfg.get("caption.keyword_umlaute", True))
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
            mehrere = len(cfg.shop_keys()) > 1
            if shop:
                zuordnung = (f"Dieser Chat gehört fest zu "
                             f"{cfg.for_shop(shop).get('brand.shop_name')} – "
                             f"du brauchst kein Kürzel.")
            elif mehrere:
                zuordnung = ("Das Shop-Kürzel ist Pflicht. Ohne Kürzel baue "
                             "ich nichts und frage nach.")
            else:
                zuordnung = (f"Ohne Angabe: "
                             f"{cfg.get('brand.shop_name', '?')}")
            send(chat_id,
                 "Schick mir einen oder mehrere Exposé-Links von "
                 "engelvoelkers.com.\n\n"
                 "Shop-Kürzel davor, Stichwort für den Call-to-Action "
                 "dahinter – Reihenfolge egal:\n"
                 "rl <link> SUEDGARTEN\n"
                 "nr <link> UFERWEG\n\n"
                 "Ein Kürzel allein in der ersten Zeile gilt für die ganze "
                 "Nachricht:\n"
                 "nr\n<link> UFERWEG\n<link> ALTSTADT\n\n"
                 f"Verfügbare Shops:\n{shop_hinweis(cfg)}\n\n"
                 f"{zuordnung}\nChat-ID: {chat_id}")
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
            ohne_shop: list[str] = []
            seen = set()
            chat_shop = cfg.shop_for_chat(chat_id)
            mehrere = len(cfg.shop_keys()) > 1
            pflicht = bool(cfg.get("shop_pflicht", True))

            for url, kw, shop in entries:
                if url in seen:
                    continue
                seen.add(url)
                key = shop or chat_shop
                # Bei mehreren Shops NICHT stillschweigend auf default_shop
                # ausweichen - sonst landet ein Objekt auf dem falschen
                # Profil und niemand merkt es.
                if not key and mehrere and pflicht:
                    ohne_shop.append(url)
                    continue
                key = key or cfg.get("default_shop")
                nach_shop.setdefault(key, []).append((url, kw))

            if ohne_shop:
                liste = "\n".join(u[:80] for u in ohne_shop[:5])
                send(chat_id,
                     f"❓ Für welchen Shop?\n\n"
                     f"Bei {len(ohne_shop)} Link(s) fehlt das Kürzel. Schick "
                     f"sie noch einmal mit Kürzel davor:\n\n"
                     f"{shop_hinweis(cfg)}\n\nBetroffen:\n{liste}")

            for shop_key, liste in nach_shop.items():
                process(chat_id, liste, cfg.for_shop(shop_key))
    finally:
        # Nachrichten als abgeholt bestätigen - auch im Fehlerfall, sonst
        # würde derselbe Link beim nächsten Lauf erneut verarbeitet.
        call("getUpdates", offset=last_id + 1, timeout=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
