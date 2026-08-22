"""Exposé-Daten von engelvoelkers.com auslesen.

Die Exposé-Seiten sind serverseitig gerendert. Wir versuchen in dieser
Reihenfolge:
  1. eingebettetes JSON (__NEXT_DATA__ / Apollo-State) -> sauberste Quelle
  2. JSON-LD
  3. DOM-Parsing als Fallback
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

UPLOADCARE_RE = re.compile(
    r"https://uploadcare\.engelvoelkers\.com/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})/"
)

# Bilder in Originalauflösung statt der komprimierten Web-Variante
HIGH_RES = "-/format/jpeg/-/stretch/off/-/progressive/yes/-/resize/2000x/-/quality/best/"

# Beschriftungen von Bedienelementen. Auf der E&V-Seite steckt das
# Galeriebild im Weiter-Element und traegt deshalb dessen alt-Text
# ("go to next image"). Ohne diesen Filter stand das als Bildtitel im
# fertigen Slide. Bewusst hier und nicht nur in browser.py: Hier laufen
# ALLE Wege zusammen (JSON, DOM, Browser), egal woher der Text kam.
UI_TEXT_RE = re.compile(
    r"^\s*(go\s+to\b|gehe\s+zu\b|next\b|previous\b|prev\b|weiter\b|zur[üu]ck\b"
    r"|n[äa]chste|vorherige|schlie[ßs]en\b|close\b|zoom|vergr[öo]|verklein"
    r"|vollbild\b|fullscreen\b|men[üu]\b|teilen\b|share\b|merken\b|drucken\b"
    r"|play\b|pause\b|slide\b|karussell|carousel|bild\s*\d+\s*$|image\s*\d+\s*$)",
    re.IGNORECASE)


def saubere_beschriftung(text: str) -> str:
    """Leere Zeichenkette, wenn der Text eine Bedienbeschriftung ist."""
    t = (text or "").strip()
    if not t or UI_TEXT_RE.match(t):
        return ""
    return t


@dataclass
class Photo:
    uuid: str
    alt: str = ""            # englischer alt-Text der Seite
    caption: str = ""        # deutsche Bildunterschrift der Seite ("offene Küche")
    title: str = ""          # was im Slider steht

    @property
    def url(self) -> str:
        return f"https://uploadcare.engelvoelkers.com/{self.uuid}/{HIGH_RES}"


def bereinige_beschriftungen(photos: list["Photo"]) -> list["Photo"]:
    """Bedienbeschriftungen aus alt, caption und title entfernen.

    Lieber gar kein Titel als ein falscher: Ein Slide ohne Bildtitel sieht
    unauffaellig aus, "go to next image" faellt jedem Betrachter auf.
    """
    entfernt: list[str] = []
    for p in photos:
        for feld in ("alt", "caption", "title"):
            alt_wert = getattr(p, feld, "") or ""
            neu = saubere_beschriftung(alt_wert)
            if alt_wert.strip() and not neu:
                entfernt.append(f"{feld}={alt_wert.strip()[:40]!r}")
            setattr(p, feld, neu)
    if entfernt:
        print(f"[i] {len(entfernt)} Bedienbeschriftung(en) verworfen: "
              f"{', '.join(entfernt[:4])}"
              f"{' ...' if len(entfernt) > 4 else ''}")
    return photos


@dataclass
class Expose:
    url: str
    ev_id: str = ""
    title: str = ""
    location: str = ""
    price: str = ""
    rooms: str = ""
    bathrooms: str = ""
    living_area: str = ""
    plot_area: str = ""
    year_built: str = ""
    energy_class: str = ""
    energy_value: str = ""
    property_type: str = ""
    floor: str = ""
    parking: str = ""
    features: list[str] = field(default_factory=list)
    description: str = ""
    location_text: str = ""
    shop: str = ""
    agent: str = ""
    photos: list[Photo] = field(default_factory=list)
    expected_images: int | None = None   # laut Galerie-Zähler auf der Seite
    source: str = "http"                 # http | browser
    aus_galerie: bool = False            # Bilder stammen aus der Vollbild-Galerie
    diagnose_bild: str = ""              # Screenshot, falls die Galerie klemmte

    def facts(self, wanted: list[str] | None = None) -> list[tuple[str, str]]:
        """Fakten fürs zweite Slide – nur was wirklich gefüllt ist.

        In der Liste darf ein Eintrag Alternativen enthalten, getrennt durch
        einen senkrechten Strich: "Grundstück|Baujahr" nimmt die
        Grundstücksfläche, wenn es eine gibt, sonst das Baujahr. So steht bei
        Häusern die Fläche und bei Wohnungen das Baujahr, ohne zwei
        Konfigurationen pflegen zu müssen.
        """
        available = {
            "Kaufpreis": self.price,
            "Wohnfläche": self.living_area,
            "Zimmer": self.rooms,
            "Badezimmer": self.bathrooms,
            "Grundstück": self.plot_area,
            "Baujahr": self.year_built,
            "Energieklasse": self.energy_class,
            "Objektart": self.property_type,
        }
        order = wanted or ["Wohnfläche", "Badezimmer", "Grundstück|Baujahr", "Zimmer"]

        out: list[tuple[str, str]] = []
        vergeben: set[str] = set()
        for eintrag in order:
            if isinstance(eintrag, (list, tuple)):
                kandidaten = [str(k).strip() for k in eintrag]
            else:
                kandidaten = [k.strip() for k in str(eintrag).split("|")]
            for k in kandidaten:
                if available.get(k) and k not in vergeben:
                    out.append((k, available[k]))
                    vergeben.add(k)
                    break

        if len(out) < 4:  # auffüllen, falls ein Wert fehlt
            for k, v in available.items():
                if v and k not in vergeben and len(out) < 4:
                    out.append((k, v))
                    vergeben.add(k)
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["photos"] = [{"uuid": p.uuid, "alt": p.alt, "caption": p.caption,
                        "title": p.title, "url": p.url} for p in self.photos]
        return d


def fetch_html(url: str, timeout: int = 30) -> str:
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"},
                     timeout=timeout)
    r.raise_for_status()
    return r.text


def _embedded_json(html: str) -> list[dict]:
    """Alle größeren JSON-Blobs aus <script>-Tags einsammeln."""
    blobs: list[dict] = []
    for m in re.finditer(
        r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            blobs.append(json.loads(m.group(1)))
        except Exception:
            pass
    for m in re.finditer(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S):
        try:
            blobs.append(json.loads(m.group(1)))
        except Exception:
            pass
    return blobs


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _photos_from_json(blobs: list[dict]) -> list[Photo]:
    """Bildergalerie aus dem JSON ziehen (bevorzugt, weil sortiert & vollständig)."""
    best: list[Photo] = []
    for blob in blobs:
        for node in _walk(blob):
            keys = {k.lower() for k in node.keys()}
            if not ({"images", "media", "pictures", "gallery"} & keys):
                continue
            for key in ("images", "media", "pictures", "gallery"):
                val = node.get(key)
                if not isinstance(val, list) or not val:
                    continue
                found: list[Photo] = []
                for item in val:
                    if isinstance(item, str):
                        m = UPLOADCARE_RE.search(item)
                        if m:
                            found.append(Photo(uuid=m.group(1)))
                    elif isinstance(item, dict):
                        raw = json.dumps(item)
                        m = UPLOADCARE_RE.search(raw)
                        uuid = m.group(1) if m else item.get("uuid") or item.get("id")
                        if not uuid or not re.fullmatch(r"[0-9a-f-]{36}", str(uuid)):
                            continue
                        alt = (item.get("alt") or item.get("caption")
                               or item.get("description") or item.get("title") or "")
                        found.append(Photo(uuid=str(uuid),
                                           alt=saubere_beschriftung(str(alt))))
                if len(found) > len(best):
                    best = found
    # Duplikate raus, Reihenfolge behalten
    seen, out = set(), []
    for p in best:
        if p.uuid not in seen:
            seen.add(p.uuid)
            out.append(p)
    return out


def _photos_from_dom(soup: BeautifulSoup, html: str) -> list[Photo]:
    """Fallback: <img>-Tags vor dem Block 'Objekte in der Nähe'."""
    cut = html.find("Objekte in der Nähe")
    scope_html = html[:cut] if cut > 0 else html
    scope = BeautifulSoup(scope_html, "html.parser")
    seen, out = set(), []
    for img in scope.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        m = UPLOADCARE_RE.search(src)
        if not m:
            continue
        alt = (img.get("alt") or "").strip()
        if "engel" in alt.lower() and "völkers" in alt.lower():
            continue  # Shop-Logo
        if m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out.append(Photo(uuid=m.group(1), alt=saubere_beschriftung(alt)))
    return out


def _label_value(soup: BeautifulSoup, label: str) -> str:
    """Sucht ein 'Label / Wert'-Paar in den Objektdetails."""
    for el in soup.find_all(string=re.compile(rf"^\s*{re.escape(label)}\s*$")):
        parent = el.parent
        for _ in range(3):
            if parent is None:
                break
            texts = [t.strip() for t in parent.stripped_strings]
            if len(texts) >= 2 and texts[0].strip() == label:
                return texts[1]
            parent = parent.parent
    return ""


def parse(html: str, url: str) -> Expose:
    soup = BeautifulSoup(html, "html.parser")
    blobs = _embedded_json(html)
    ex = Expose(url=url)

    og = soup.find("meta", property="og:title")
    ex.title = (og.get("content") if og else "") or (soup.title.string if soup.title else "")
    ex.title = ex.title.strip()

    ogd = soup.find("meta", property="og:description")
    ex.description = (ogd.get("content") if ogd else "").strip()

    text = soup.get_text("\n", strip=True)

    m = re.search(r"Engel & Völkers ID:\s*([A-Z0-9-]+)", text)
    ex.ev_id = m.group(1) if m else ""

    m = re.search(r"([\d.]+)\s*€", text)
    ex.price = f"{m.group(1)} €" if m else ""

    m = re.search(r"(\d+)\s*Zimmer", text)
    ex.rooms = m.group(1) if m else ""
    m = re.search(r"(\d+)\s*Badezimmer", text)
    ex.bathrooms = m.group(1) if m else ""
    m = re.search(r"~?\s*([\d.,]+)\s*m²\s*Wohnfläche", text)
    ex.living_area = f"ca. {m.group(1)} m²" if m else ""
    m = re.search(r"~?\s*([\d.,]+)\s*m²\s*Grundstücksfläche", text)
    ex.plot_area = f"ca. {m.group(1)} m²" if m else ""
    m = re.search(r"Baujahr\s*(\d{4})", text)
    ex.year_built = m.group(1) if m else ""
    m = re.search(r"Energieeffizienzklasse\s*([A-H][+]?)\b", text)
    ex.energy_class = m.group(1) if m else ""
    m = re.search(r"Endenergieverbrauch\s*([\d.,]+\s*kWh/m²a)", text)
    ex.energy_value = m.group(1) if m else ""
    m = re.search(r"Ihr:e Expert:in:\s*([^\n]+)", text)
    ex.agent = m.group(1).strip() if m else ""
    m = re.search(r"(Engel & Völkers [A-ZÄÖÜ][\wäöüß\- ]+)", text)
    ex.shop = m.group(1).strip() if m else ""

    ex.property_type = _label_value(soup, "Objektart")
    ex.floor = _label_value(soup, "Etage")
    ex.parking = _label_value(soup, "Parkplätze")

    # Ort: Breadcrumb "Ort: <Stadtteil>, <Stadt>"
    m = re.search(r"Ort:\s*([^\n]+)", text)
    ex.location = m.group(1).strip() if m else ""
    if not ex.location:
        h = soup.find(string=re.compile(r"^[A-ZÄÖÜ][\wäöüß\-]+,\s*[A-ZÄÖÜ]"))
        ex.location = h.strip() if h else ""

    ex.photos = bereinige_beschriftungen(
        _photos_from_json(blobs) or _photos_from_dom(soup, html))

    from .browser import expected_total
    ex.expected_images = expected_total(text)
    return ex


def scrape(url: str, browser: str = "auto") -> Expose:
    """Exposé auslesen.

    browser="auto"   : erst per HTTP; nur wenn Bilder fehlen, den Browser starten
    browser="always" : immer den Browser nehmen
    browser="never"  : nur HTTP (schnell, liefert aber oft nur die ersten Bilder)

    Hintergrund: Die Seite rendert serverseitig nur die ersten Galeriebilder.
    Der Zähler ("1/6") verrät, wie viele es wirklich sind.
    """
    html = fetch_html(url)
    ex = parse(html, url)

    need_browser = browser == "always" or (
        browser == "auto"
        and (not ex.photos
             or (ex.expected_images or 0) > len(ex.photos))
    )
    if not need_browser:
        return ex

    try:
        from .browser import fetch_gallery
        res = fetch_gallery(url)
    except ImportError:
        print("[!] Playwright nicht installiert - es fehlen möglicherweise Bilder. "
              "Installation: pip install playwright && playwright install chromium")
        return ex
    except Exception as exc:                                   # noqa: BLE001
        print(f"[!] Browser-Auslesen fehlgeschlagen ({str(exc)[:120]}) - "
              "nutze die HTTP-Variante.")
        return ex

    full = parse(res["html"], url)
    # Felder aus dem Browser-DOM bevorzugen, aber Lücken mit der HTTP-Variante füllen
    for f_name in ("title", "location", "price", "rooms", "bathrooms", "living_area",
                   "plot_area", "year_built", "energy_class", "energy_value", "ev_id",
                   "property_type", "floor", "parking", "description", "shop", "agent"):
        if not getattr(full, f_name):
            setattr(full, f_name, getattr(ex, f_name))
    full.photos = [Photo(uuid=u, alt=a, caption=c)
                   for u, a, c in res["photos"]] or ex.photos
    # Letzte Station vor dem Zeichnen: hier kommt nichts mehr durch, egal ob
    # es aus dem JSON, dem DOM oder der Diaschau stammt.
    full.photos = bereinige_beschriftungen(full.photos)
    full.expected_images = res.get("expected") or ex.expected_images
    full.source = "browser"
    full.aus_galerie = bool(res.get("aus_galerie"))
    full.diagnose_bild = res.get("diagnose_bild") or ""

    # Notbremse: Nie mehr Bilder als der Zähler der Seite angibt. Alles
    # darüber hinaus kann nur von einem fremden Objekt stammen.
    if full.expected_images and len(full.photos) > full.expected_images:
        print(f"[!] {len(full.photos)} Bilder, laut Seite gibt es nur "
              f"{full.expected_images} - überzählige werden verworfen.")
        full.photos = full.photos[:full.expected_images]

    if full.expected_images and len(full.photos) < full.expected_images:
        print(f"[!] Nur {len(full.photos)} von {full.expected_images} Bildern gefunden.")
    return full
