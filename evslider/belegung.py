"""Belegte Termine bei Metricool erkennen und ausweichen.

Warum: Jeder Lauf hat bisher denselben Termin errechnet (z.B. Montag 9:30).
Zwei Objekte hintereinander landeten damit auf derselben Uhrzeit. Hier wird
vor dem Einplanen nachgefragt, was im Kalender dieses Shops schon steht -
belegte Termine fallen raus.

Faellt die Abfrage aus (kein Token, API-Fehler), bleiben die Termine wie
berechnet. Lieber ein doppelt belegter Slot als ein Post, der gar nicht
kommt - im Protokoll steht dann eine Warnung.
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

WOCHENTAGE = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
              "friday": 4, "saturday": 5, "sunday": 6}

# Steht im Protokoll. Passt die Nummer nicht zu der, die publish.py erwartet,
# liegt eine alte Fassung im Repo.
VERSION = "2026-08-22b"

_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _tz(cfg) -> ZoneInfo:
    return ZoneInfo(cfg.get("schedule.timezone", "Europe/Berlin"))


def _datumsfelder(daten) -> list[str]:
    """Alle Datumsangaben aus der Antwort einsammeln.

    Metricool hat den Aufbau der Antwort schon mehrfach geaendert. Statt auf
    einen festen Pfad zu setzen, durchsuchen wir die Struktur nach Feldern,
    deren Name auf ein Datum hindeutet.
    """
    treffer: list[str] = []

    def lauf(knoten, schluessel=""):
        if isinstance(knoten, dict):
            for k, v in knoten.items():
                lauf(v, k)
        elif isinstance(knoten, list):
            for v in knoten:
                lauf(v, schluessel)
        elif isinstance(knoten, str):
            if "date" in schluessel.lower() or "time" in schluessel.lower():
                if _ISO_RE.match(knoten):
                    treffer.append(knoten)

    lauf(daten)
    return treffer


def geplante_termine(cfg, von: dt.datetime, bis: dt.datetime) -> list[dt.datetime]:
    """Was liegt zwischen `von` und `bis` schon im Metricool-Kalender?

    Gefragt wird immer fuer die blogId des Shops, der gerade dran ist. Zwei
    Shops blockieren sich also nicht gegenseitig.
    """
    import requests

    token = getattr(cfg, "metricool_token", None)
    user_id = getattr(cfg, "metricool_user_id", None)
    blog_id = getattr(cfg, "metricool_blog_id", None)
    if not (token and user_id and blog_id):
        raise RuntimeError("Metricool-Zugangsdaten fehlen")

    base = cfg.get("metricool.base_url", "https://app.metricool.com/api").rstrip("/")
    antwort = requests.get(
        f"{base}/v2/scheduler/posts",
        params={"userId": user_id, "blogId": blog_id,
                "start": von.strftime("%Y-%m-%dT%H:%M:%S"),
                "end": bis.strftime("%Y-%m-%dT%H:%M:%S")},
        headers={"X-Mc-Auth": token, "Accept": "application/json"},
        timeout=30,
    )
    antwort.raise_for_status()

    tz = _tz(cfg)
    out: list[dt.datetime] = []
    for roh in _datumsfelder(antwort.json()):
        try:
            d = dt.datetime.fromisoformat(roh.replace(" ", "T")[:19])
        except ValueError:
            continue
        out.append(d.replace(tzinfo=tz) if d.tzinfo is None else d.astimezone(tz))
    return sorted(set(out))


def _kollision(kandidat: dt.datetime, belegt: list[dt.datetime],
               toleranz: dt.timedelta, tag_komplett: bool):
    """Der Post, der dem Kandidaten im Weg steht - oder None."""
    if tag_komplett:
        return next((b for b in belegt if b.date() == kandidat.date()), None)
    return next((b for b in belegt if abs(b - kandidat) <= toleranz), None)


def freie_kandidaten(cfg, kandidaten: list[dt.datetime]) -> list[dt.datetime]:
    """Aus der Kandidatenliste alles entfernen, was schon belegt ist.

    Die Reihenfolge bleibt erhalten - `plan_slots` hat sie nach Prioritaet
    aufgebaut (erst jeder Tag zur ersten Uhrzeit, dann die zweite Uhrzeit,
    dann die Folgewoche).

    Einstellungen in config.yaml unter `schedule`:
      slot_check          an/aus, Vorgabe an
      slot_toleranz_min   wie nah ein Post sein darf, Vorgabe 60 Minuten
      tag_komplett        true = ein Post pro Tag, egal zu welcher Uhrzeit
    """
    if not kandidaten or not cfg.get("schedule.slot_check", True):
        return kandidaten

    toleranz = dt.timedelta(minutes=cfg.get("schedule.slot_toleranz_min", 60))
    tag_komplett = bool(cfg.get("schedule.tag_komplett", False))

    try:
        belegt = geplante_termine(cfg,
                                  min(kandidaten) - dt.timedelta(days=1),
                                  max(kandidaten) + dt.timedelta(days=1))
    except Exception as exc:                                   # noqa: BLE001
        print(f"[i] Belegung konnte nicht geprüft werden ({exc}) - "
              f"Termine werden ungeprüft vergeben.")
        return kandidaten

    regel = ("ein Post pro Tag" if tag_komplett
             else f"Toleranz {int(toleranz.total_seconds() // 60)} Min")
    print(f"[i] Kalender dieses Shops: {len(belegt)} Posts bereits "
          f"eingeplant ({regel}).")

    frei: list[dt.datetime] = []
    weg = 0
    for k in kandidaten:
        koll = _kollision(k, belegt, toleranz, tag_komplett)
        if koll is not None:
            weg += 1
            if weg <= 5:        # Protokoll nicht zumuellen
                print(f"[i] {k:%a %d.%m. %H:%M} belegt "
                      f"(Post um {koll:%d.%m. %H:%M}) - übersprungen.")
            continue
        # Auch die Termine beruecksichtigen, die in DIESEM Lauf schon
        # vergeben wurden - sonst kollidieren zwei Posts desselben
        # Durchgangs. Das ist keine Belegung, also ohne Protokollzeile.
        if _kollision(k, frei, toleranz, tag_komplett) is not None:
            continue
        frei.append(k)

    if weg > 5:
        print(f"[i] ... insgesamt {weg} bereits belegte Termine übersprungen.")
    if not frei:
        print("[i] Kein freier Termin im Vorausschau-Zeitraum - "
              "nehme die berechneten Termine trotzdem.")
        return kandidaten
    return frei


def freier_termin(cfg, wunsch: dt.datetime, vorschau_tage: int = 21) -> dt.datetime:
    """Einzelnen Wunschtermin pruefen und bei Bedarf tageweise weiterschieben.

    `plan_slots` braucht das nicht (es filtert ueber `freie_kandidaten`),
    fuer direkte Aufrufe ist es aber praktisch.
    """
    if not cfg.get("schedule.slot_check", True):
        return wunsch

    if wunsch.tzinfo is None:
        wunsch = wunsch.replace(tzinfo=_tz(cfg))

    erlaubt = {WOCHENTAGE[d.lower()] for d in cfg.get(
        "schedule.days",
        ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"])}

    kandidaten: list[dt.datetime] = []
    tag = wunsch
    for _ in range(vorschau_tage + 1):
        if tag.weekday() in erlaubt:
            kandidaten.append(tag)
        tag += dt.timedelta(days=1)

    frei = freie_kandidaten(cfg, kandidaten)
    ergebnis = frei[0] if frei else wunsch
    if ergebnis != wunsch:
        print(f"[i] Termin verschoben: {wunsch:%a %d.%m. %H:%M} war belegt, "
              f"neu {ergebnis:%a %d.%m. %H:%M}.")
    return ergebnis
