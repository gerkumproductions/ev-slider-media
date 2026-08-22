"""Bilder öffentlich hosten + Post in Metricool einplanen."""
from __future__ import annotations

import datetime as dt
import mimetypes
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}


# ---------- Hosting ----------

def upload_images(paths: list[Path], cfg) -> list[str]:
    """Metricool braucht öffentlich erreichbare Bild-URLs.

    Modi:
      github : kostenloses öffentliches Repo, URLs über raw.githubusercontent.com
      s3     : S3 oder Cloudflare R2
      manual : ein Server, der die Dateien selbst ausliefert
    """
    import os

    if cfg.get("hosting.mode") == "github":
        return _upload_github(paths, cfg)

    mode = cfg.get("hosting.mode", "manual")
    if mode == "manual":
        # Der Server liefert die Bilder selbst unter <PUBLIC_URL>/media/<slug>/<datei>
        base = (os.environ.get("EVSLIDER_PUBLIC_URL")
                or cfg.get("hosting.public_base_url", "")).rstrip("/")
        if not base:
            raise RuntimeError(
                "hosting.mode=manual ohne öffentliche URL: EVSLIDER_PUBLIC_URL setzen "
                "(oder hosting.mode=s3 konfigurieren). Metricool kann keine lokalen "
                "Dateien lesen."
            )
        out_root = cfg.path(cfg.get("output_dir", "out")).resolve()
        return [f"{base}/media/{p.resolve().relative_to(out_root).as_posix()}" for p in paths]
    if mode != "s3":
        raise ValueError(f"Unbekannter hosting.mode: {mode}")

    import boto3  # optional dependency

    kwargs = {}
    if cfg.get("hosting.endpoint_url"):
        kwargs["endpoint_url"] = cfg.get("hosting.endpoint_url")
    s3 = boto3.client("s3", **kwargs)
    bucket = cfg.get("hosting.bucket")
    prefix = cfg.get("hosting.prefix", "")
    base = cfg.get("hosting.public_base_url", "").rstrip("/")

    urls = []
    for p in paths:
        key = f"{prefix}{p.name}"
        ctype = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        s3.upload_file(str(p), bucket, key,
                       ExtraArgs={"ContentType": ctype, "CacheControl": "public, max-age=31536000"})
        urls.append(f"{base}/{key}" if base else f"https://{bucket}.s3.amazonaws.com/{key}")
    return urls


