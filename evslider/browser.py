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

# Ab hier zeigt die Seite fremde Objekte. Alles danach ist tabu.
FREMD_MARKER = (
    "Objekte in der Nähe", "Ähnliche Objekte", "Das könnte Sie auch",
    "Weitere Immobilien", "Ähnliche Immobilien", "Weitere Objekte",
)

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

# Bilder, die nicht zur Galerie gehören. Beim Nachfüllen aus der
# Seitenstruktur darf so kein Werbebild ein echtes Objektfoto verdrängen.
EXCLUDE_ALT = (
    "engel", "völkers", "voelkers", "shop image", "wavy pattern", "logo",
    "homebuyer", "home buyer", "realtor", "real estate agent", "estate agent",
    "advisor", "consultant", "handshake", "shaking hands", "consultation",
)


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


def _eigene_reihenfolge(html: str) -> list[str]:
    """UUIDs des eigenen Objekts in der Reihenfolge des Quelltextes.

    Die Galeriebilder stehen dort oben, Werbung und Shop-Logo weiter unten.
    Deshalb reicht es, die ersten N zu nehmen - ganz ohne Blättern.
    """
    schnitt = min((html.find(m) for m in FREMD_MARKER if html.find(m) > 0),
                  default=-1)
    bereich = html[:schnitt] if schnitt > 0 else html
    rest = html[schnitt:] if schnitt > 0 else ""

    # Logos und Shop-Grafiken tauchen auch bei den fremden Objekten weiter
    # unten auf. Echte Objektfotos gibt es nur einmal, im eigenen Bereich.
    auch_woanders = set(UPLOADCARE_RE.findall(rest))

    gesehen, out = set(), []
    for uuid in UPLOADCARE_RE.findall(bereich):
        if uuid in gesehen or uuid in auch_woanders:
            continue
        # Mehrfach wiederholte Bilder sind Logos oder Platzhalter
        if bereich.count(uuid) > 4:
            continue
        gesehen.add(uuid)
        out.append(uuid)
    return out


def _eigene_uuids(html: str) -> set[str] | None:
    """UUIDs, die VOR dem Bereich fremder Objekte stehen.

    Ohne diese Grenze landen Fotos von Nachbarobjekten im Slider - das darf
    nie passieren. None = kein Marker gefunden, dann gilt keine Einschränkung.
    """
    schnitt = min((html.find(m) for m in FREMD_MARKER if html.find(m) > 0),
                  default=-1)
    if schnitt <= 0:
        return None
    return set(UPLOADCARE_RE.findall(html[:schnitt]))


