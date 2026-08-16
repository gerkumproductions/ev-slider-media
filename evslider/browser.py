"""Bildergalerie mit echtem Browser auslesen.

Warum: Die Exposé-Seite rendert serverseitig nur die ersten paar Bilder.
Der Rest kommt per JavaScript nach, wenn man durch die Galerie blättert.
Der Zähler auf der Seite ("1/6") verrät die Gesamtzahl - den nutzen wir als
Kontrolle, ob wir wirklich alles erwischt haben.
"""
from __future__ import annotations

import re

UPLOADCARE_RE = re.compile(
    r"uploadcare\.engelvoelkers\.com/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})"
)
COUNTER_RE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,3})\b")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

COOKIE_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "button#uc-btn-accept-banner",
    "[data-testid='uc-accept-all-button']",
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Akzeptieren')",
    "button:has-text('Zustimmen')",
]

NEXT_SELECTORS = [
    "button[aria-label*='ächste' i]",       # Nächste / naechste
    "button[aria-label*='next' i]",
    "[data-testid*='next' i]",
    "button:has-text('next')",
    ".swiper-button-next",
]

# Bilder, die nicht zur Galerie gehören
EXCLUDE_ALT = ("engel", "völkers", "voelkers", "shop image", "wavy pattern", "logo")


def expected_total(page_text: str) -> int | None:
    """Gesamtzahl aus dem Galerie-Zähler ('1/6' -> 6)."""
    best = None
    for cur, total in COUNTER_RE.findall(page_text):
        c, t = int(cur), int(total)
        if 1 <= c <= t <= 60:            # plausibler Galerie-Zähler
            best = max(best or 0, t)
    return best


def _harvest(page) -> list[tuple[str, str]]:
    """(uuid, alt) aller Uploadcare-Bilder im aktuellen DOM, in Dokumentreihenfolge."""
    raw = page.eval_on_selector_all(
        "img, source",
        """els => els.map(e => ({
             src: e.currentSrc || e.src || e.srcset || '',
             alt: e.alt || (e.parentElement && e.parentElement.querySelector('img')
                            ? e.parentElement.querySelector('img').alt : '') || ''
           }))""",
    )
    out: list[tuple[str, str]] = []
    for item in raw:
        m = UPLOADCARE_RE.search(item.get("src") or "")
        if not m:
            continue
        alt = (item.get("alt") or "").strip()
        if any(x in alt.lower() for x in EXCLUDE_ALT):
            continue
        out.append((m.group(1), alt))
    return out


def fetch_gallery(url: str, headless: bool = True, max_clicks: int = 40,
                  timeout: int = 45000) -> dict:
    """Öffnet die Seite, blättert durch die Galerie und sammelt alle Bilder.

    Rückgabe: {"html", "photos": [(uuid, alt)], "expected": int|None}
    """
    from playwright.sync_api import sync_playwright   # lokaler Import: optional

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, user_agent=UA,
                                locale="de-DE")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            page.wait_for_timeout(2500)

            for sel in COOKIE_SELECTORS:
                try:
                    if page.locator(sel).first.is_visible(timeout=800):
                        page.locator(sel).first.click(timeout=2000)
                        page.wait_for_timeout(1200)
                        break
                except Exception:
                    continue

            # Lazy-Loading anstoßen
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(800)
            page.mouse.wheel(0, -1200)
            page.wait_for_timeout(500)

            total = expected_total(page.inner_text("body"))

            seen: dict[str, str] = {}
            order: list[str] = []

            def collect():
                for uuid, alt in _harvest(page):
                    if uuid not in seen:
                        seen[uuid] = alt
                        order.append(uuid)
                    elif alt and not seen[uuid]:
                        seen[uuid] = alt

            collect()
            # Galerie öffnen (Vollbild zeigt oft alle Bilder auf einmal)
            try:
                page.locator("img[src*='uploadcare']").first.click(timeout=2500)
                page.wait_for_timeout(1500)
                collect()
            except Exception:
                pass

            stale = 0
            for _ in range(max_clicks):
                if total and len(order) >= total:
                    break
                before = len(order)
                clicked = False
                for sel in NEXT_SELECTORS:
                    try:
                        btn = page.locator(sel).last
                        if btn.is_visible(timeout=500):
                            btn.click(timeout=2000)
                            clicked = True
                            break
                    except Exception:
                        continue
                if not clicked:
                    page.keyboard.press("ArrowRight")
                page.wait_for_timeout(700)
                collect()
                stale = stale + 1 if len(order) == before else 0
                if stale >= 4:
                    break

            html = page.content()
            return {"html": html,
                    "photos": [(u, seen[u]) for u in order],
                    "expected": total}
        finally:
            browser.close()