def _upload_github(paths: list[Path], cfg) -> list[str]:
    """Bilder in ein öffentliches GitHub-Repo legen.

    Kostet nichts, braucht keine Kreditkarte, und die URLs bleiben gültig.
    Voraussetzung: ein Repo (öffentlich) und ein Token mit Contents-Schreibrecht
    in GITHUB_TOKEN.
    """
    import base64
    import os

    token = os.environ.get("GITHUB_TOKEN")

    # Läuft das Tool in GitHub Actions, gewinnt IMMER das Repo aus der Umgebung.
    # Ein alter oder falscher Eintrag in der config.yaml kann so nicht mehr
    # dazwischenfunken.
    env_repo = os.environ.get("GITHUB_REPOSITORY")
    if env_repo and "/" in env_repo:
        owner, _, repo = env_repo.partition("/")
    else:
        owner = cfg.get("hosting.github_owner")
        repo = cfg.get("hosting.github_repo")
    branch = cfg.get("hosting.github_branch", "main")
    prefix = cfg.get("hosting.prefix", "")
    if not (token and owner and repo):
        raise RuntimeError(
            "GitHub-Hosting: GITHUB_TOKEN setzen sowie hosting.github_owner "
            "und hosting.github_repo in der config.yaml eintragen."
        )

    print(f"[i] Bild-Upload nach {owner}/{repo} (Branch {branch})")
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    urls = []
    for p in paths:
        key = f"{prefix}{p.parent.name}/{p.name}".lstrip("/")
        api = f"https://api.github.com/repos/{owner}/{repo}/contents/{key}"
        body = {"message": f"slider: {p.name}",
                "content": base64.b64encode(p.read_bytes()).decode(),
                "branch": branch}
        # Existiert die Datei schon, braucht GitHub den bisherigen sha
        prev = requests.get(api, params={"ref": branch}, headers=headers, timeout=30)
        if prev.status_code == 200:
            body["sha"] = prev.json().get("sha")
        r = requests.put(api, json=body, headers=headers, timeout=120)
        if r.status_code >= 400:
            hinweis = ""
            if r.status_code == 404:
                hinweis = (f" – Repo {owner}/{repo} nicht gefunden oder der Token "
                           f"hat kein Schreibrecht darauf.")
            elif r.status_code == 403:
                hinweis = (" – kein Schreibrecht. In der slider.yml muss "
                           "'permissions: contents: write' stehen.")
            raise RuntimeError(f"GitHub-Upload fehlgeschlagen ({r.status_code})"
                               f"{hinweis}: {r.text[:200]}")
        urls.append(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{key}")
    return urls


def normalize_media(cfg, url: str) -> str:
    """Bild auf Metricools Server kopieren lassen.

    Metricool empfiehlt das ausdrücklich vor dem Einplanen - danach hängt der
    Post nicht mehr davon ab, dass unsere URL erreichbar bleibt.
    """
    base = cfg.get("metricool.base_url").rstrip("/")
    try:
        r = requests.get(f"{base}/actions/normalize/image/url",
                         params={"url": url},
                         headers={"X-Mc-Auth": cfg.metricool_token or ""},
                         timeout=90)
        if r.status_code >= 400:
            return url
        data = r.json() if r.text.strip().startswith(("{", "[")) else r.text.strip()
        if isinstance(data, dict):
            return data.get("url") or data.get("data") or url
        return data or url
    except Exception:                                          # noqa: BLE001
        return url        # im Zweifel die Original-URL nehmen


# ---------- Termin bestimmen ----------

def plan_slots(cfg, count: int) -> list[dt.datetime]:
    """Termine für `count` Posts auf die Wochentage verteilen.

    Strategie: erst ein Post pro Tag (Mo–Sa) zur Primärzeit – das gibt den
    größten Abstand. Reicht das nicht, kommt die zweite Uhrzeit dazu, danach
    rollt es in die Folgewoche.

    Vor der Auswahl wird bei Metricool nachgefragt, welche dieser Termine
    schon belegt sind. Ohne diesen Schritt bekam jeder Lauf denselben Termin,
    weil das Tool ja nur seine eigene Rechnung kennt und nicht den Kalender.
    """
    tz = ZoneInfo(cfg.get("schedule.timezone", "Europe/Berlin"))
    now = dt.datetime.now(tz)
    days = [WEEKDAYS[d.lower()] for d in cfg.get(
        "schedule.days", ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"])]
    times = cfg.get("schedule.times", ["18:00"])
    lead = dt.timedelta(hours=cfg.get("schedule.min_lead_hours", 12))

    # Kandidaten in Prioritätsreihenfolge: erst jeder Tag zur ersten Uhrzeit,
    # dann zweite Uhrzeit, dann Folgewoche. Erst danach wird chronologisch sortiert.
    ordered: list[dt.datetime] = []
    for week in range(8):
        for time_str in times:
            hh, mm = (int(x) for x in time_str.split(":"))
            for wd in days:
                days_ahead = (wd - now.weekday()) % 7 + week * 7
                cand = (now + dt.timedelta(days=days_ahead)).replace(
                    hour=hh, minute=mm, second=0, microsecond=0)
                if cand < now + lead or cand in ordered:
                    continue
                ordered.append(cand)

    # Belegte Termine aussortieren. Die Reihenfolge bleibt dabei erhalten.
    try:
        from . import belegung
    except ImportError:                                        # direkter Aufruf
        import belegung                                        # type: ignore
    ordered = belegung.freie_kandidaten(cfg, ordered)

    gewaehlt = sorted(ordered[:count])
    for t in gewaehlt:
        print(f"[i] Termin: {t:%a %d.%m.%Y %H:%M}")
    return gewaehlt


def next_slot(cfg, offset_index: int = 0) -> dt.datetime:
    """Einzelnen Termin holen (Kompatibilität)."""
    return plan_slots(cfg, offset_index + 1)[offset_index]


# ---------- Metricool ----------

def schedule_post(cfg, text: str, image_urls: list[str], when: dt.datetime,
                  dry_run: bool = False) -> dict:
    base = cfg.get("metricool.base_url").rstrip("/")
    url = f"{base}/v2/scheduler/posts"
    params = {"userId": cfg.metricool_user_id, "blogId": cfg.metricool_blog_id}
    if cfg.get("metricool.normalize_media", True) and not dry_run:
        image_urls = [normalize_media(cfg, u) for u in image_urls]
    body = {
        "autoPublish": cfg.get("metricool.auto_publish", True),
        "text": text,
        "media": image_urls,
        "publicationDate": {
            "dateTime": when.strftime("%Y-%m-%dT%H:%M:%S"),
            "timezone": cfg.get("schedule.timezone", "Europe/Berlin"),
        },
        "providers": [{"network": n} for n in cfg.get("metricool.networks", ["instagram"])],
        "descendants": [],
    }
    if dry_run:
        return {"dry_run": True, "url": url, "params": params, "body": body}

    if not (cfg.metricool_token and params["userId"] and params["blogId"]):
        raise RuntimeError("METRICOOL_USER_TOKEN / METRICOOL_USER_ID / METRICOOL_BLOG_ID fehlen.")

    r = requests.post(url, params=params, json=body,
                      headers={"X-Mc-Auth": cfg.metricool_token,
                               "Content-Type": "application/json"},
                      timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Metricool {r.status_code}: {r.text[:500]}")
    return r.json() if r.text else {"status": r.status_code}


def list_brands(cfg) -> list[dict]:
    """Hilfsaufruf: blogId (Marke) herausfinden."""
    base = cfg.get("metricool.base_url").rstrip("/")
    r = requests.get(f"{base}/admin/simpleProfiles",
                     params={"userId": cfg.metricool_user_id},
                     headers={"X-Mc-Auth": cfg.metricool_token}, timeout=30)
    r.raise_for_status()
    return r.json()
