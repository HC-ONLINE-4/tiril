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
SA_JSON = os.getenv("DRIVE_SERVICE_ACCOUNT_JSON", "").strip()
OAUTH_JSON = os.getenv("DRIVE_OAUTH_JSON", "").strip()
TIKTOK_COOKIES = os.getenv("TIKTOK_COOKIES", "").strip()

# Si no hay env var, intentar leer de cookies.json
if not TIKTOK_COOKIES:
    cookies_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.json")
    if os.path.exists(cookies_file):
        with open(cookies_file, "r", encoding="utf-8") as f:
            TIKTOK_COOKIES = f.read().strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
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
    msg = str(msg).replace(USERNAME, "usuario")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def mask_email(email: str) -> str:
    """Oculta el correo: solo primeros 2 chars y el dominio."""
    try:
        name, _, dom = email.partition("@")
        if not dom:
            return "***"
        return f"{name[:2]}***@{dom}"
    except Exception:
        return "***"


_tg_tasks = set()


async def telegram_send(text: str):
    """Envia un mensaje de Telegram (Bot API). Silencioso si no hay token/chat."""
    if not (TG_TOKEN and TG_CHAT_ID):
        return
    try:
        async with httpx.AsyncClient(timeout=10) as hc:
            r = await hc.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": text},
            )
            if r.status_code != 200:
                log(f"Telegram HTTP {r.status_code}: {r.text[:150]}")
    except Exception as e:
        log(f"Telegram error: {e}")


def telegram_notify(text: str):
    """Lanza la notificacion sin bloquear la grabacion (fire-and-forget)."""
    if not (TG_TOKEN and TG_CHAT_ID):
        return
    task = asyncio.get_running_loop().create_task(telegram_send(text))
    _tg_tasks.add(task)
    task.add_done_callback(_tg_tasks.discard)


def get_profile_stats():
    """Obtiene estadisticas del perfil (seguidores, videos, etc) via web scraping."""
    try:
        import re
        resp = httpx.get(
            f"https://www.tiktok.com/@{USERNAME}",
            headers={"User-Agent": UA},
            follow_redirects=True,
            timeout=15,
        )
        html = resp.text
        stats = {}
        for field in ['followerCount', 'followingCount', 'heartCount', 'videoCount']:
            match = re.search(f'"{field}"\\s*:\\s*(\\d+)', html)
            if match:
                stats[field] = int(match.group(1))
        return stats
    except Exception:
        return {}


def get_drive_service():
    from googleapiclient.discovery import build

    if OAUTH_JSON:
        # Subir como la cuenta de Google del usuario (15 GB, fiable).
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_info(json.loads(OAUTH_JSON))
        creds.refresh(Request())
    else:
        # Fallback: service account (solo lectura util; no puede crear archivos
        # en un Drive personal -> ver drive_setup.py para configurar OAuth).
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            json.loads(SA_JSON), scopes=["https://www.googleapis.com/auth/drive"]
        )
    return build("drive", "v3", credentials=creds)


