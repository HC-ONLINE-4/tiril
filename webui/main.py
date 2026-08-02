#!/usr/bin/env python3
"""
TikTok Live Recorder - Interfaz web privada (solo para ti)
FastAPI + login por cookie httpOnly. Lista, reproduce y descarga grabaciones + chat.
Credenciales via variables de entorno (WEB_USERNAME, WEB_PASSWORD, SECRET_KEY).
"""

import json
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(BASE_DIR / ".env")

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR / "grabaciones")))
WEB_USERNAME = os.getenv("WEB_USERNAME", "admin")
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=False)

VIDEO_EXTS = (".mp4", ".flv", ".ts")


# ---------- Autenticacion ----------
def _is_authed(request: Request) -> bool:
    return request.session.get("user") == WEB_USERNAME


def _authed_or_redirect(request: Request) -> HTMLResponse | None:
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    return None


# ---------- Utilidades ----------
def _list_recordings():
    """Escanea la carpeta de grabaciones agrupando video + chat por base."""
    if not OUTPUT_DIR.is_dir():
        return []
    recordings = {}
    for f in os.listdir(OUTPUT_DIR):
        path = OUTPUT_DIR / f
        if not path.is_file():
            continue
        if f.endswith(VIDEO_EXTS):
            base = f.rsplit(".", 1)[0]
            rec = recordings.setdefault(base, {"video": None, "chat": None})
            rec["video"] = f
        elif f.endswith(".json") and "_chat" in f:
            base = f.replace("_chat.json", "")
            rec = recordings.setdefault(base, {"video": None, "chat": None})
            rec["chat"] = f

    items = []
    for base, rec in sorted(recordings.items(), reverse=True):
        item = {"base": base, "video": rec["video"], "chat": rec["chat"], "size_mb": 0, "mtime": 0}
        if rec["video"]:
            p = OUTPUT_DIR / rec["video"]
            item["size_mb"] = round(p.stat().st_size / (1024 * 1024), 1)
            item["mtime"] = p.stat().st_mtime
        items.append(item)
    return items


def _safe_filename(name: str) -> str:
    """Evita path traversal: solo permite el nombre base dentro de grabaciones."""
    name = os.path.basename(name)
    if not name or ".." in name:
        raise ValueError("Nombre invalido")
    return name


