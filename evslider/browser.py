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

# Reiter, der die Vollbild-Bilderansicht öffnet - dort steht unter jedem Bild
# die deutsche Unterschrift ("offene Küche") neben dem Zähler ("3/5").
GALLERY_TAB_SELECTORS = [
    "button:has-text('Bilder')",
    "a:has-text('Bilder')",
    "[role='tab']:has-text('Bilder')",
    "div:has-text('Bilder ('):not(:has(div))",
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


# Sucht zu jedem Bild die sichtbare deutsche Bildunterschrift ("offene Küche").
# Die steht je nach Ansicht in einer figcaption oder in einem kurzen Textknoten
# direkt unter dem Bild. Der alt-Text ist nur die englische Notloesung.
_JS_HARVEST = r"""
els => {
  const kurz = t => t && t.trim().length > 0 && t.trim().length < 70;
  const bildunterschrift = el => {
    const fig = el.closest('figure');
    const fc = fig && fig.querySelector('figcaption');
    if (fc && kurz(fc.innerText)) return fc.innerText.trim();
    // Nur im Bereich suchen, der genau DIESES Bild enthaelt - sonst erbt ein
    // Bild ohne Unterschrift die des Nachbarn.
    let p = el.parentElement;
    for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
      if (p.querySelectorAll('img').length > 1) break;
      const kandidaten = [...p.querySelectorAll('figcaption,p,span,div')]
        .filter(n => n.children.length === 0 && kurz(n.innerText))
        .map(n => n.innerText.trim())
        .filter(t => !/^\d+\s*\/\s*\d+$/.test(t));
      if (kandidaten.length) return kandidaten[kandidaten.length - 1];
    }
    return '';
  };
  return els.map(e => ({
    src: e.currentSrc || e.src || e.srcset || '',
    alt: e.alt || '',
    caption: bildunterschrift(e)
  }));
}
"""


def _harvest(page) -> list[tuple[str, str, str]]:
    """(uuid, alt, bildunterschrift) aller Uploadcare-Bilder im aktuellen DOM."""
    raw = page.eval_on_selector_all("img, source", _JS_HARVEST)
    out: list[tuple[str, str, str]] = []
    for item in raw:
        m = UPLOADCARE_RE.search(item.get("src") or "")
        if not m:
            continue
        alt = (item.get("alt") or "").strip()
        if any(x in alt.lower() for x in EXCLUDE_ALT):
            continue
        out.append((m.group(1), alt, (item.get("caption") or "").strip()))
    return out


# Liest, was gerade im Vollbild zu sehen ist: grösstes Bild + Unterschrift.
# Die Unterschrift steht im selben Bereich wie der Zähler - darüber finden
# wir sie zuverlässiger als über die Position im Seitenaufbau.
_JS_CURRENT = r"""
() => {
  const kurz = t => t && t.trim().length > 0 && t.trim().length < 70;
  const istZaehler = t => /^\d+\s*\/\s*\d+$/.test((t || '').trim());
  const blaetter = [...document.querySelectorAll('span,div,p,figcaption')]
    .filter(n => n.children.length === 0);

  const zaehler = blaetter.find(n => istZaehler(n.innerText));
  let caption = '';
  if (zaehler) {
    let p = zaehler.parentElement;
    for (let i = 0; i < 3 && p && !caption; i++, p = p.parentElement) {
      const t = [...p.querySelectorAll('span,div,p,figcaption')]
        .filter(n => n.children.length === 0)
        .map(n => (n.innerText || '').trim())
        .filter(t => kurz(t) && !istZaehler(t));
      if (t.length) caption = t[0];
    }
  }

  let best = null, area = 0;
  for (const img of document.querySelectorAll('img')) {
    const r = img.getBoundingClientRect();
    const a = Math.max(0, r.width) * Math.max(0, r.height);
    if (r.bottom > 0 && r.top < window.innerHeight && a > area) { area = a; best = img; }
  }
  return {
    src: best ? (best.currentSrc || best.src || '') : '',
    caption: caption,
    zaehler: zaehler ? zaehler.innerText.trim() : ''
  };
}
"""


def _current_slide(page) -> tuple[str, str]:
    """(uuid, Unterschrift) des gerade sichtbaren Bildes."""
    try:
        d = page.evaluate(_JS_CURRENT)
    except Exception:                                          # noqa: BLE001
        return "", ""
    m = UPLOADCARE_RE.search(d.get("src") or "")
    return (m.group(1) if m else ""), (d.get("caption") or "").strip()


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

            seen: dict[str, tuple[str, str]] = {}     # uuid -> (alt, caption)
            order: list[str] = []

            def collect():
                for uuid, alt, caption in _harvest(page):
                    alt_alt, alt_cap = seen.get(uuid, ("", ""))
                    if uuid not in seen:
                        order.append(uuid)
                    # Einmal gefundene Angaben nicht durch leere ueberschreiben
                    seen[uuid] = (alt or alt_alt, caption or alt_cap)

            collect()

            # Vollbild-Bilderansicht öffnen: erst über den Reiter "Bilder",
            # sonst durch Klick aufs erste Bild.
            geoeffnet = False
            for sel in GALLERY_TAB_SELECTORS:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=800):
                        el.click(timeout=2000)
                        geoeffnet = True
                        break
                except Exception:
                    continue
            if not geoeffnet:
                try:
                    page.locator("img[src*='uploadcare']").first.click(timeout=2500)
                except Exception:
                    pass
            page.wait_for_timeout(1800)
            collect()

            def unterschrift_merken():
                uuid, caption = _current_slide(page)
                if uuid and caption:
                    alt_alt, alt_cap = seen.get(uuid, ("", ""))
                    if uuid not in seen:
                        order.append(uuid)
                    seen[uuid] = (alt_alt, caption)      # Website schlägt alt-Text

            unterschrift_merken()

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
                unterschrift_merken()
                stale = stale + 1 if len(order) == before else 0
                if stale >= 4:
                    break

            html = page.content()
            return {"html": html,
                    "photos": [(u, seen[u][0], seen[u][1]) for u in order],
                    "expected": total}
        finally:
            browser.close()