def check_folder(svc):
    """Valida que se pueda ESCRIBIR en la carpeta DRIVE_FOLDER (crea y borra un archivo probe)."""
    try:
        who = svc.about().get(fields="user(emailAddress)").execute()
        log(f"Autenticado en Drive como: {mask_email(who.get('user', {}).get('emailAddress', ''))}")
    except Exception:
        pass

    try:
        from googleapiclient.http import MediaIoBaseUpload
        import io
        probe = svc.files().create(
            body={"name": "_probe.txt", "parents": [DRIVE_FOLDER], "mimeType": "text/plain"},
            media_body=MediaIoBaseUpload(io.BytesIO(b"ok"), "text/plain"),
        ).execute()
        svc.files().delete(fileId=probe["id"]).execute()
        log("Carpeta Drive OK: acceso de escritura confirmado")
        return True
    except Exception as e:
        log(f"ERROR: no se puede ESCRIBIR en la carpeta de Drive: {e}")
        telegram_notify(f"🔴 ERROR DRIVE\n@{USERNAME}\n{e}")
        if OAUTH_JSON:
            log("El token fue creado con la cuenta duena de la carpeta? "
                "(vuelve a correr: python drive_setup.py client_secret.json)")
        else:
            log("La service account no puede crear archivos en un Drive personal; "
                "configura OAuth con drive_setup.py.")
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
    # -movflags +faststart SOLO (sin frag_keyframe/empty_moov, que rompen la
    # reproduccion en algunos reproductores y Drive) y se toleran paquetes
    # corruptos (los descarta en vez de arruinar el video) con discardcorrupt.
    cmd = [
        FFMPEG, "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
        "-reconnect_at_eof", "1",
        "-fflags", "+discardcorrupt", "-err_detect", "ignore_err",
        "-i", url, "-t", str(limit_seconds),
        "-c", "copy", "-movflags", "+faststart",
        "-y", str(video),
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=limit_seconds + 120)
    except Exception as e:
        log(f"Error en ffmpeg: {e}")
        telegram_notify(f"🔴 ERROR FFMPEG\n@{USERNAME}\n{e}")
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
        telegram_notify(f"⚠️ ERROR STREAM URL\n@{USERNAME}\n{e}")
    return None


async def check_live(client) -> bool:
    """Verifica si el usuario esta en vivo con reintentos."""
    for attempt in range(3):
        try:
            result = await client.is_live()
            if result is None:
                log(f"[CHECK] is_live() devolvio None (intento {attempt + 1}/3)")
                await asyncio.sleep(2)
                continue
            return result
        except Exception as e:
            error_msg = str(e)
            log(f"Error verificando live (intento {attempt + 1}/3): {e}")
            if attempt < 2:
                await asyncio.sleep(3)
    telegram_notify(f"⚠️ ERROR VERIFICANDO LIVE\n@{USERNAME}\n3 intentos fallidos")
    return False


