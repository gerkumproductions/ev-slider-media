"""CLI:  evslider run <expose-url> [<expose-url> ...]"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import bilder as bilder_mod
from . import briefing as briefing_mod
from . import caption as caption_mod
from . import config as config_mod
from . import publish as publish_mod
from .render import Renderer, save_all
from .render_heuser import HeuserRenderer
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


def cmd_brief(args, cfg):
    """Slider aus einem Briefing statt aus einem Expose."""
    cfg = cfg.for_shop(args.shop)
    if cfg.get("slides.layout", "ev") == "ev":
        print(f"[!] Shop {args.shop!r} nutzt das E&V-Layout und erwartet einen "
              f"Expose-Link. 'evslider run <url>' benutzen.")
        return 1

    thema = " ".join(args.text)
    br = briefing_mod.erzeuge(thema, cfg, keyword=args.keyword or "")
    print(f"  {len(br.slides)} Slides aus dem Briefing")

    slides = bilder_mod.erzeuge_alle(br.slides, cfg)
    ohne = sum(1 for s in slides if not s.get("photo"))
    if ohne:
        print(f"  [!] {ohne} Slide(s) ohne Foto")

    out_root = cfg.path(cfg.get("output_dir", "out"))
    shop_slug = slugify(cfg.get("brand.handle", "shop").lstrip("@"))
    slug = slugify(br.titel())
    ziel = out_root / shop_slug / slug
    paths = save_all(HeuserRenderer(cfg).build({"slides": slides}), ziel, slug)
    print(f"  {len(paths)} Slides → {ziel}")

    text = br.caption(cfg)
    (ziel / "caption.txt").write_text(text, encoding="utf-8")
    (ziel / "briefing.json").write_text(
        json.dumps({"thema": thema, "slides": br.slides},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    if args.no_schedule:
        print("  (nichts eingeplant)")
        return 0

    when = publish_mod.plan_slots(cfg, 1)[0]
    urls = publish_mod.upload_images(paths, cfg)
    publish_mod.schedule_post(cfg, text, urls, when, dry_run=args.dry_run)
    print(f"  {'[dry-run] ' if args.dry_run else ''}geplant für {when:%d.%m.%Y %H:%M}")
    return 0


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

    p_brief = sub.add_parser("brief", help="Slider aus einem Briefing bauen")
    p_brief.add_argument("shop", help="Shop-Kürzel, z.B. heuser")
    p_brief.add_argument("text", nargs="+", help="das Briefing")
    p_brief.add_argument("--keyword", default="", help="Stichwort für den CTA")
    p_brief.add_argument("--no-schedule", action="store_true",
                         help="nur Slides erzeugen, nichts einplanen")
    p_brief.add_argument("--dry-run", action="store_true")
    p_brief.set_defaults(func=cmd_brief)

    p_brands = sub.add_parser("brands", help="Metricool-Marken/blogIds auflisten")
    p_brands.set_defaults(func=cmd_brands)

    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
