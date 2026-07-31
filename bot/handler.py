from __future__ import annotations

import logging
from typing import Any

from ai.factory import AINotConfiguredError, get_stt_provider
from bot.access import AccessDenied, resolve_or_create_user
from bot.client import MaxClient
from bot.pipeline import MessagePipeline
from bot.status import normalize_sender
from database.models import AccessState, MessageRole, ChatMessage
from subscriptions.service import (
    access_message,
    approved_user_message,
    can_use_features,
    payment_help_text,
    submit_receipt,
)

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
        if payload.get("url") and att_type in {"audio", "voice"}:
            return payload["url"]
    return None


def _find_image(message: dict[str, Any]) -> tuple[str | None, str]:
    for att in _iter_attachments(message):
        att_type = (att.get("type") or "").lower()
        payload = att.get("payload") or {}
        if att_type in {"image", "photo"}:
            url = payload.get("url") or att.get("url")
            if url:
                return url, "receipt.jpg"
        # some clients send screenshot as file
        if att_type == "file":
            url = payload.get("url") or att.get("url")
            name = (payload.get("filename") or "").lower()
            if url and any(name.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                return url, payload.get("filename") or "receipt.jpg"
    return None, "receipt.jpg"


class UpdateHandler:
    def __init__(self, client: MaxClient) -> None:
        self.client = client
        self.pipeline = MessagePipeline()

    def handle_update(self, update: dict[str, Any]) -> None:
        update_type = update.get("update_type") or update.get("type")
        logger.info("Handling update_type=%s", update_type)

        if update_type == "bot_started":
            self._on_bot_started(update)
            return
        if update_type != "message_created":
            return

        message = update.get("message") or {}
        sender = normalize_sender(message.get("sender") or update.get("user") or {})
        if sender.get("is_bot"):
            return

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

        # Receipt image first — allowed even when blocked
        image_url, filename = _find_image(message)
        if image_url:
            self._handle_receipt(user, image_url, filename)
            return

        text = _extract_text(message)
        is_voice = False
        transcript = ""
        audio_url = _find_audio_url(message)
        if audio_url and not text:
            if not can_use_features(user):
                self._reply(user, access_message(user) or payment_help_text())
                return
            is_voice = True
            try:
                audio = self.client.download(audio_url)
                stt = get_stt_provider()
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

        lower = text.lower()
        if any(k in lower for k in ("оплат", "подписк", "чек", "перевод")):
            # Always allow payment help
            if "чек" not in lower or image_url is None:
                self._reply(user, access_message(user) or payment_help_text())
                # still allow grace users to continue with features below
                if user.access_state() == AccessState.BLOCKED:
                    return

        # Subscription gate
        user.ensure_grace_period()
        state = user.access_state()
        if state == AccessState.BLOCKED:
            self._reply(user, access_message(user) or payment_help_text())
            return
        if state == AccessState.GRACE:
            # Notify once per day about payment wait, but allow features
            notice = access_message(user)
            if notice and self._should_send_grace_notice(user):
                self._reply(user, notice)

        logger.info("Incoming message from %s: %s", user.max_user_id, text[:120])
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

    def _should_send_grace_notice(self, user) -> bool:
        from django.utils import timezone

        now = timezone.now()
        if user.last_payment_notice_at and (now - user.last_payment_notice_at).total_seconds() < 20 * 3600:
            return False
        user.last_payment_notice_at = now
        user.save(update_fields=["last_payment_notice_at"])
        return True

    def _handle_receipt(self, user, image_url: str, filename: str) -> None:
        try:
            raw = self.client.download(image_url)
            receipt = submit_receipt(user, raw, filename=filename)
        except Exception:
            logger.exception("Receipt processing failed")
            self._reply(
                user,
                "Не удалось разобрать чек. Пришлите более чёткий скрин перевода.\n\n"
                + payment_help_text(),
            )
            return

        ChatMessage.objects.create(
            user=user,
            role=MessageRole.USER,
            text=f"[чек] сумма={receipt.amount} match={receipt.details_match}",
            intent="receipt_submit",
            meta={"receipt_id": receipt.id},
        )
        if receipt.details_match:
            msg = (
                f"Чек получен и отправлен администратору на проверку.\n"
                f"Сумма: {receipt.amount or 'не распознана'} ₽"
                f"{', дата: ' + receipt.transfer_date.strftime('%d.%m.%Y') if receipt.transfer_date else ''}.\n"
                f"Предварительно: ~{receipt.months_granted or 0} мес. подписки."
            )
        else:
            msg = (
                "Чек получен, но реквизиты распознаны неуверенно "
                "(телефон/ФИО/дата). Администратор проверит вручную.\n\n"
                + payment_help_text()
            )
        self._reply(user, msg)
        ChatMessage.objects.create(
            user=user,
            role=MessageRole.ASSISTANT,
            text=msg,
            intent="receipt_submit",
        )

    def _on_bot_started(self, update: dict[str, Any]) -> None:
        payload_user = normalize_sender(update.get("user") or {})
        chat_id = update.get("chat_id")
        try:
            user = resolve_or_create_user(payload_user, chat_id=chat_id)
        except AccessDenied as exc:
            uid = payload_user.get("user_id") or payload_user.get("id")
            if uid:
                self.client.send_message(str(exc), user_id=uid)
            return
        user.ensure_grace_period()
        base = (
            "Привет! Я Voitos — твой личный помощник.\n"
            "Пиши мысли, задачи и напоминания. Можно голосом.\n"
            "Команды: «Запомни это», «Не запоминай», «Что мне нужно сделать?»\n\n"
        )
        state = user.access_state()
        if state == AccessState.ACTIVE:
            extra = "Подписка активна."
        elif state == AccessState.GRACE:
            extra = access_message(user) or payment_help_text()
        else:
            extra = access_message(user) or payment_help_text()
        self._reply(user, base + extra)

    def _reply(self, user, text: str) -> None:
        try:
            if user.chat_id:
                self.client.send_message(text, chat_id=user.chat_id)
            else:
                self.client.send_message(text, user_id=user.max_user_id)
            logger.info("Reply sent to %s", user.max_user_id)
        except Exception:
            logger.exception("Failed to send reply to user %s", user.max_user_id)
            try:
                self.client.send_message(text, user_id=user.max_user_id)
            except Exception:
                logger.exception("Fallback send also failed")
