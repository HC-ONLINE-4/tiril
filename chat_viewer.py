#!/usr/bin/env python3
"""
TikTok Live Chat Viewer
Reproductor de grabaciones con video + chat lado a lado estilo TikTok
Usa ffplay (viene con ffmpeg) para reproducir video
"""

import json
import os
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox
import threading
import time
import signal
import random

# Colores para usuarios (estilo TikTok)
USER_COLORS = [
    "#ff6b6b", "#4ecdc4", "#45b7d1", "#96ceb4", "#ffeaa7",
    "#dda0dd", "#98d8c8", "#f7dc6f", "#bb8fce", "#85c1e9",
    "#f8c291", "#82e0aa", "#f1948a", "#85929e", "#73c6b6",
]

user_color_map = {}


def get_user_color(username: str) -> str:
    """Obtiene un color consistente para un usuario."""
    if username not in user_color_map:
        user_color_map[username] = random.choice(USER_COLORS)
    return user_color_map[username]


class ChatViewer:
    """Reproductor de grabaciones con video + chat usando ffplay."""

    def __init__(self, root):
        self.root = root
        self.root.title("TikTok Live Viewer")
        self.root.geometry("1200x700")
        self.root.minsize(1000, 600)
        self.root.configure(bg="#1a1a2e")

        self.video_file = None
        self.chat_data = None
        self.messages = []
        self.playing = False
        self.paused = False
        self.current_time = 0
        self.duration = 0
        self.ffplay_process = None
        self.update_job = None
        self.selected_index = None
        self.chat_messages = []
        self.start_time = None
        self.duration_thread = None
        self.monitoring = False

        self._build_ui()
        self._load_recordings()

    def _build_ui(self):
        """Construye la interfaz de usuario."""
        # Header
        header = tk.Frame(self.root, bg="#e94560", height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header,
            text="TikTok Live Viewer",
            font=("Segoe UI", 16, "bold"),
            bg="#e94560",
            fg="white",
        ).pack(expand=True)

        # Main container
        main_frame = tk.Frame(self.root, bg="#1a1a2e")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Left panel - Video info + controls
        left_panel = tk.Frame(main_frame, bg="#0f0f23")
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        # Video area placeholder
        self.video_frame = tk.Frame(left_panel, bg="#000000", height=400)
        self.video_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.video_frame.pack_propagate(False)

        self.video_label = tk.Label(
            self.video_frame,
            text="Selecciona una grabación\ny haz clic en 'Cargar'",
            font=("Segoe UI", 14),
            bg="#000000",
            fg="#888",
            justify=tk.CENTER,
        )
        self.video_label.pack(expand=True)

        # Video controls
        controls_frame = tk.Frame(left_panel, bg="#1a1a2e", height=80)
        controls_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        controls_frame.pack_propagate(False)

        # Time display
        self.time_label = tk.Label(
            controls_frame,
            text="00:00:00",
            font=("Consolas", 14, "bold"),
            bg="#1a1a2e",
            fg="#00ff41",
        )
        self.time_label.pack(side=tk.LEFT, padx=10, pady=5)

        # Play/Pause button
        self.play_btn = tk.Button(
            controls_frame,
            text="▶ Play",
            font=("Segoe UI", 11, "bold"),
            bg="#27ae60",
            fg="white",
            activebackground="#2ecc71",
            relief=tk.FLAT,
            padx=20,
            pady=8,
            cursor="hand2",
            command=self.toggle_play,
            state=tk.DISABLED,
        )
        self.play_btn.pack(side=tk.LEFT, padx=5, pady=5)

        # Stop button
        self.stop_btn = tk.Button(
            controls_frame,
            text="⏹ Stop",
            font=("Segoe UI", 11, "bold"),
            bg="#c0392b",
            fg="white",
            activebackground="#e74c3c",
            relief=tk.FLAT,
            padx=20,
            pady=8,
            cursor="hand2",
            command=self.stop_video,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5, pady=5)

        # Status label
        self.status_label = tk.Label(
            controls_frame,
            text="Detenido",
            font=("Segoe UI", 10),
            bg="#1a1a2e",
            fg="#888",
        )
        self.status_label.pack(side=tk.RIGHT, padx=10, pady=5)

        # Right panel - Chat
        right_panel = tk.Frame(main_frame, bg="#0f0f23", width=350)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(5, 0))
        right_panel.pack_propagate(False)

        # Chat header
        chat_header = tk.Frame(right_panel, bg="#16213e", height=40)
        chat_header.pack(fill=tk.X)
        chat_header.pack_propagate(False)

        tk.Label(
            chat_header,
            text="Chat del Live",
            font=("Segoe UI", 12, "bold"),
            bg="#16213e",
            fg="white",
        ).pack(side=tk.LEFT, padx=10, pady=5)

        self.chat_count_label = tk.Label(
            chat_header,
            text="0 mensajes",
            font=("Segoe UI", 9),
            bg="#16213e",
            fg="#888",
        )
        self.chat_count_label.pack(side=tk.RIGHT, padx=10)

        # Chat messages area
        self.chat_frame = tk.Frame(right_panel, bg="#0f0f23")
        self.chat_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Chat canvas with scrollbar
        self.chat_canvas = tk.Canvas(
            self.chat_frame, bg="#0f0f23", highlightthickness=0
        )
        scrollbar = tk.Scrollbar(
            self.chat_frame, orient=tk.VERTICAL, command=self.chat_canvas.yview
        )
        self.chat_inner = tk.Frame(self.chat_canvas, bg="#0f0f23")

        self.chat_inner.bind(
            "<Configure>",
            lambda e: self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all")),
        )

        self.chat_canvas.create_window((0, 0), window=self.chat_inner, anchor="nw")
        self.chat_canvas.configure(yscrollcommand=scrollbar.set)

        self.chat_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Bottom panel - Recording selector
        bottom_frame = tk.Frame(self.root, bg="#16213e", height=80)
        bottom_frame.pack(fill=tk.X, side=tk.BOTTOM)
        bottom_frame.pack_propagate(False)

        tk.Label(
            bottom_frame,
            text="Grabaciones:",
            font=("Segoe UI", 10, "bold"),
            bg="#16213e",
            fg="white",
        ).pack(side=tk.LEFT, padx=10, pady=10)

        # Recording listbox
        listbox_frame = tk.Frame(bottom_frame, bg="#16213e")
        listbox_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.recording_listbox = tk.Listbox(
            listbox_frame,
            font=("Consolas", 9),
            bg="#0f0f23",
            fg="#00ff41",
            selectbackground="#e94560",
            selectforeground="white",
            relief=tk.FLAT,
            height=3,
        )
        self.recording_listbox.pack(fill=tk.BOTH, expand=True)
        self.recording_listbox.bind("<<ListboxSelect>>", self._on_select)

        # Load button
        self.load_btn = tk.Button(
            bottom_frame,
            text="Cargar",
            font=("Segoe UI", 10, "bold"),
            bg="#3498db",
            fg="white",
            activebackground="#2980b9",
            relief=tk.FLAT,
            padx=15,
            pady=5,
            cursor="hand2",
            command=self._load_selected,
        )
        self.load_btn.pack(side=tk.RIGHT, padx=10, pady=10)

        # Refresh button
        self.refresh_btn = tk.Button(
            bottom_frame,
            text="Refresh",
            font=("Segoe UI", 9),
            bg="#7f8c8d",
            fg="white",
            activebackground="#95a5a6",
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self._load_recordings,
        )
        self.refresh_btn.pack(side=tk.RIGHT, padx=5, pady=10)

    def _load_recordings(self):
        """Carga las grabaciones disponibles."""
        self.recording_listbox.delete(0, tk.END)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(script_dir, "grabaciones")

        if not os.path.exists(output_dir):
            return

        recordings = {}
        for f in os.listdir(output_dir):
            filepath = os.path.join(output_dir, f)
            if f.endswith((".mp4", ".flv", ".ts")):
                base = f.rsplit(".", 1)[0]
                if base not in recordings:
                    recordings[base] = {"video": None, "chat": None}
                recordings[base]["video"] = filepath
            elif f.endswith(".json") and "_chat" in f:
                base = f.replace("_chat.json", "")
                if base not in recordings:
                    recordings[base] = {"video": None, "chat": None}
                recordings[base]["chat"] = filepath

        for base, files in sorted(recordings.items(), reverse=True):
            has_video = "V" if files["video"] else "-"
            has_chat = "C" if files["chat"] else "-"
            display = f"[{has_video}|{has_chat}] {base}"
            self.recording_listbox.insert(tk.END, display)

    def _on_select(self, event):
        """Maneja la selección de una grabación."""
        selection = self.recording_listbox.curselection()
        if selection:
            self.selected_index = selection[0]

    def _load_selected(self):
        """Carga la grabación seleccionada."""
        if not hasattr(self, "selected_index") or self.selected_index is None:
            return

        item = self.recording_listbox.get(self.selected_index)
        if not item:
            return

        # Detener video actual
        self.stop_video()

        # Extraer nombre del archivo
        base_name = item.split("] ", 1)[1] if "] " in item else item
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(script_dir, "grabaciones")

        # Buscar video
        video_extensions = [".mp4", ".flv", ".ts"]
        video_file = None
        for ext in video_extensions:
            path = os.path.join(output_dir, base_name + ext)
            if os.path.exists(path):
                video_file = path
                break

        # Buscar chat
        chat_file = os.path.join(output_dir, base_name + "_chat.json")
        if not os.path.exists(chat_file):
            chat_file = None

        if video_file:
            self.video_file = video_file
            self.play_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.NORMAL)
            self.video_label.config(
                text=f"Archivo cargado:\n{os.path.basename(video_file)}\n\nHaz clic en Play"
            )
        else:
            messagebox.showwarning("Advertencia", "No se encontro archivo de video")

        if chat_file:
            self._load_chat(chat_file)
        else:
            self.chat_count_label.config(text="Sin chat")
            self._clear_chat()

    def _get_video_duration(self):
        """Obtiene la duración del video usando ffprobe."""
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                self.video_file
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return float(result.stdout.strip())
        except:
            pass
        return 0

    def _play_video(self):
        """Reproduce el video usando ffplay."""
        if not self.video_file:
            return

        # Obtener duración
        self.duration = self._get_video_duration()

        # Comando ffplay - con ventana de video
        cmd = [
            "ffplay",
            "-autoexit",
            "-loglevel", "quiet",
            "-window_title", f"TikTok Live - {os.path.basename(self.video_file)}",
            self.video_file
        ]

        try:
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.ffplay_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )

            self.playing = True
            self.paused = False
            self.start_time = time.time()
            self.play_btn.config(text="⏸ Pause")
            self.status_label.config(text="Reproduciendo", fg="#27ae60")

            # Iniciar monitoreo de tiempo
            self._start_time_monitor()

            # Esperar a que termine el video
            def _wait():
                if self.ffplay_process:
                    self.ffplay_process.wait()
                    if self.playing:
                        self.root.after(0, self._video_ended)

            threading.Thread(target=_wait, daemon=True).start()

        except FileNotFoundError:
            self.video_label.config(
                text="ffplay no encontrado.\nAsegurate de que ffmpeg esta instalado."
            )
        except Exception as e:
            self.video_label.config(text=f"Error: {str(e)}")

    def _video_ended(self):
        """Maneja el fin del video."""
        self.playing = False
        self.paused = False
        self.play_btn.config(text="▶ Play")
        self.status_label.config(text="Finalizado", fg="#f39c12")
        self._stop_time_monitor()

    def _start_time_monitor(self):
        """Inicia el monitoreo del tiempo de reproducción."""
        self.monitoring = True
        self._update_time()

    def _stop_time_monitor(self):
        """Detiene el monitoreo del tiempo."""
        self.monitoring = False

    def _update_time(self):
        """Actualiza el tiempo de reproducción."""
        if not self.monitoring:
            return

        if self.start_time and self.playing and not self.paused:
            elapsed = time.time() - self.start_time
            self.current_time = elapsed

            # Formatear tiempo
            current_str = self._format_time(elapsed)
            duration_str = self._format_time(self.duration)
            self.time_label.config(text=f"{current_str} / {duration_str}")

            # Sincronizar chat
            self._sync_chat(elapsed)

        if self.monitoring:
            self.update_job = self.root.after(500, self._update_time)

    def _format_time(self, seconds):
        """Formatea segundos a HH:MM:SS."""
        if not seconds or seconds < 0:
            return "00:00:00"
        h, rem = divmod(int(seconds), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _load_chat(self, filepath):
        """Carga un archivo de chat."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                self.chat_data = json.load(f)

            self.messages = self.chat_data.get("messages", [])
            self.chat_count_label.config(text=f"{len(self.messages)} mensajes")

            self._clear_chat()
            self._display_all_messages()

        except Exception as e:
            messagebox.showerror("Error", f"Error cargando chat:\n{str(e)}")

    def _clear_chat(self):
        """Limpia el area de chat."""
        for widget in self.chat_inner.winfo_children():
            widget.destroy()
        self.chat_messages = []

    def _display_all_messages(self):
        """Muestra todos los mensajes del chat."""
        for msg in self.messages:
            self._add_chat_message(msg, scroll=False)
        self._scroll_chat_to_bottom()

    def _add_chat_message(self, msg, scroll=True):
        """Agrega un mensaje al chat."""
        msg_type = msg.get("type", "comment")
        user = msg.get("user", "Anonimo")
        text = msg.get("text", "")
        timestamp = msg.get("timestamp", 0)
        is_super_fan = msg.get("is_super_fan", False)

        # Frame para el mensaje
        msg_frame = tk.Frame(self.chat_inner, bg="#0f0f23", padx=5, pady=2)
        msg_frame.pack(fill=tk.X, anchor="w")

        # Timestamp
        ts_text = self._format_time(timestamp)
        ts_label = tk.Label(
            msg_frame,
            text=f"[{ts_text}] ",
            font=("Consolas", 8),
            bg="#0f0f23",
            fg="#666",
        )
        ts_label.pack(side=tk.LEFT)

        # Badge de super fan
        if is_super_fan:
            badge = tk.Label(
                msg_frame,
                text="*",
                font=("Segoe UI", 8, "bold"),
                bg="#0f0f23",
                fg="#f39c12",
            )
            badge.pack(side=tk.LEFT)

        # Nombre de usuario
        color = get_user_color(user)
        user_label = tk.Label(
            msg_frame,
            text=f"{user}: ",
            font=("Segoe UI", 9, "bold"),
            bg="#0f0f23",
            fg=color,
        )
        user_label.pack(side=tk.LEFT)

        # Mensaje
        if msg_type == "gift":
            msg_color = "#f39c12"
        elif msg_type == "join":
            msg_color = "#27ae60"
        elif msg_type == "like":
            msg_color = "#e74c3c"
        elif msg_type == "follow":
            msg_color = "#9b59b6"
        else:
            msg_color = "#ecf0f1"

        msg_label = tk.Label(
            msg_frame,
            text=text,
            font=("Segoe UI", 9),
            bg="#0f0f23",
            fg=msg_color,
            wraplength=280,
            justify=tk.LEFT,
        )
        msg_label.pack(side=tk.LEFT, fill=tk.X)

        self.chat_messages.append(msg_frame)

        if scroll:
            self._scroll_chat_to_bottom()

    def _scroll_chat_to_bottom(self):
        """Desplaza el chat al final."""
        self.chat_canvas.update_idletasks()
        self.chat_canvas.yview_moveto(1.0)

    def _sync_chat(self, current_time):
        """Sincroniza el chat con el tiempo del video."""
        if not self.messages:
            return

        # Encontrar mensajes visibles
        visible_count = 0
        for msg in self.messages:
            if msg.get("timestamp", 0) <= current_time:
                visible_count += 1
            else:
                break

        # Mostrar solo los mensajes visibles
        current_visible = len(self.chat_messages)
        if visible_count > current_visible:
            for i in range(current_visible, visible_count):
                self._add_chat_message(self.messages[i], scroll=False)
            self._scroll_chat_to_bottom()

    def toggle_play(self):
        """Alterna entre play y pause."""
        if not self.video_file:
            return

        if self.playing and not self.paused:
            # Pausar
            self._pause_video()
        elif self.playing and self.paused:
            # Reanudar
            self._resume_video()
        else:
            # Reproducir
            self._play_video()

    def _pause_video(self):
        """Pausa la reproducción."""
        if self.ffplay_process:
            # ffplay no tiene pause nativo, matar y reiniciar desde la posición
            self.paused = True
            self.play_btn.config(text="▶ Play")
            self.status_label.config(text="Pausado", fg="#f39c12")
            # Guardar posición actual
            self._saved_time = self.current_time

    def _resume_video(self):
        """Reanuda la reproducción desde la posición guardada."""
        if self.ffplay_process:
            self.ffplay_process.terminate()
            self.ffplay_process = None

        # Reiniciar desde la posición guardada
        saved_time = getattr(self, '_saved_time', 0)

        cmd = [
            "ffplay",
            "-autoexit",
            "-ss", str(saved_time),
            "-loglevel", "quiet",
            "-window_title", f"TikTok Live - {os.path.basename(self.video_file)}",
            self.video_file
        ]

        try:
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.ffplay_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )

            self.paused = False
            self.start_time = time.time() - saved_time
            self.play_btn.config(text="⏸ Pause")
            self.status_label.config(text="Reproduciendo", fg="#27ae60")

            def _wait():
                if self.ffplay_process:
                    self.ffplay_process.wait()
                    if self.playing and not self.paused:
                        self.root.after(0, self._video_ended)

            threading.Thread(target=_wait, daemon=True).start()

        except Exception as e:
            self.status_label.config(text=f"Error: {str(e)}", fg="#e74c3c")

    def stop_video(self):
        """Detiene la reproducción."""
        if self.ffplay_process:
            try:
                self.ffplay_process.terminate()
                self.ffplay_process.wait(timeout=2)
            except:
                try:
                    self.ffplay_process.kill()
                except:
                    pass
            self.ffplay_process = None

        self.playing = False
        self.paused = False
        self.current_time = 0
        self.start_time = None
        self._stop_time_monitor()

        self.play_btn.config(text="▶ Play")
        self.status_label.config(text="Detenido", fg="#888")
        self.time_label.config(text="00:00:00")

    def on_close(self):
        """Limpieza al cerrar."""
        self.stop_video()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ChatViewer(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
