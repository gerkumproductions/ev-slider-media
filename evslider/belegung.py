"""Belegte Termine bei Metricool erkennen und ausweichen.

Warum: Jeder Lauf hat bisher denselben Termin errechnet (z.B. Montag 9:30).
Zwei Objekte an einem Abend landeten damit auf derselben Uhrzeit. Hier wird
vor dem Einplanen nachgefragt, was in dem Zeitraum schon im Kalender steht -
und bei Bedarf auf den naechsten freien Tag geschoben.

Faellt die Abfrage aus (kein Token, API-Fehler), bleibt der Wunschtermin
stehen. Lieber ein doppelt belegter Slot als ein Post, der gar nicht kommt -
im Protokoll steht dann eine Warnung.
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

WOCHENTAGE = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
              "friday": 4, "saturday": 5, "sunday": 6}

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
    """Was liegt zwischen `von` und `bis` schon im Metricool-Kalender?"""
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
    return out


def freier_termin(cfg, wunsch: dt.datetime, vorschau_tage: int = 21) -> dt.datetime:
    """Wunschtermin - oder der naechste Tag, an dem die Uhrzeit noch frei ist.

    Regeln (alle ueber config.yaml einstellbar):
      schedule.slot_check          an/aus, Vorgabe an
      schedule.slot_toleranz_min   wie nah ein Post sein darf, Vorgabe 60 Min
      schedule.tag_komplett        true = ein Post pro Tag, egal zu welcher
                                   Uhrzeit. Vorgabe false.
      schedule.days                erlaubte Wochentage, Vorgabe Mo-Sa
    """
    if not cfg.get("schedule.slot_check", True):
        return wunsch

    tz = _tz(cfg)
    if wunsch.tzinfo is None:
        wunsch = wunsch.replace(tzinfo=tz)

    erlaubt = {WOCHENTAGE[d.lower()] for d in cfg.get(
        "schedule.days",
        ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"])}
    toleranz = dt.timedelta(minutes=cfg.get("schedule.slot_toleranz_min", 60))
    tag_komplett = bool(cfg.get("schedule.tag_komplett", False))

    ende = wunsch + dt.timedelta(days=vorschau_tage)
    try:
        belegt = geplante_termine(cfg, wunsch - dt.timedelta(days=1), ende)
    except Exception as exc:                                   # noqa: BLE001
        print(f"[i] Belegung konnte nicht geprüft werden ({exc}) - "
              f"Wunschtermin bleibt: {wunsch:%a %d.%m. %H:%M}.")
        return wunsch

    print(f"[i] Im Kalender bis {ende:%d.%m.}: {len(belegt)} Posts gefunden.")

    kandidat = wunsch
    for _ in range(vorschau_tage + 1):
        if kandidat.weekday() not in erlaubt:
            print(f"[i] {kandidat:%a %d.%m.} ist kein Posttag - einen weiter.")
            kandidat += dt.timedelta(days=1)
            continue

        if tag_komplett:
            kollision = next(
                (b for b in belegt if b.date() == kandidat.date()), None)
        else:
            kollision = next(
                (b for b in belegt if abs(b - kandidat) <= toleranz), None)

        if kollision is None:
            if kandidat != wunsch:
                print(f"[i] Termin verschoben: {wunsch:%a %d.%m. %H:%M} war belegt, "
                      f"neu {kandidat:%a %d.%m. %H:%M}.")
            else:
                print(f"[i] Termin frei: {kandidat:%a %d.%m. %H:%M}.")
            return kandidat

        print(f"[i] {kandidat:%a %d.%m. %H:%M} belegt "
              f"(Post um {kollision:%H:%M}) - einen Tag weiter.")
        kandidat += dt.timedelta(days=1)

    print(f"[i] In {vorschau_tage} Tagen kein freier Termin gefunden - "
          f"bleibe bei {wunsch:%a %d.%m. %H:%M}.")
    return wunsch
