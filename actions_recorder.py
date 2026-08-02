#!/usr/bin/env python3
"""
TikTok Live Recorder para GitHub Actions.
Sin tarjeta ni servidor propio: corre en los runners de GitHub y sube las
grabaciones a Google Drive (15 GB gratis) usando una service account.

El job vive continuamente hasta ~5h25m (limite del runner: 6h):
  - Chequea a TikTok cada 60 segundos (POLL_SECONDS).
  - Cuando detecta LIVE, graba EL LIVE COMPLETO en un solo archivo.
  - Sube cada segmento a Drive apenas termina (nada se pierde si el
    job muere) y al terminar el live sube el chat completo.
  - Si el runner llega al limite, sube lo grabado y el siguiente run
    (watchdog cada 5 min) retoma el resto del live.

Secrets requeridos en el repositorio:
  TIKTOK_USERNAME              - usuario de TikTok a grabar
  DRIVE_SERVICE_ACCOUNT_JSON   - JSON completo de la service account
  DRIVE_FOLDER                 - ID de la carpeta de Drive compartida con la SA

Variables del repositorio (opcionales):
  POLL_SECONDS=60              - cada cuanto verificar el live
  RETENTION_DAYS=14            - borra grabaciones mas antiguas que N dias
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx


def _env_int(name, default):
    """Lee un entero de variable de entorno; si falta o es invalido usa default."""
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


USERNAME = os.environ["TIKTOK_USERNAME"]
DRIVE_FOLDER = os.environ["DRIVE_FOLDER"]
SA_JSON = os.environ["DRIVE_SERVICE_ACCOUNT_JSON"]
POLL_SECONDS = _env_int("POLL_SECONDS", 60)
RETENTION_DAYS = _env_int("RETENTION_DAYS", 14)
MAX_RUNTIME = _env_int("MAX_RUNTIME", 19500)  # 5h25m: deja ~28 min al job (350) para subir el video final
SAFETY_MARGIN = 300  # no empezar grabaciones nuevas faltando <5 min para el limite
FFMPEG = "ffmpeg"
UPLOAD_DIR = Path("/tmp/upload")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

os.makedirs(UPLOAD_DIR, exist_ok=True)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def get_drive_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        json.loads(SA_JSON), scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)


def sa_email():
    """Devuelve el correo de la service account (para los mensajes de ayuda)."""
    try:
        return json.loads(SA_JSON).get("client_email", "")
    except Exception:
        return ""


def check_folder(svc):
    """Valida que la carpeta DRIVE_FOLDER exista y sea accesible por la SA."""
    try:
        meta = svc.files().get(fileId=DRIVE_FOLDER, fields="id,name,trashed").execute()
        log(f"Carpeta Drive OK: '{meta.get('name')}' ({meta.get('id')})")
        return True
    except Exception as e:
        log("ERROR: la service account NO puede acceder a la carpeta de Drive.")
        log(f"Detalle: {e}")
        log("Solucion: abre la carpeta en drive.google.com -> Compartir -> agrega este correo")
        log(f"  {sa_email()}")
        log("  con permiso EDITOR, y verifica que el secret DRIVE_FOLDER sea solo el ID")
        log(f"  ({DRIVE_FOLDER}) sin URL ni espacios.")
        return False


def upload_file(svc, path: Path) -> bool:
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(str(path), resumable=True, chunksize=8 * 1024 * 1024)
    meta = {"name": path.name, "parents": [DRIVE_FOLDER]}
    resp = svc.files().create(body=meta, media_body=media, fields="id,name,size").execute()
    log(f"Subido a Drive: {resp['name']} ({resp.get('size', '?')} bytes)")
    return True


async def upload_with_retry(svc, path: Path, attempts: int = 5) -> bool:
    """Sube un archivo con reintentos y espera progresiva (fallos de red transitorios)."""
    for i in range(1, attempts + 1):
        try:
            return upload_file(svc, path)
        except Exception as e:
            log(f"Error subiendo {path.name} (intento {i}/{attempts}): {e}")
            if i < attempts:
                await asyncio.sleep(10 * i)
    log(f"Rendido: no se pudo subir {path.name}")
    return False


def cleanup_old(svc):
    """Borra de la carpeta los archivos con mas de RETENTION_DAYS."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        q = (f"'{DRIVE_FOLDER}' in parents and trashed=false "
             f"and createdTime < '{cutoff.isoformat(timespec='seconds')}'")
        files = svc.files().list(q=q, fields="files(id,name)", pageSize=100).execute().get("files", [])
        for f in files:
            svc.files().delete(fileId=f["id"]).execute()
            log(f"Limpieza: borrado {f['name']}")
        if files:
            log(f"Limpieza: {len(files)} archivo(s) eliminado(s) de Drive")
    except Exception as e:
        log(f"Error en limpieza de Drive: {e}")


