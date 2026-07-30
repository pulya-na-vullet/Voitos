from __future__ import annotations

import logging
from typing import Any

from ai.factory import AINotConfiguredError, get_stt_provider
from bot.access import AccessDenied, resolve_or_create_user
from bot.client import MaxClient
from bot.pipeline import MessagePipeline

logger = logging.getLogger(__name__)


def _extract_text(message: dict[str, Any]) -> str:
    body = message.get("body") or {}
    if isinstance(body, dict):
        return (body.get("text") or message.get("text") or "").strip()
    return (message.get("text") or "").strip()


def _iter_attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    body = message.get("body") or {}
    attachments = []
    if isinstance(body, dict):
        attachments.extend(body.get("attachments") or [])
    attachments.extend(message.get("attachments") or [])
    return [a for a in attachments if isinstance(a, dict)]


def _find_audio_url(message: dict[str, Any]) -> str | None:
    for att in _iter_attachments(message):
        att_type = (att.get("type") or "").lower()
        payload = att.get("payload") or {}
        if att_type in {"audio", "voice", "file", "unsupported"}:
            url = payload.get("url") or att.get("url")
            if url:
                return url
            # Some payloads only have token — skip if no URL
        # nested
        if payload.get("url") and att_type in {"audio", "voice"}:
            return payload["url"]
    return None


class UpdateHandler:
    def __init__(self, client: MaxClient) -> None:
        self.client = client
        self.pipeline = MessagePipeline()

    def handle_update(self, update: dict[str, Any]) -> None:
        update_type = update.get("update_type") or update.get("type")
        if update_type == "bot_started":
            self._on_bot_started(update)
            return
        if update_type != "message_created":
            return
        message = update.get("message") or {}
        sender = message.get("sender") or update.get("user") or {}
        recipient = message.get("recipient") or {}
        chat_id = (
            update.get("chat_id")
            or recipient.get("chat_id")
            or message.get("chat_id")
        )
        try:
            user = resolve_or_create_user(sender, chat_id=chat_id)
        except AccessDenied as exc:
            target_user = sender.get("user_id") or sender.get("id")
            if target_user:
                try:
                    self.client.send_message(str(exc), user_id=target_user)
                except Exception:
                    logger.exception("Failed to notify denied user")
            return

        text = _extract_text(message)
        is_voice = False
        transcript = ""
        audio_url = _find_audio_url(message)
        if audio_url and not text:
            is_voice = True
            try:
                audio = self.client.download(audio_url)
                stt = get_stt_provider()
                # Try oggopus first (typical for voice), then mp3
                transcript = stt.transcribe(audio, audio_format="oggopus")
                if not transcript:
                    transcript = stt.transcribe(audio, audio_format="mp3")
                text = transcript
            except AINotConfiguredError as exc:
                self._reply(user, str(exc))
                return
            except Exception:
                logger.exception("Voice transcription failed")
                self._reply(user, "Не удалось распознать голос.")
                return
            if not text:
                self._reply(user, "Не расслышал. Попробуй ещё раз.")
                return

        if not text:
            return

        try:
            reply = self.pipeline.handle(
                user,
                text,
                is_voice=is_voice,
                voice_transcript=transcript,
            )
        except Exception:
            logger.exception("Pipeline failed")
            reply = "Произошла ошибка. Попробуй ещё раз."

        self._reply(user, reply)

    def _on_bot_started(self, update: dict[str, Any]) -> None:
        payload_user = update.get("user") or {}
        chat_id = update.get("chat_id")
        try:
            user = resolve_or_create_user(payload_user, chat_id=chat_id)
        except AccessDenied as exc:
            uid = payload_user.get("user_id") or payload_user.get("id")
            if uid:
                self.client.send_message(str(exc), user_id=uid)
            return
        self._reply(
            user,
            "Привет! Я Voitos — твой личный помощник.\n"
            "Пиши мысли, задачи и напоминания. Можно голосом.\n"
            "Команды: «Запомни это», «Не запоминай», «Что мне нужно сделать?»",
        )

    def _reply(self, user, text: str) -> None:
        try:
            if user.chat_id:
                self.client.send_message(text, chat_id=user.chat_id)
            else:
                self.client.send_message(text, user_id=user.max_user_id)
        except Exception:
            logger.exception("Failed to send reply to user %s", user.max_user_id)
            # Fallback to user_id
            try:
                self.client.send_message(text, user_id=user.max_user_id)
            except Exception:
                logger.exception("Fallback send also failed")