# ---------- Paginas ----------
LOGIN_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Acceso - TikTok Recorder</title>
<style>
body{margin:0;font-family:'Segoe UI',Arial,sans-serif;background:#1a1a2e;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#0f0f23;border-radius:12px;padding:40px;width:320px;text-align:center;border:1px solid #333}
h1{color:#e94560;font-size:20px;margin:0 0 4px}
p{color:#888;font-size:13px;margin:0 0 24px}
input{width:100%;box-sizing:border-box;padding:12px;margin-bottom:12px;border-radius:6px;border:1px solid #333;background:#1a1a2e;color:#eee;font-size:14px}
button{width:100%;padding:12px;border:none;border-radius:6px;background:#e94560;color:#fff;font-size:14px;font-weight:bold;cursor:pointer}
button:hover{background:#f0627a}
.error{color:#e74c3c;font-size:13px;margin-bottom:12px}
</style></head><body>
<div class="card">
<h1>TikTok Live Recorder</h1>
<p>Acceso privado</p>
<form method="post" action="/login">
<input type="text" name="username" placeholder="Usuario" autocomplete="username" required autofocus>
<input type="password" name="password" placeholder="Contraseña" autocomplete="current-password" required>
<button type="submit">Entrar</button>
</form>
</div></body></html>"""

INDEX_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Grabaciones - TikTok Recorder</title>
<style>
body{margin:0;font-family:'Segoe UI',Arial,sans-serif;background:#1a1a2e;color:#eee}
header{background:#e94560;padding:14px 24px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:17px;margin:0}
header a{color:#fff;text-decoration:none;font-size:13px}
main{padding:20px 24px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:#888;padding:8px;border-bottom:1px solid #333;font-size:12px;text-transform:uppercase}
td{padding:10px 8px;border-bottom:1px solid #222;vertical-align:middle}
.badge{display:inline-block;font-size:11px;font-weight:bold;padding:2px 8px;border-radius:10px;margin-right:6px}
.b-v{background:#27ae60;color:#fff}.b-c{background:#3498db;color:#fff}
.actions a{margin-right:8px;color:#3498db;text-decoration:none;font-size:13px}
.actions a.del{color:#e74c3c}
.empty{color:#666;text-align:center;padding:40px}
.mono{font-family:Consolas,monospace;font-size:13px;color:#00ff41}
</style></head><body>
<header><h1>TikTok Live Recorder</h1><a href="/logout">Salir</a></header>
<main>
<table>
<tr><th>Fecha</th><th>Grabacion</th><th>Tamaño</th><th>Contenido</th><th>Acciones</th></tr>
__ROWS__
</table>
</main></body></html>"""

ROW_HTML = """<tr>
<td class="mono">{fecha}</td>
<td>{base}</td>
<td>{size}</td>
<td>{badges}</td>
<td class="actions">{links}</td>
</tr>"""

PLAYER_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{base} - TikTok Recorder</title>
<style>
body{margin:0;font-family:'Segoe UI',Arial,sans-serif;background:#1a1a2e;color:#eee;display:flex;flex-direction:column;height:100vh}
header{background:#e94560;padding:12px 24px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:15px;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
header a{color:#fff;text-decoration:none;font-size:13px}
.wrap{display:flex;flex:1;min-height:0}
.video-col{flex:1;background:#000;display:flex;align-items:center;justify-content:center;min-width:0}
video{width:100%;height:100%;max-height:100%}
.chat-col{width:340px;background:#0f0f23;display:flex;flex-direction:column;border-left:1px solid #222}
.chat-head{padding:10px 14px;background:#16213e;font-size:13px;font-weight:bold}
#chatbox{flex:1;overflow-y:auto;padding:8px 12px;font-size:13px}
.msg{margin:4px 0;line-height:1.35}
.ts{color:#666;font-size:11px;font-family:Consolas,monospace}
.uname{font-weight:bold}
.text-comment{color:#ecf0f1}.text-gift{color:#f39c12}.text-join{color:#27ae60}.text-like{color:#e74c3c}.text-follow{color:#9b59b6}
</style></head><body>
<header><h1>{base}</h1><a href="/">← Volver</a></header>
<div class="wrap">
<div class="video-col"><video id="v" controls autoplay preload="metadata"></video></div>
<div class="chat-col">
<div class="chat-head" id="chatcount">Chat del live</div>
<div id="chatbox"></div>
</div>
</div>
<script>
const BASE={base_js};
const video=document.getElementById('v');
const chatbox=document.getElementById('chatbox');
let messages=[];
let shown=0;
let colors={};
const PALETTE=['#ff6b6b','#4ecdc4','#45b7d1','#96ceb4','#ffeaa7','#dda0dd','#98d8c8','#f7dc6f','#bb8fce','#85c1e9','#f8c291','#82e0aa','#f1948a','#85929e','#73c6b6'];
function colorFor(u){if(!colors[u])colors[u]=PALETTE[Object.keys(colors).length%PALETTE.length];return colors[u]}
function fmt(s){s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=s%60;return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(x).padStart(2,'0')}
function addMsg(m){const div=document.createElement('div');div.className='msg';
const ts=document.createElement('span');ts.className='ts';ts.textContent='['+fmt(m.timestamp)+'] ';
const un=document.createElement('span');un.className='uname';un.style.color=colorFor(m.user);un.textContent=(m.is_super_fan?'*':'')+m.user+': ';
const tx=document.createElement('span');tx.className='text-'+(m.type||'comment');tx.textContent=m.text;
div.appendChild(ts);div.appendChild(un);div.appendChild(tx);chatbox.appendChild(div);}
function showUpTo(t){while(shown<messages.length&&(messages[shown].timestamp||0)<=t){addMsg(messages[shown]);shown++}
chatbox.scrollTop=chatbox.scrollHeight;}
video.addEventListener('timeupdate',()=>showUpTo(video.currentTime));
video.addEventListener('loadedmetadata',()=>{document.getElementById('chatcount').textContent=messages.length+' mensajes';showUpTo(video.currentTime)});
video.src='/media/'+encodeURIComponent(BASE+'.mp4');
fetch('/chat/'+encodeURIComponent(BASE+'_chat.json')).then(r=>r.ok?r.json():null).then(d=>{if(d){messages=d.messages||[];document.getElementById('chatcount').textContent=messages.length+' mensajes';showUpTo(video.currentTime||0)}}).catch(()=>{});
</script></body></html>"""


# ---------- Rutas ----------
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _is_authed(request):
        return RedirectResponse("/", status_code=303)
    return LOGIN_HTML


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == WEB_USERNAME and password == WEB_PASSWORD:
        request.session["user"] = username
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(LOGIN_HTML.replace("<h1>TikTok Live Recorder</h1>",
        '<h1>TikTok Live Recorder</h1><div class="error">Credenciales incorrectas</div>'), status_code=401)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    redir = _authed_or_redirect(request)
    if redir:
        return redir
    items = _list_recordings()
    if not items:
        rows = '<tr><td colspan="5" class="empty">Aun no hay grabaciones. El servidor monitorea 24/7 y graba automaticamente cuando el live inicia.</td></tr>'
    else:
        rows = ""
        for it in items:
            badges = ""
            badges += '<span class="badge b-v">Video</span>' if it["video"] else '<span class="badge b-v" style="background:#555">Sin video</span>'
            badges += '<span class="badge b-c">Chat</span>' if it["chat"] else '<span class="badge b-c" style="background:#555">Sin chat</span>'
            fecha = ""
            if it["mtime"]:
                import datetime as _dt
                fecha = _dt.datetime.fromtimestamp(it["mtime"]).strftime("%Y-%m-%d %H:%M")
            links = ""
            if it["video"]:
                links += f'<a href="/play?base={it["base"]}">Ver</a>'
                links += f'<a href="/media/{it["video"]}" download>Descargar</a>'
            else:
                links += '<span style="color:#555">-</span>'
            if it["chat"]:
                links += f'<a href="/chat/{it["chat"]}" target="_blank">Chat JSON</a>'
            rows += ROW_HTML.format(
                fecha=fecha,
                base=it["base"],
                size=f'{it["size_mb"]} MB' if it["video"] else "-",
                badges=badges,
                links=links,
            )
    return INDEX_HTML.replace("__ROWS__", rows)


@app.get("/play", response_class=HTMLResponse)
async def play(request: Request, base: str = ""):
    redir = _authed_or_redirect(request)
    if redir:
        return redir
    base = _safe_filename(base)
    return PLAYER_HTML.replace("{base}", base).replace("{base_js}", json.dumps(base))


@app.get("/api/recordings")
async def api_recordings(request: Request):
    redir = _authed_or_redirect(request)
    if redir:
        return redir
    return JSONResponse(_list_recordings())


@app.get("/media/{name}")
async def media(request: Request, name: str):
    redir = _authed_or_redirect(request)
    if redir:
        return redir
    name = _safe_filename(name)
    path = OUTPUT_DIR / name
    if not path.is_file():
        return JSONResponse({"error": "No encontrado"}, status_code=404)
    return FileResponse(path, media_type="video/mp4" if path.suffix == ".mp4" else "application/octet-stream")


@app.get("/chat/{name}")
async def chat_file(request: Request, name: str):
    redir = _authed_or_redirect(request)
    if redir:
        return redir
    name = _safe_filename(name)
    path = OUTPUT_DIR / name
    if not path.is_file():
        return JSONResponse({"error": "No encontrado"}, status_code=404)
    return FileResponse(path, media_type="application/json", filename=name)