def record_segment(url, name, limit_seconds):
    """
    Graba el stream hasta que termine o hasta limit_seconds.
    Devuelve la ruta del archivo y True si el stream terminó solo.
    """
    video = UPLOAD_DIR / f"{name}.mp4"
    cmd = [
        FFMPEG, "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
        "-i", url, "-t", str(limit_seconds),
        "-c", "copy", "-movflags", "+faststart+frag_keyframe+empty_moov",
        "-y", str(video),
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=limit_seconds + 120)
    except Exception as e:
        log(f"Error en ffmpeg: {e}")
    return video


def extract_stream_url(room_info):
    try:
        raw = (room_info.get("stream_url", {})
               .get("live_core_sdk_data", {})
               .get("pull_data", {})
               .get("stream_data", "{}"))
        data = json.loads(raw).get("data", {})
        for q in ("origin", "uhd", "hd", "sd", "ld"):
            if q in data and data[q].get("main", {}).get("flv"):
                log(f"Calidad: {q}")
                return data[q]["main"]["flv"]
        if data:
            first = next(iter(data))
            url = data[first].get("main", {}).get("flv")
            if url:
                log(f"Calidad por defecto: {first}")
                return url
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
        log(f"Error extrayendo stream URL: {e}")
    return None


async def check_live(client) -> bool:
    try:
        return await client.is_live()
    except Exception as e:
        log(f"Error verificando live: {e}")
        return False


async def record_whole_live(client, state, svc, deadline) -> bool:
    """Conecta, graba TODO el live y lo sube al terminar. True si se grabo algo."""
    from chat_capture import ChatCapture

    cap = None
    pending = []
    uploaded = 0

    try:
        log("LIVE detectado! Conectando al WebSocket...")
        try:
            await client.start(fetch_room_info=True)
        except Exception as e:
            log(f"Error conectando: {e}")
            return False

        title = "Sin titulo"
        stream_url = None
        if client.room_info and isinstance(client.room_info, dict):
            title = client.room_info.get("title", title)
            stream_url = extract_stream_url(client.room_info)
        if not stream_url:
            try:
                info = await client._web.fetch_room_info()
                if isinstance(info, dict):
                    title = info.get("title", title)
                    stream_url = extract_stream_url(info)
            except Exception as e:
                log(f"Fallback fetch_room_info fallo: {e}")

        if not stream_url:
            log("No se encontro stream URL")
            return False

        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_")[:50].strip()
        base = f"{USERNAME}_{ts}_{safe_title}" if safe_title else f"{USERNAME}_{ts}"

        cap = ChatCapture(USERNAME, str(UPLOAD_DIR))
        cap.start()
        state["cap"] = cap

        seg = 1
        while True:
            remaining = int(deadline - time.time())
            name = base if seg == 1 else f"{base}_{seg}"
            log(f"Grabando segmento {seg} (hasta {remaining}s restantes)...")
            video = await asyncio.get_running_loop().run_in_executor(
                None, record_segment, stream_url, name, max(remaining, 60)
            )
            seg += 1

            # Subir el segmento YA MISMO: aunque el job muera despues,
            # lo grabado hasta aqui ya esta a salvo en Drive.
            if video.exists() and video.stat().st_size >= 100_000:
                if await upload_with_retry(svc, video):
                    uploaded += 1
                    snap = UPLOAD_DIR / f"{name}_chat.json"
                    cap.save(filename=snap.name)
                    if snap.exists():
                        await upload_with_retry(svc, snap, attempts=3)
                        snap.unlink()
                    video.unlink(missing_ok=True)
                else:
                    # No se pudo subir ahora: se guarda y se reintenta al final
                    pending.append(video)
            elif video.exists():
                log(f"Segmento vacio o muy pequeno, se descarta: {video.name}")
                video.unlink()

            if not await check_live(client):
                log("El live termino")
                break
            if time.time() >= deadline - SAFETY_MARGIN:
                log("Presupuesto del runner agotado con el live activo: subiendo lo ultimo...")
                break

        # Reintentar subidas pendientes con el tiempo que quede
        for p in pending:
            if p.exists() and p.stat().st_size >= 100_000:
                if await upload_with_retry(svc, p):
                    uploaded += 1
            p.unlink(missing_ok=True)

        # Chat completo del live
        chat_path = UPLOAD_DIR / f"{base}_chat.json"
        if cap:
            cap.save(filename=chat_path.name)
        if chat_path.exists():
            await upload_with_retry(svc, chat_path)
            chat_path.unlink()

        if uploaded:
            log(f"Live completo subido ({uploaded} archivo(s) de video + chat)")
            return True
        return False

    finally:
        state["cap"] = None
        if cap:
            try:
                cap.stop()
            except Exception:
                pass
        try:
            await client.disconnect()
        except Exception:
            pass


