#!/usr/bin/env python3
"""
TikTok Live Recorder - Version headless para servidor (24/7)
Adaptado de app.py (version de escritorio con GUI, que se mantiene intacta).
Usa Playwright (cookies) + TikTokLive (WebSocket) + ffmpeg + captura de chat.
Configuracion via variables de entorno (ver .env.example).
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from chat_capture import ChatCaptureManager

# === Configuracion (variables de entorno) ===
BASE_DIR = Path(__file__).resolve().parent
if load_dotenv:
    load_dotenv(BASE_DIR / ".env")

USERNAME = os.getenv("TIKTOK_USERNAME", "").strip()
if not USERNAME:
    logger.error("Falta TIKTOK_USERNAME (variable de entorno). El proceso se detiene.")
    sys.exit(1)
OUTPUT_DIR = os.getenv("OUTPUT_DIR", str(BASE_DIR / "grabaciones"))
PROFILE_DIR = os.getenv("PROFILE_DIR", str(BASE_DIR / "browser_profile"))
LOG_DIR = os.getenv("LOG_DIR", str(BASE_DIR / "logs"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "30"))
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() in ("1", "true", "yes")
LIVE_URL = f"https://www.tiktok.com/@{USERNAME}/live"

# === Logging: consola + archivo con rotacion ===
os.makedirs(LOG_DIR, exist_ok=True)
logger = logging.getLogger("tiktok-recorder")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

_console = logging.StreamHandler()
_console.setFormatter(_fmt)
logger.addHandler(_console)

_file = RotatingFileHandler(
    os.path.join(LOG_DIR, "recorder.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file.setFormatter(_fmt)
logger.addHandler(_file)


class HeadlessRecorder:
    def __init__(self):
        self.running = False
        self.recording = False
        self.ffmpeg_process = None
        self.current_file = None
        self.start_time = None
        self.loop = None
        self.client = None
        self._last_cleanup = None

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(PROFILE_DIR, exist_ok=True)

    # ---------- FASE 1: bootstrap con Playwright ----------
    def bootstrap_cookies(self) -> bool:
        """Abre TikTok una vez con Chromium para obtener cookies persistentes."""
        logger.info("FASE 1: Bootstrap - abriendo Chromium para obtener cookies...")
        context = None
        pw = None
        try:
            from playwright.sync_api import sync_playwright

            args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            if sys.platform != "win32":
                args.append("--no-sandbox")

            pw = sync_playwright().start()
            context = pw.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=PLAYWRIGHT_HEADLESS,
                args=args,
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/New_York",
            )

            page = context.pages[0] if context.pages else context.new_page()
            logger.info("Navegando a TikTok...")
            page.goto(LIVE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            cookies = context.cookies()
            cookie_names = [c["name"] for c in cookies]
            logger.info(f"Cookies obtenidas: {len(cookies)} ({', '.join(cookie_names[:5])}...)")

            context.close()
            context = None
            pw.stop()
            pw = None
            logger.info("Bootstrap completado. Chromium cerrado.")
            return True

        except Exception as e:
            logger.error(f"Error en bootstrap: {e}")
            try:
                if context:
                    context.close()
                if pw:
                    pw.stop()
            except Exception:
                pass
            return False

    # ---------- Grabacion con ffmpeg (bloqueante, en executor) ----------
    def record_stream(self, stream_url, title):
        self.recording = True
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_")[:50].strip()
        filename = f"{USERNAME}_{timestamp}_{safe_title}.mp4" if safe_title else f"{USERNAME}_{timestamp}.mp4"
        filepath = os.path.join(OUTPUT_DIR, filename)
        self.current_file = filepath
        self.start_time = time.time()

        logger.info(f"[REC] Archivo: {filename}")
        logger.info(f"[REC] Titulo: {title}")
        logger.info(f"[REC] URL: {stream_url[:80]}...")

        cmd = [
            FFMPEG_PATH,
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "10",
            "-i", stream_url,
            "-c", "copy",
            "-movflags", "+faststart+frag_keyframe+empty_moov",
            "-y", filepath,
        ]

        logger.info("[REC] Ejecutando ffmpeg...")
        try:
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.ffmpeg_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
            logger.info(f"[REC] ffmpeg PID: {self.ffmpeg_process.pid} - grabando...")
            self.ffmpeg_process.wait()
            logger.info("[REC] ffmpeg termino")
        except Exception as e:
            logger.error(f"[REC] Error ffmpeg: {e}")
        finally:
            self.recording = False
            self.ffmpeg_process = None
            self.current_file = None
            self.start_time = None

            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                size_mb = os.path.getsize(filepath) / (1024 * 1024)
                logger.info(f"[REC] Guardado: {filename} ({size_mb:.1f} MB)")
            else:
                logger.info("[REC] Grabacion vacia, eliminando...")
                if os.path.exists(filepath):
                    os.remove(filepath)

    def stop_ffmpeg(self):
        """Detiene ffmpeg si esta corriendo."""
        if self.ffmpeg_process:
            logger.info("Deteniendo grabacion...")
            try:
                if self.ffmpeg_process.stdin and not self.ffmpeg_process.stdin.closed:
                    self.ffmpeg_process.stdin.write(b'q')
                    self.ffmpeg_process.stdin.flush()
                    self.ffmpeg_process.stdin.close()
                self.ffmpeg_process.wait(timeout=15)
            except Exception:
                try:
                    self.ffmpeg_process.kill()
                    self.ffmpeg_process.wait(timeout=5)
                except Exception:
                    pass
            self.ffmpeg_process = None
            self.recording = False
            self.current_file = None
            self.start_time = None

    # ---------- Extraccion de stream URL ----------
    @staticmethod
    def _extract_stream_url(room_info):
        """Extrae la URL del stream (FLV) desde room_info."""
        try:
            stream_data_raw = (
                room_info.get("stream_url", {})
                .get("live_core_sdk_data", {})
                .get("pull_data", {})
                .get("stream_data", "{}")
            )
            stream_data = json.loads(stream_data_raw)
            streams = stream_data.get("data", {})

            preferred = ["origin", "uhd", "hd", "sd", "ld"]
            for quality in preferred:
                if quality in streams:
                    url = streams[quality].get("main", {}).get("flv")
                    if url:
                        logger.info(f"Calidad: {quality}")
                        return url

            if streams:
                first = next(iter(streams))
                url = streams[first].get("main", {}).get("flv")
                if url:
                    logger.info(f"Calidad por defecto: {first}")
                    return url
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"Error extrayendo stream URL: {e}")
        return None

    # ---------- Limpieza de grabaciones viejas ----------
    def cleanup_old_recordings(self):
        """Borra grabaciones y chats mas antiguos que RETENTION_DAYS."""
        cutoff = time.time() - RETENTION_DAYS * 86400
        removed = 0
        try:
            for f in os.listdir(OUTPUT_DIR):
                path = os.path.join(OUTPUT_DIR, f)
                try:
                    if (
                        os.path.isfile(path)
                        and os.path.getmtime(path) < cutoff
                        and f.endswith((".mp4", ".flv", ".ts", ".json"))
                    ):
                        os.remove(path)
                        removed += 1
                except OSError:
                    continue
        except OSError as e:
            logger.error(f"[LIMPIEZA] Error: {e}")
        if removed:
            logger.info(f"[LIMPIEZA] Eliminados {removed} archivos de mas de {RETENTION_DAYS} dias")

    # ---------- FASE 2+3: monitoreo via TikTokLive ----------
    async def async_monitor(self):
        import httpx
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import (
            CommentEvent,
            GiftEvent,
            JoinEvent,
            LikeEvent,
            FollowEvent,
            ConnectEvent,
            DisconnectEvent,
            LiveEndEvent,
        )

        self.client = TikTokLiveClient(unique_id=USERNAME)
        client = self.client
        client.ignore_broken_payload = True
        chat_manager = ChatCaptureManager.get_instance()

        @client.on(ConnectEvent)
        async def on_connect(event: ConnectEvent):
            logger.info(f"Conectado al live de @{USERNAME}")

        @client.on(CommentEvent)
        async def on_comment(event: CommentEvent):
            capture = chat_manager.get_capture()
            if capture:
                capture.add_comment(event)
                logger.info(f"[CHAT] {event.user.nickname}: {event.comment}")

        @client.on(GiftEvent)
        async def on_gift(event: GiftEvent):
            capture = chat_manager.get_capture()
            if capture:
                capture.add_gift(event)
                count = event.repeat_count if hasattr(event, 'repeat_count') else 1
                count_text = f" x{count}" if count > 1 else ""
                logger.info(f"[GIFT] {event.user.nickname}: {event.gift.name}{count_text}")

        @client.on(JoinEvent)
        async def on_join(event: JoinEvent):
            capture = chat_manager.get_capture()
            if capture:
                capture.add_join(event)

        @client.on(LikeEvent)
        async def on_like(event: LikeEvent):
            capture = chat_manager.get_capture()
            if capture:
                capture.add_like(event)

        @client.on(FollowEvent)
        async def on_follow(event: FollowEvent):
            capture = chat_manager.get_capture()
            if capture:
                capture.add_follow(event)
                logger.info(f"[FOLLOW] {event.user.nickname} siguio al creador")

        @client.on(DisconnectEvent)
        async def on_disconnect(event: DisconnectEvent):
            logger.info("Desconectado del WebSocket")

        @client.on(LiveEndEvent)
        async def on_live_end(event: LiveEndEvent):
            logger.info("Live finalizado - volviendo a monitorear")

        http_client = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            timeout=8,
        )

        network_was_down = False

        while self.running:
            # 1. Limpieza diaria
            today = datetime.now().date()
            if self._last_cleanup != today:
                self._last_cleanup = today
                self.cleanup_old_recordings()

            # 2. Verificar conexion de red
            network_ok = await self._check_network(http_client)

            if not network_ok:
                if not network_was_down:
                    logger.warning("SIN CONEXION a internet. Reintentando cada 5s...")
                    network_was_down = True
                await asyncio.sleep(5)
                continue

            if network_was_down:
                logger.info("Conexion RESTAURADA. Retomando monitoreo...")
                network_was_down = False

            # 3. Verificar si el live esta activo
            logger.info(f"Verificando @{USERNAME}...")
            try:
                is_live = await client.is_live()
            except Exception as e:
                logger.error(f"Error al verificar live: {e} - reintentando en 3s...")
                await asyncio.sleep(3)
                continue

            if not is_live:
                logger.info(f"Offline. Reintentando en {POLL_INTERVAL}s...")
                for _ in range(POLL_INTERVAL):
                    if not self.running:
                        break
                    await asyncio.sleep(1)
                continue

            # 4. Live detectado
            logger.warning("LIVE detectado! Conectando al WebSocket...")

            try:
                await client.start(fetch_room_info=True)

                stream_url = None
                title = "Sin titulo"

                if hasattr(client, 'room_id'):
                    logger.info(f"Room ID: {client.room_id}")

                if client.room_info:
                    room = client.room_info
                    if isinstance(room, dict):
                        title = room.get("title", "Sin titulo")
                        stream_url = self._extract_stream_url(room)

                if not stream_url:
                    try:
                        room_info = await client._web.fetch_room_info()
                        if room_info:
                            logger.info("Usando fetch_room_info como fallback")
                            if isinstance(room_info, dict):
                                title = room_info.get("title", title)
                                stream_url = self._extract_stream_url(room_info)
                    except Exception as e:
                        logger.error(f"Error con fetch_room_info: {e}")

                if not stream_url:
                    logger.info("No se encontro stream URL. Reintentando en 10s...")
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    await asyncio.sleep(10)
                    continue

                logger.info(f"Stream URL OK. Titulo: {title}")

            except Exception as e:
                logger.error(f"Error al conectar: {e}")
                try:
                    await client.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(5)
                continue

            # 5. Iniciar captura de chat
            chat_manager.start_capture(USERNAME, OUTPUT_DIR)
            logger.info("Captura de chat iniciada")

            # 6. Iniciar grabacion (bloqueante en executor; el WS sigue capturando chat)
            logger.info("Iniciando grabacion...")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.record_stream, stream_url, title)

            # 7. Guardar chat al terminar la grabacion
            chat_filepath = chat_manager.stop_capture()
            if chat_filepath:
                logger.info(f"Chat guardado: {os.path.basename(chat_filepath)}")
            else:
                logger.info("No se capturaron mensajes de chat")

            try:
                await client.disconnect()
            except Exception:
                pass

            logger.info("Stream finalizado. Volviendo a monitorear...")
            await asyncio.sleep(2)

        await http_client.aclose()
        logger.info("Monitoreo detenido")

    async def _check_network(self, http_client):
        """Verifica que la red esta activa haciendo una prueba rapida."""
        try:
            resp = await http_client.get("https://www.tiktok.com", timeout=8)
            return resp.status_code == 200
        except Exception:
            return False

    # ---------- Arranque / detencion ----------
    def run(self):
        self.running = True
        logger.info("=" * 50)
        logger.info(f"Iniciando grabador headless 24/7 - @{USERNAME}")
        logger.info(f"Grabaciones: {OUTPUT_DIR}")
        logger.info(f"Intervalo de verificacion: {POLL_INTERVAL}s | Retencion: {RETENTION_DAYS} dias")

        bootstrap_ok = self.bootstrap_cookies()
        if not bootstrap_ok:
            logger.warning("Bootstrap fallo, continuando sin cookies persistentes...")

        logger.info("Iniciando monitoreo WebSocket...")
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self.loop.add_signal_handler(sig, self.stop)
            except (NotImplementedError, RuntimeError):
                pass

        try:
            self.loop.run_until_complete(self.async_monitor())
        except Exception as e:
            logger.error(f"Error fatal en el loop: {e}", exc_info=True)
        finally:
            try:
                self.loop.close()
            except Exception:
                pass
        logger.info("Proceso terminado")

    def stop(self):
        logger.info("Señal de detencion recibida, apagando...")
        self.running = False

        chat_manager = ChatCaptureManager.get_instance()
        try:
            chat_manager.stop_capture()
        except Exception as e:
            logger.error(f"Error guardando chat: {e}")

        self.stop_ffmpeg()

        if self.client:
            if self.loop and self.loop.is_running():
                try:
                    future = asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop)
                    future.result(timeout=5)
                except Exception:
                    pass
            self.client = None

        if self.loop and self.loop.is_running():
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass


def main():
    recorder = HeadlessRecorder()
    recorder.run()


if __name__ == "__main__":
    main()
