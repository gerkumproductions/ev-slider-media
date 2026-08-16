"""Kleine Weboberfläche: Links einfügen → Slider bauen → Metricool einplanen.

Start:  uvicorn webapp:app --host 0.0.0.0 --port 8000
Login:  Passwort aus EVSLIDER_PASSWORD (Basic Auth, Benutzer beliebig)

Der Server hostet die fertigen Slides selbst unter /media/… – damit braucht
es keinen zusätzlichen S3-Bucket, solange der Server öffentlich erreichbar ist.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from evslider import caption as caption_mod
from evslider import config as config_mod
from evslider import publish as publish_mod
from evslider.cli import slugify
from evslider.render import Renderer, save_all
from evslider.scrape import scrape

cfg = config_mod.load(os.environ.get("EVSLIDER_CONFIG", "config.yaml"))
MEDIA = cfg.path(cfg.get("output_dir", "out"))
MEDIA.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE = os.environ.get("EVSLIDER_PUBLIC_URL", "").rstrip("/")

app = FastAPI(title="EV Slider")
app.mount("/media", StaticFiles(directory=MEDIA), name="media")
security = HTTPBasic()

JOBS: dict[str, dict] = {}


def auth(cred: HTTPBasicCredentials = Depends(security)) -> str:
    expected = os.environ.get("EVSLIDER_PASSWORD")
    if not expected or not secrets.compare_digest(cred.password, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falsches Passwort",
                            headers={"WWW-Authenticate": "Basic"})
    return cred.username


# ---------- Pipeline im Hintergrund ----------

def run_job(job_id: str, urls: list[str], schedule: bool) -> None:
    job = JOBS[job_id]
    renderer = Renderer(cfg)
    slots = publish_mod.plan_slots(cfg, len(urls))

    for i, url in enumerate(urls):
        entry: dict = {"url": url, "status": "läuft"}
        job["items"].append(entry)
        try:
            ex = scrape(url, browser=cfg.get('scrape.browser', 'auto'))
            entry["title"] = ex.title
            entry["images"] = f"{len(ex.photos)}/{ex.expected_images or '?'}"
            if not ex.photos:
                raise RuntimeError("Keine Bilder im Exposé gefunden")

            texts = caption_mod.generate(ex, cfg)
            text = caption_mod.full_text(texts, cfg)
            slug = f"{slugify(ex.location or ex.title)}-{ex.ev_id or i}-{job_id[:6]}"
            paths = save_all(renderer.build(ex), MEDIA / slug, slug)
            (MEDIA / slug / "caption.txt").write_text(text, encoding="utf-8")

            entry["preview"] = [f"/media/{slug}/{p.name}" for p in paths]
            entry["caption"] = text

            if schedule:
                if cfg.get("hosting.mode") == "s3":
                    img_urls = publish_mod.upload_images(paths, cfg)
                elif PUBLIC_BASE:
                    img_urls = [f"{PUBLIC_BASE}/media/{slug}/{p.name}" for p in paths]
                else:
                    raise RuntimeError(
                        "Keine öffentliche Bild-URL: EVSLIDER_PUBLIC_URL setzen "
                        "oder hosting.mode=s3 konfigurieren.")
                res = publish_mod.schedule_post(cfg, text, img_urls, slots[i])
                entry["scheduled"] = slots[i].strftime("%a %d.%m.%Y %H:%M")
                entry["metricool"] = res
            entry["status"] = "fertig"
        except Exception as exc:                                  # noqa: BLE001
            entry["status"] = "fehler"
            entry["error"] = str(exc)[:300]
    job["done"] = True


# ---------- Routen ----------

PAGE = """<!doctype html><html lang=de><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>EV Slider</title>
<style>
:root{--red:%(red)s}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;
padding:32px 20px;background:#fafafa;color:#1a1a1a;max-width:760px;margin-inline:auto}
h1{font-size:22px;font-weight:600;letter-spacing:.02em;margin:0 0 4px}
p.sub{color:#777;margin:0 0 28px;font-size:14px}
textarea{width:100%%;min-height:170px;padding:14px;border:1px solid #ddd;border-radius:8px;
font:14px/1.6 ui-monospace,monospace;resize:vertical}
label.chk{display:flex;align-items:center;gap:8px;margin:16px 0;font-size:14px}
button{background:var(--red);color:#fff;border:0;border-radius:8px;padding:13px 26px;
font-size:15px;font-weight:500;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
.item{background:#fff;border:1px solid #eee;border-radius:10px;padding:16px;margin-top:14px}
.item h3{margin:0 0 6px;font-size:15px;font-weight:600}
.meta{font-size:13px;color:#666}
.err{color:#b00020;font-size:13px}
.thumbs{display:flex;gap:8px;overflow-x:auto;margin-top:12px;padding-bottom:4px}
.thumbs img{height:150px;border-radius:6px;flex:0 0 auto}
.badge{display:inline-block;font-size:12px;padding:2px 9px;border-radius:99px;
background:#eee;color:#555}
.badge.ok{background:#e6f4ea;color:#1e6b34}.badge.err{background:#fdecec;color:#b00020}
</style>
<h1>Instagram-Slider aus Exposé-Links</h1>
<p class=sub>Ein Link pro Zeile. Die Posts werden automatisch auf Mo–Sa verteilt.</p>
<textarea id=urls placeholder="https://www.engelvoelkers.com/de/de/exposes/..."></textarea>
<label class=chk><input type=checkbox id=sched checked> In Metricool einplanen</label>
<button id=go>Slider erstellen</button>
<div id=out></div>
<script>
const go=document.getElementById('go'),out=document.getElementById('out');
go.onclick=async()=>{
  const urls=document.getElementById('urls').value.split('\\n').map(s=>s.trim()).filter(Boolean);
  if(!urls.length)return;
  go.disabled=true;go.textContent='Läuft…';out.innerHTML='';
  const fd=new FormData();fd.append('urls',urls.join('\\n'));
  fd.append('schedule',document.getElementById('sched').checked);
  const r=await fetch('/run',{method:'POST',body:fd});const {job_id}=await r.json();
  const poll=setInterval(async()=>{
    const j=await(await fetch('/status/'+job_id)).json();
    out.innerHTML=j.items.map(i=>`<div class=item>
      <h3>${i.title||i.url}</h3>
      <span class="badge ${i.status==='fertig'?'ok':i.status==='fehler'?'err':''}">${i.status}</span>
      ${i.scheduled?`<span class=meta> · geplant für ${i.scheduled}</span>`:''}
      ${i.error?`<div class=err>${i.error}</div>`:''}
      ${i.preview?`<div class=thumbs>${i.preview.map(p=>`<img src="${p}">`).join('')}</div>`:''}
      ${i.caption?`<details><summary class=meta>Posttext</summary>
        <pre style="white-space:pre-wrap;font:13px/1.6 inherit">${i.caption}</pre></details>`:''}
    </div>`).join('');
    if(j.done){clearInterval(poll);go.disabled=false;go.textContent='Slider erstellen';}
  },1500);
};
</script></html>"""


@app.get("/", response_class=HTMLResponse)
def index(user: str = Depends(auth)):
    return PAGE % {"red": cfg.get("brand.red", "#C8102E")}


@app.post("/run")
def run(urls: str = Form(...), schedule: str = Form("true"), user: str = Depends(auth)):
    url_list = [u.strip() for u in urls.splitlines() if u.strip().startswith("http")]
    if not url_list:
        raise HTTPException(400, "Keine gültigen Links")
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"items": [], "done": False}
    threading.Thread(target=run_job,
                     args=(job_id, url_list, schedule.lower() == "true"),
                     daemon=True).start()
    return {"job_id": job_id}


@app.get("/status/{job_id}")
def status_(job_id: str, user: str = Depends(auth)):
    if job_id not in JOBS:
        raise HTTPException(404, "Unbekannter Job")
    return JSONResponse(json.loads(json.dumps(JOBS[job_id], default=str)))