async def main():
    if not (USERNAME and DRIVE_FOLDER and SA_JSON):
        log("Faltan secrets: TIKTOK_USERNAME / DRIVE_FOLDER / DRIVE_SERVICE_ACCOUNT_JSON")
        sys.exit(2)

    try:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=8) as hc:
            r = await hc.get("https://www.tiktok.com")
            if r.status_code != 200:
                log("Sin red valida, saliendo")
                return
    except Exception as e:
        log(f"Error de red: {e} - saliendo")
        return

    from TikTokLive import TikTokLiveClient
    from TikTokLive.events import (
        CommentEvent, GiftEvent, JoinEvent, LikeEvent, FollowEvent,
    )

    client = TikTokLiveClient(unique_id=USERNAME)
    client.ignore_broken_payload = True

    state = {"cap": None}

    @client.on(CommentEvent)
    async def on_comment(event):
        if state["cap"]:
            state["cap"].add_comment(event)

    @client.on(GiftEvent)
    async def on_gift(event):
        if state["cap"]:
            state["cap"].add_gift(event)

    @client.on(JoinEvent)
    async def on_join(event):
        if state["cap"]:
            state["cap"].add_join(event)

    @client.on(LikeEvent)
    async def on_like(event):
        if state["cap"]:
            state["cap"].add_like(event)

    @client.on(FollowEvent)
    async def on_follow(event):
        if state["cap"]:
            state["cap"].add_follow(event)

    svc = get_drive_service()
    if not check_folder(svc):
        sys.exit(1)
    cleanup_old(svc)

    deadline = time.time() + MAX_RUNTIME
    log(f"Monitor iniciado: chequeando a @{USERNAME} cada {POLL_SECONDS}s "
        f"durante {MAX_RUNTIME // 3600}h{MAX_RUNTIME % 3600 // 60}m")

    while time.time() < deadline - SAFETY_MARGIN:
        if await check_live(client):
            try:
                if await record_whole_live(client, state, svc, deadline):
                    cleanup_old(svc)
            except Exception as e:
                log(f"Error inesperado grabando el live: {e} - continuando el monitor...")
        else:
            await asyncio.sleep(POLL_SECONDS)

    log("Presupuesto de este run agotado: finalizando (el cron de respaldo reinicia)")


if __name__ == "__main__":
    asyncio.run(main())