async def record_whole_live(client, state, svc, deadline) -> bool:
    """Conecta, graba TODO el live y lo sube al terminar. True si se grabo algo."""
    from chat_capture import ChatCapture

    cap = None
    pending = []
    uploaded = 0
    started_at = time.time()

    try:
        log("LIVE detectado! Conectando al WebSocket...")
        if not state.get("live_notified"):
            telegram_notify(f"LIVE DETECTADO\n@{USERNAME}\nConectando al WebSocket...")
            state["live_notified"] = True
        try:
            await client.start(fetch_room_info=True)
        except Exception as e:
            log(f"Error conectando: {e}")
            telegram_notify(f"🔴 ERROR WEBSOCKET\n@{USERNAME}\n{e}")
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
            if remaining <= 0:
                log("Sin tiempo restante en este job")
                break

            # Cuando el live cambia (entra un invitado, cortes, cambio de
            # calidad) TikTok invalida la URL del stream: se renueva en cada
            # segmento para no grabarla muerta.
            try:
                info = await client._web.fetch_room_info()
                if isinstance(info, dict):
                    fresh = extract_stream_url(info)
                    if fresh and fresh != stream_url:
                        log("Stream URL renovada (el live cambio)")
                        stream_url = fresh
            except Exception as e:
                log(f"No se pudo refrescar stream URL: {e}")

            name = base if seg == 1 else f"{base}_{seg}"
            log(f"Grabando segmento {seg} (hasta {remaining}s restantes)...")
            video = await asyncio.get_running_loop().run_in_executor(
                None, record_segment, stream_url, name, max(remaining, 60)
            )
            seg += 1

            if not (video.exists() and video.stat().st_size >= 100_000):
                video.unlink(missing_ok=True)
                log("Stream cortado o sin datos: se detiene la grabacion y se "
                    "reintentara con URL fresca")
                break

            # Subir el segmento YA MISMO: aunque el job muera despues,
            # lo grabado hasta aqui ya esta a salvo en Drive.
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

            if not await check_live(client):
                log("El live termino")
                state["live_notified"] = False
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
            dur = int(time.time() - started_at)
            log(f"Live completo subido ({uploaded} archivo(s) de video + chat)")
            telegram_notify(
                f"LIVE FINALIZADO\n@{USERNAME}\n"
                f"Duracion: {dur // 60}min {dur % 60}s\n"
                f"Subido a Drive: {uploaded} archivo(s)"
            )
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
    if not (USERNAME and DRIVE_FOLDER and (SA_JSON or OAUTH_JSON)):
        log("Faltan secrets: TIKTOK_USERNAME / DRIVE_FOLDER / y DRIVE_SERVICE_ACCOUNT_JSON "
            "o DRIVE_OAUTH_JSON")
        sys.exit(2)

    try:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=8) as hc:
            r = await hc.get("https://www.tiktok.com")
            if r.status_code != 200:
                log("Sin red valida, saliendo")
                return
    except Exception as e:
        log(f"Error de red: {e} - saliendo")
        telegram_notify(f"🔴 ERROR RED\n@{USERNAME}\n{e}")
        return

    # Autorizar sign server para sesiones autenticadas
    os.environ["WHITELIST_AUTHENTICATED_SESSION_ID_HOST"] = "api.eulerstream.com"

    from TikTokLive import TikTokLiveClient
    from TikTokLive.events import (
        CommentEvent, GiftEvent, JoinEvent, LikeEvent, FollowEvent,
    )

    session_ok = False
    session_id = ""
    session_idc = ""
    if TIKTOK_COOKIES:
        try:
            ck = json.loads(TIKTOK_COOKIES)
            session_id = ck.get("sessionid", "")
            session_idc = ck.get("tt_target_idc") or ck.get("tt-target-idc", "")
            if session_id:
                session_ok = True
                log(f"[LOGIN] Sesion TikTok aplicada OK (sessionid: ***{session_id[-4:]})")
                if session_idc:
                    log(f"[LOGIN] Centro de datos: {session_idc}")
            else:
                log("[LOGIN] TIKTOK_COOKIES presente pero sessionid VACIO - no hay sesion")
        except json.JSONDecodeError:
            log("[LOGIN] ERROR: TIKTOK_COOKIES no es JSON valido - revisa el formato")
        except Exception as e:
            log(f"[LOGIN] ERROR parseando TIKTOK_COOKIES: {e}")
    else:
        log("[LOGIN] Sin TIKTOK_COOKIES - solo podra ver lives publicos sin restriccion")

    client = TikTokLiveClient(unique_id=USERNAME)
    client.ignore_broken_payload = True

    # Aplicar sesion despues de crear el cliente (metodo oficial)
    # Limpiar cookies primero para evitar duplicado "tt-target-idc"
    if session_ok and session_id:
        client._web.cookies.clear()
        client.web.set_session(session_id, session_idc)

    # Verificar si la sesion funciona (test rapido)
    if session_ok:
        try:
            test = await client.is_live()
            if test is None:
                log("[LOGIN] WARNING: is_live() devolvio None - la sesion puede no estar funcionando")
            else:
                log(f"[LOGIN] Verificacion OK - acceso a TikTok funcional")
        except Exception as e:
            log(f"[LOGIN] WARNING: la sesion puede estar expirada: {e}")

    state = {"cap": None, "live_notified": False}

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
    drive_ok = check_folder(svc)
    if not drive_ok:
        sys.exit(1)
    cleanup_old(svc)

    deadline = time.time() + MAX_RUNTIME
    login_status = "SI" if session_ok else "NO"
    drive_status = "OK" if drive_ok else "FALLO"
    log(f"Monitor iniciado: chequeando a @{USERNAME} cada {POLL_SECONDS}s "
        f"durante {MAX_RUNTIME // 3600}h{MAX_RUNTIME % 3600 // 60}m")
    telegram_notify(
        f"MONITOR ACTIVO\n"
        f"@{USERNAME} cada {POLL_SECONDS}s\n"
        f"Vigilando hasta {MAX_RUNTIME // 3600}h{MAX_RUNTIME % 3600 // 60}m\n"
        f"Sesion TikTok: {login_status}\n"
        f"Drive: {drive_status}"
    )

    # Estadisticas
    stats = {
        "checks": 0,
        "live_detected": 0,
        "recordings": 0,
        "errors": 0,
        "http_errors": {},
    }

    last_heartbeat = time.time()
    while time.time() < deadline - SAFETY_MARGIN:
        stats["checks"] += 1
        try:
            is_live = await check_live(client)
        except Exception as e:
            stats["errors"] += 1
            error_key = str(e)[:50]
            stats["http_errors"][error_key] = stats["http_errors"].get(error_key, 0) + 1
            is_live = False

        if is_live:
            stats["live_detected"] += 1
            try:
                if await record_whole_live(client, state, svc, deadline):
                    stats["recordings"] += 1
                    cleanup_old(svc)
            except Exception as e:
                stats["errors"] += 1
                error_key = str(e)[:50]
                stats["http_errors"][error_key] = stats["http_errors"].get(error_key, 0) + 1
                log(f"Error inesperado grabando el live: {e} - continuando el monitor...")
                telegram_notify(f"🔴 ERROR GRABANDO\n@{USERNAME}\n{e}")
        else:
            state["live_notified"] = False
            await asyncio.sleep(POLL_SECONDS)

        # Heartbeat: avisar cada hora con estadisticas
        if time.time() - last_heartbeat >= 3600:
            remaining = int((deadline - time.time()) / 3600)
            
            # Obtener stats del perfil
            profile = get_profile_stats()
            videos_count = profile.get("videoCount", "?")
            followers = profile.get("followerCount", "?")
            likes = profile.get("heartCount", "?")
            
            errors_detail = ""
            if stats["http_errors"]:
                errors_detail = "\nErrores HTTP:\n"
                for err, count in sorted(stats["http_errors"].items(), key=lambda x: -x[1])[:5]:
                    errors_detail += f"  - {err}: {count}x\n"
            telegram_notify(
                f"✅ HEARTBEAT (1h)\n"
                f"@{USERNAME}\n"
                f"Tiempo restante: ~{remaining}h\n"
                f"---\n"
                f"Videos: {videos_count}\n"
                f"Seguidores: {followers}\n"
                f"Likes totales: {likes}\n"
                f"---\n"
                f"Chequeos: {stats['checks']}\n"
                f"Live detectados: {stats['live_detected']}\n"
                f"Grabaciones: {stats['recordings']}\n"
                f"Errores: {stats['errors']}"
                f"{errors_detail}"
            )
            last_heartbeat = time.time()

    log("Presupuesto de este run agotado: finalizando")
    
    # Obtener stats finales del perfil
    profile = get_profile_stats()
    videos_count = profile.get("videoCount", "?")
    followers = profile.get("followerCount", "?")
    likes = profile.get("heartCount", "?")
    
    errors_detail = ""
    if stats["http_errors"]:
        errors_detail = "\nErrores HTTP:\n"
        for err, count in sorted(stats["http_errors"].items(), key=lambda x: -x[1])[:5]:
            errors_detail += f"  - {err}: {count}x\n"
    telegram_notify(
        f"⏹️ MONITOR FINALIZADO\n"
        f"@{USERNAME}\n"
        f"Duracion: {MAX_RUNTIME // 3600}h{MAX_RUNTIME % 3600 // 60}m\n"
        f"---\n"
        f"Videos: {videos_count}\n"
        f"Seguidores: {followers}\n"
        f"Likes totales: {likes}\n"
        f"---\n"
        f"Chequeos: {stats['checks']}\n"
        f"Live detectados: {stats['live_detected']}\n"
        f"Grabaciones: {stats['recordings']}\n"
        f"Errores: {stats['errors']}"
        f"{errors_detail}"
        f"\nEl cron disparara el siguiente ciclo."
    )


if __name__ == "__main__":
    asyncio.run(main())
