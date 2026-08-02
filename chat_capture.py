#!/usr/bin/env python3
"""
TikTok Live Chat Capture
Captura mensajes del chat via WebSocket usando TikTokLive
"""

import json
import os
import time
from datetime import datetime


class ChatCapture:
    """Captura y almacena mensajes del chat de TikTok Live."""

    def __init__(self, username: str, output_dir: str):
        self.username = username
        self.output_dir = output_dir
        self.messages = []
        self.gifts = []
        self.start_time = None
        self.is_active = False

    def start(self):
        """Inicia la captura de chat."""
        self.messages = []
        self.gifts = []
        self.start_time = time.time()
        self.is_active = True

    def stop(self):
        """Detiene la captura de chat."""
        self.is_active = False

    def _get_timestamp(self) -> float:
        """Obtiene el timestamp relativo desde el inicio."""
        if self.start_time is None:
            return 0.0
        return round(time.time() - self.start_time, 2)

    def add_comment(self, event) -> None:
        """
        Agrega un mensaje del chat desde un CommentEvent.

        Args:
            event: CommentEvent de TikTokLive
        """
        if not self.is_active:
            return

        try:
            message = {
                "timestamp": self._get_timestamp(),
                "user": event.user.nickname,
                "user_id": event.user.id,
                "text": event.comment,
                "is_super_fan": event.user_is_super_fan,
                "emotes": [e.emote.id for e in event.emotes] if event.emotes else [],
                "type": "comment"
            }
            self.messages.append(message)
        except Exception as e:
            print(f"Error capturando comentario: {e}")

    def add_gift(self, event) -> None:
        """
        Agrega un regalo desde un GiftEvent.

        Args:
            event: GiftEvent de TikTokLive
        """
        if not self.is_active:
            return

        try:
            gift_data = {
                "timestamp": self._get_timestamp(),
                "user": event.user.nickname,
                "user_id": event.user.id,
                "gift_name": event.gift.name,
                "gift_id": event.gift.id,
                "count": event.repeat_count if hasattr(event, 'repeat_count') else 1,
                "coin_value": event.gift.coin_value if hasattr(event.gift, 'coin_value') else 0,
                "type": "gift"
            }
            self.gifts.append(gift_data)

            # También agregar como mensaje para que aparezca en el chat
            count_text = f" x{gift_data['count']}" if gift_data['count'] > 1 else ""
            message = {
                "timestamp": self._get_timestamp(),
                "user": event.user.nickname,
                "user_id": event.user.id,
                "text": f"🎁 {event.gift.name}{count_text}",
                "is_super_fan": False,
                "emotes": [],
                "type": "gift"
            }
            self.messages.append(message)
        except Exception as e:
            print(f"Error capturando regalo: {e}")

    def add_join(self, event) -> None:
        """
        Agrega un evento de unión desde un JoinEvent.

        Args:
            event: JoinEvent de TikTokLive
        """
        if not self.is_active:
            return

        try:
            message = {
                "timestamp": self._get_timestamp(),
                "user": event.user.nickname,
                "user_id": event.user.id,
                "text": "se unió al live",
                "is_super_fan": False,
                "emotes": [],
                "type": "join"
            }
            self.messages.append(message)
        except Exception as e:
            print(f"Error capturando unión: {e}")

    def add_like(self, event) -> None:
        """
        Agrega un like desde un LikeEvent.

        Args:
            event: LikeEvent de TikTokLive
        """
        if not self.is_active:
            return

        try:
            count = event.count if hasattr(event, 'count') else 1
            message = {
                "timestamp": self._get_timestamp(),
                "user": event.user.nickname,
                "user_id": event.user.id,
                "text": f"❤️ like" if count == 1 else f"❤️ {count} likes",
                "is_super_fan": False,
                "emotes": [],
                "type": "like"
            }
            self.messages.append(message)
        except Exception as e:
            print(f"Error capturando like: {e}")

    def add_follow(self, event) -> None:
        """
        Agrega un follow desde un FollowEvent.

        Args:
            event: FollowEvent de TikTokLive
        """
        if not self.is_active:
            return

        try:
            message = {
                "timestamp": self._get_timestamp(),
                "user": event.user.nickname,
                "user_id": event.user.id,
                "text": "siguió al creador",
                "is_super_fan": False,
                "emotes": [],
                "type": "follow"
            }
            self.messages.append(message)
        except Exception as e:
            print(f"Error capturando follow: {e}")

    def get_messages(self) -> list:
        """Retorna todos los mensajes capturados."""
        return self.messages

    def get_message_count(self) -> int:
        """Retorna la cantidad de mensajes."""
        return len(self.messages)

    def save(self, filename: str = None) -> str:
        """
        Guarda los mensajes en un archivo JSON.

        Args:
            filename: Nombre del archivo (opcional)

        Returns:
            Ruta del archivo guardado
        """
        if not self.messages:
            return None

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        if filename is None:
            filename = f"{self.username}_{timestamp}_chat.json"

        filepath = os.path.join(self.output_dir, filename)

        data = {
            "username": self.username,
            "started_at": datetime.fromtimestamp(self.start_time).isoformat() if self.start_time else None,
            "ended_at": datetime.now().isoformat(),
            "duration_seconds": self._get_timestamp(),
            "total_messages": len(self.messages),
            "total_gifts": len(self.gifts),
            "messages": self.messages,
            "gifts": self.gifts
        }

        os.makedirs(self.output_dir, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return filepath

    @staticmethod
    def load(filepath: str) -> dict:
        """
        Carga un archivo de chat guardado.

        Args:
            filepath: Ruta al archivo JSON

        Returns:
            Diccionario con los datos del chat
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)


class ChatCaptureManager:
    """Manejador singleton para la captura de chat."""

    _instance = None
    _capture = None

    @classmethod
    def get_instance(cls) -> 'ChatCaptureManager':
        """Obtiene la instancia singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start_capture(self, username: str, output_dir: str) -> ChatCapture:
        """Inicia una nueva captura de chat."""
        ChatCaptureManager._capture = ChatCapture(username, output_dir)
        ChatCaptureManager._capture.start()
        return ChatCaptureManager._capture

    def get_capture(self) -> ChatCapture:
        """Obtiene la captura actual."""
        return ChatCaptureManager._capture

    def stop_capture(self) -> str:
        """
        Detiene la captura actual y la guarda.

        Returns:
            Ruta del archivo guardado o None
        """
        if ChatCaptureManager._capture:
            ChatCaptureManager._capture.stop()
            filepath = ChatCaptureManager._capture.save()
            ChatCaptureManager._capture = None
            return filepath
        return None