def _harvest(page, erlaubt: set[str] | None = None) -> list[tuple[str, str, str]]:
    """(uuid, alt, bildunterschrift) aller Uploadcare-Bilder im aktuellen DOM."""
    raw = page.eval_on_selector_all("img, source", _JS_HARVEST)
    out: list[tuple[str, str, str]] = []
    for item in raw:
        m = UPLOADCARE_RE.search(item.get("src") or "")
        if not m:
            continue
        if erlaubt is not None and m.group(1) not in erlaubt:
            continue                      # gehört zu einem fremden Objekt
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
    alt: best ? (best.alt || '') : '',
    caption: caption,
    zaehler: zaehler ? zaehler.innerText.trim() : ''
  };
}
"""


# Wenn keine Unterschrift gefunden wird: zeigen, was rund um den Zaehler steht.
# Damit laesst sich der Seitenaufbau nachtraeglich anpassen.
_JS_DIAGNOSE = r"""
() => {
  const istZaehler = t => /^\d+\s*\/\s*\d+$/.test((t || '').trim());
  const blaetter = [...document.querySelectorAll('span,div,p,figcaption')]
    .filter(n => n.children.length === 0);
  const z = blaetter.find(n => istZaehler(n.innerText));
  if (!z) return 'kein Zaehler gefunden. Sichtbare kurze Texte: ' +
    blaetter.map(n => (n.innerText || '').trim())
            .filter(t => t && t.length < 40).slice(-12).join(' | ');
  let p = z.parentElement, pfad = [];
  for (let i = 0; i < 3 && p; i++, p = p.parentElement) {
    pfad.push(p.tagName + '.' + (p.className || '').toString().slice(0, 30) +
      ' -> [' + [...p.querySelectorAll('span,div,p,figcaption')]
        .filter(n => n.children.length === 0)
        .map(n => (n.innerText || '').trim()).filter(Boolean).join(' | ') + ']');
  }
  return pfad.join('  ##  ');
}
"""


def _diagnose(page) -> str:
    try:
        return str(page.evaluate(_JS_DIAGNOSE))
    except Exception as exc:                                   # noqa: BLE001
        return f"Diagnose fehlgeschlagen: {exc}"


def _zaehlerstand(page) -> str:
    try:
        d = page.evaluate(_JS_CURRENT)
        return str(d.get("zaehler") or "")
    except Exception:                                          # noqa: BLE001
        return ""


# Sucht den Weiter-Pfeil ueber seine LAGE, nicht ueber Beschriftungen:
# rechter Bildrand, vertikal mittig, anklickbar. So finden wir ihn auch,
# wenn er weder aria-label noch sprechende Klasse hat.
_JS_PFEIL = r"""
() => {
  const W = window.innerWidth, H = window.innerHeight;
  const kandidaten = [...document.querySelectorAll(
    'button,[role="button"],a,svg,div,span')];
  let best = null;
  for (const el of kandidaten) {
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 20 || r.width > 160 || r.height > 160) continue;
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cx < W * 0.72) continue;              // muss rechts liegen
    if (cy < H * 0.25 || cy > H * 0.80) continue;  // vertikal mittig
    if (!best || cx > best.cx) best = { el, cx, cy };
  }
  if (!best) return null;
  best.el.scrollIntoView({block: 'center'});
  return { x: Math.round(best.cx), y: Math.round(best.cy) };
}
"""


def _weiterblaettern(page) -> bool:
    """Ein Bild weiter. Erfolg wird am Zähler geprüft - nicht daran, ob ein
    Klick möglich war. Erst wenn der Zähler sich ändert, ist wirklich
    umgeblättert worden.
    """
    vorher = _zaehlerstand(page)

    def pfeil_klicken():
        pos = page.evaluate(_JS_PFEIL)
        if not pos:
            raise RuntimeError("kein Pfeil gefunden")
        page.mouse.click(pos["x"], pos["y"])

    versuche = [("Pfeil nach Lage", pfeil_klicken)]
    for sel in NEXT_SELECTORS:
        versuche.append(("klick " + sel, lambda s=sel: page.locator(s).last.click(timeout=1200)))
    versuche.append(("pfeiltaste", lambda: page.keyboard.press("ArrowRight")))

    def rechts_klicken():
        box = page.viewport_size or {"width": 1440, "height": 1000}
        page.mouse.click(int(box["width"] * 0.90), int(box["height"] * 0.5))
    versuche.append(("rechter Rand", rechts_klicken))

    for name, aktion in versuche:
        try:
            aktion()
        except Exception:
            continue
        page.wait_for_timeout(1200)
        if _zaehlerstand(page) != vorher:
            if name != _weiterblaettern.zuletzt:
                print(f"[i] Weiterblättern per: {name}")
                _weiterblaettern.zuletzt = name
            return True
    return False


_weiterblaettern.zuletzt = ""


def _current_slide(page) -> tuple[str, str, str]:
    """(uuid, alt-Text, Unterschrift) des gerade sichtbaren Bildes."""
    try:
        d = page.evaluate(_JS_CURRENT)
    except Exception:                                          # noqa: BLE001
        return "", "", ""
    m = UPLOADCARE_RE.search(d.get("src") or "")
    return ((m.group(1) if m else ""),
            (d.get("alt") or "").strip(),
            (d.get("caption") or "").strip())


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

            eigene = _eigene_uuids(page.content())
            eigene_start = eigene
            print(f"[i] Bilder dieses Objekts laut Seitenaufbau: "
                  f"{len(eigene) if eigene is not None else 'Grenze nicht erkannt'}")

            def collect():
                for uuid, alt, caption in _harvest(page, eigene):
                    alt_alt, alt_cap = seen.get(uuid, ("", ""))
                    if uuid not in seen:
                        order.append(uuid)
                    # Einmal gefundene Angaben nicht durch leere ueberschreiben
                    seen[uuid] = (alt or alt_alt, caption or alt_cap)

            collect()

            # Vollbild-Bilderansicht öffnen: erst über den Reiter "Bilder",
            # sonst durch Klick aufs erste Bild.
            # Reihenfolge ist entscheidend: Erst die Vollbild-Ansicht öffnen
            # (Klick aufs Hauptbild), DANN erscheint der Reiter "Bilder (5)".
            try:
                page.locator("img[src*='uploadcare']").first.click(timeout=3000)
                page.wait_for_timeout(1800)
            except Exception:
                pass

            reiter = False
            for versuch in (
                lambda: page.get_by_text(re.compile(r"^\s*Bilder\s*\(\d+\)\s*$")).first,
                lambda: page.get_by_role("tab", name=re.compile("Bilder")).first,
                lambda: page.locator("button:has-text('Bilder'), a:has-text('Bilder')").first,
            ):
                try:
                    el = versuch()
                    if el.is_visible(timeout=1200):
                        el.click(timeout=2500)
                        reiter = True
                        page.wait_for_timeout(1500)
                        break
                except Exception:
                    continue
            print(f"[i] Bilder-Reiter geöffnet: {reiter} | Zähler: "
                  f"{_zaehlerstand(page) or 'keiner'}")

            total = expected_total(page.inner_text("body")) or total
            try:
                box = page.viewport_size or {"width": 1440, "height": 1000}
                page.mouse.move(box["width"] // 2, box["height"] // 2)
            except Exception:
                pass

            # Durch die Vollbild-Ansicht blättern und dabei Bild + Unterschrift
            # paarweise mitschreiben. Das ist die verlaessliche Quelle: hier
            # tauchen nur Bilder auf, die wirklich zur Galerie gehoeren.
            galerie: list[tuple[str, str, str]] = []
            gesehen: set[str] = set()
            # Grosszuegig blaettern: lieber ein paar Klicks zu viel als ein
            # halber Slider. Abbruch erst, wenn mehrfach nichts Neues kommt.
            schritte = max((total or 0) * 2, 24)
            leerlauf = 0
            for _ in range(schritte):
                # Grosse Fotos brauchen einen Moment. Mehrfach nachsehen,
                # bis ein neues Bild wirklich geladen ist.
                uuid, alt, caption = _current_slide(page)
                for _versuch in range(6):
                    if uuid and uuid not in gesehen:
                        break
                    page.wait_for_timeout(700)
                    uuid, alt, caption = _current_slide(page)
                total = total or expected_total(page.inner_text("body"))
                # Dieselbe Unterschrift zweimal heißt: die Zuordnung stimmt
                # nicht. Dann lieber keine als eine falsche.
                if caption and any(c == caption for _, _, c in galerie):
                    caption = ""

                if uuid and uuid not in gesehen:
                    gesehen.add(uuid)
                    galerie.append((uuid, alt, caption))
                    leerlauf = 0
                elif uuid:
                    for i, (u, a, c) in enumerate(galerie):
                        if u == uuid:
                            galerie[i] = (u, a or alt, c or caption)
                    leerlauf += 1
                else:
                    leerlauf += 1

                # Zusätzlich mitnehmen, was durch das Blättern neu geladen
                # wurde - aber NUR wenn die Grenze zu fremden Objekten bekannt
                # ist, und OHNE Unterschriften: die sind hier nicht sicher dem
                # richtigen Bild zuzuordnen. Unterschriften kommen allein aus
                # der Zähler-Zuordnung oben.
                if eigene is not None:
                    for u2, a2, _ in _harvest(page, eigene):
                        if u2 not in gesehen:
                            gesehen.add(u2)
                            galerie.append((u2, a2, ""))
                            leerlauf = 0
                if total and len(galerie) >= total:
                    break
                # nachgeladene eigene Bilder in die Grenze aufnehmen
                if eigene_start is not None:
                    neu = _eigene_uuids(page.content())
                    if neu:
                        eigene = eigene_start | neu
                if leerlauf >= 6:      # sechsmal dasselbe Bild -> Ende
                    break
                if not _weiterblaettern(page):
                    leerlauf += 2

            # Fehlt etwas gegenueber dem Zaehler, aus dem DOM nachfuellen.
            # Dann ist die Quelle nicht mehr rein - Werbefilter bleibt aktiv.
            # Auffüllen aus dem Quelltext: die ersten N eigenen Bilder.
            soll = total or 5
            if len(galerie) < soll:
                html_jetzt = page.content()
                for uuid in _eigene_reihenfolge(html_jetzt):
                    if len(galerie) >= soll:
                        break
                    if uuid in gesehen:
                        continue
                    gesehen.add(uuid)
                    galerie.append((uuid, "", ""))
                print(f"[i] Aus dem Quelltext auf {len(galerie)} Bilder aufgefüllt "
                      f"(Soll: {soll}).")

            ergaenzt = False
            if total and len(galerie) < total:
                if eigene is None:
                    print("[i] Diaschau unvollständig, aber die Grenze zu fremden "
                          "Objekten ist unklar - es wird NICHT ergänzt.")
                else:
                    ergaenzt = True
                    print(f"[i] Diaschau brachte nur {len(galerie)} von {total} - "
                          f"ergänze aus dem Bereich dieses Objekts.")
                    collect()
                    for uuid in order:
                        if uuid not in gesehen and len(galerie) < total:
                            gesehen.add(uuid)
                            a, c = seen.get(uuid, ("", ""))
                            galerie.append((uuid, a, c))
                    print(f"[i] Ergänzt auf {len(galerie)} Bilder "
                          f"(nur eigene).")

            # Wenn die Diaschau nicht vollstaendig war: Screenshot mitgeben,
            # damit man sieht, was der Browser dort wirklich vor sich hat.
            schnappschuss = ""
            if not total or len(galerie) < total:
                try:
                    from pathlib import Path
                    ziel = Path("out") / "galerie-diagnose.png"
                    ziel.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(ziel), full_page=False)
                    schnappschuss = str(ziel)
                    print(f"[i] Screenshot der Galerie gespeichert: {ziel}")
                except Exception as exc:                       # noqa: BLE001
                    print(f"[i] Screenshot fehlgeschlagen: {exc}")

            if galerie and (not total or len(galerie) >= min(total, 3)):
                mit = sum(1 for _, _, c in galerie if c)
                print(f"[i] Aus der Vollbild-Ansicht: {len(galerie)} Bilder, "
                      f"{mit} mit Unterschrift.")
                if not mit:
                    print("[i] Diagnose (Umfeld des Zählers):", _diagnose(page)[:400])
                # Aus der Galerie: hier liegen nur Objektfotos, keine Werbung.
                return {"html": page.content(), "photos": galerie,
                        "expected": total, "aus_galerie": not ergaenzt,
                        "diagnose_bild": schnappschuss}

            print("[i] Vollbild-Ansicht lieferte zu wenig - nutze die Seitenstruktur.")
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

            mit_caption = sum(1 for u in order if seen.get(u, ("", ""))[1])
            print(f"[i] {len(order)} Bilder, davon {mit_caption} mit Unterschrift "
                  f"von der Website.")
            if not mit_caption:
                print("[i] Diagnose (Umfeld des Zählers):", _diagnose(page)[:400])

            html = page.content()
            return {"html": html,
                    "photos": [(u, seen[u][0], seen[u][1]) for u in order],
                    "expected": total, "aus_galerie": False,
                    "diagnose_bild": schnappschuss}
        finally:
            browser.close()
