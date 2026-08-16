"""CLI:  evslider run <expose-url> [<expose-url> ...]"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import caption as caption_mod
from . import config as config_mod
from . import publish as publish_mod
from .render import Renderer, save_all
from .scrape import scrape


def slugify(text: str, maxlen: int = 50) -> str:
    t = (text.lower()
         .replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:maxlen].rstrip("-") or "objekt"


def cmd_probe(args, cfg):
    ex = scrape(args.url, browser=args.browser)
    print(f"# Bilder: {len(ex.photos)} gefunden, "
          f"{ex.expected_images or '?'} laut Zähler | Quelle: {ex.source}\n",
          file=sys.stderr)
    print(json.dumps(ex.to_dict(), ensure_ascii=False, indent=2))


def cmd_run(args, cfg):
    out_root = cfg.path(cfg.get("output_dir", "out"))
    renderer = Renderer(cfg)
    results = []
    slots = publish_mod.plan_slots(cfg, len(args.urls))
    if not args.no_schedule:
        print("Geplante Termine: " + ", ".join(s.strftime("%a %d.%m. %H:%M") for s in slots))

    for i, url in enumerate(args.urls):
        print(f"\n▸ {url}")
        ex = scrape(url, browser=args.browser)
        exp = f"/{ex.expected_images}" if ex.expected_images else ""
        print(f"  {ex.title[:70]}… | {ex.price} | {len(ex.photos)}{exp} Bilder ({ex.source})")
        if not ex.photos:
            print("  [!] Keine Bilder gefunden – bitte 'evslider probe' prüfen.")
            continue

        text_parts = caption_mod.generate(ex, cfg)
        text = caption_mod.full_text(text_parts, cfg)

        slides = renderer.build(ex)
        slug = f"{slugify(ex.location or ex.title)}-{ex.ev_id or i}"
        paths = save_all(slides, out_root / slug, slug)
        print(f"  {len(paths)} Slides → {out_root / slug}")

        (out_root / slug / "caption.txt").write_text(text, encoding="utf-8")
        (out_root / slug / "expose.json").write_text(
            json.dumps(ex.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        if args.no_schedule:
            results.append({"slug": slug, "slides": [str(p) for p in paths]})
            continue

        urls = publish_mod.upload_images(paths, cfg)
        when = slots[i]
        res = publish_mod.schedule_post(cfg, text, urls, when, dry_run=args.dry_run)
        print(f"  {'[dry-run] ' if args.dry_run else ''}geplant für {when:%d.%m.%Y %H:%M}")
        results.append({"slug": slug, "scheduled": when.isoformat(), "metricool": res})

    print("\n" + json.dumps(results, ensure_ascii=False, indent=2)[:2000])


def cmd_brands(args, cfg):
    print(json.dumps(publish_mod.list_brands(cfg), ensure_ascii=False, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="evslider")
    ap.add_argument("-c", "--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Slider bauen und einplanen")
    p_run.add_argument("urls", nargs="+")
    p_run.add_argument("--no-schedule", action="store_true",
                       help="nur Slides + Text erzeugen, nichts einplanen")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Metricool-Request nur anzeigen")
    p_run.add_argument("--browser", choices=["auto", "always", "never"], default="auto",
                       help="Browser für die Bildergalerie (Standard: auto)")
    p_run.set_defaults(func=cmd_run)

    p_probe = sub.add_parser("probe", help="Rohdaten eines Exposés anzeigen")
    p_probe.add_argument("url")
    p_probe.add_argument("--browser", choices=["auto", "always", "never"], default="auto")
    p_probe.set_defaults(func=cmd_probe)

    p_brands = sub.add_parser("brands", help="Metricool-Marken/blogIds auflisten")
    p_brands.set_defaults(func=cmd_brands)

    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
