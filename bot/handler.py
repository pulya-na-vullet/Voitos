from __future__ import annotations

import logging
from typing import Any

import base64

from ai.factory import AINotConfiguredError, get_stt_provider
from ai.intent import HELP_RE, SERVICE_COLLECTIONS_RE, SUBSCRIPTION_RE
from bot.access import AccessDenied, resolve_or_create_user
from bot.client import MaxClient
from bot.messages import help_message
from bot.pipeline import MessagePipeline
from bot.registration import needs_registration, start_registration
from bot.status import normalize_sender
from database.models import AccessState, ChatMessage, MessageRole, PendingAction
from services.service import open_invites_for_user, submit_service_receipt
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

        # Help / subscription / collections / registration stay available when blocked
        is_help = bool(HELP_RE.match(text))
        is_subscription = bool(SUBSCRIPTION_RE.match(text))
        is_collections = bool(SERVICE_COLLECTIONS_RE.match(text))
        lower = text.lower()
        payment_topic = any(k in lower for k in ("оплат", "подписк", "чек", "перевод"))

        user.ensure_grace_period()
        state = user.access_state()
        if state == AccessState.BLOCKED:
            from bot.messages import subscription_detail_message
            from services.service import format_collections_for_user

            pending, _ = PendingAction.objects.get_or_create(user=user)
            if needs_registration(user) and not (is_help or is_subscription or is_collections):
                self._reply(user, start_registration(user, pending))
            elif is_help:
                self._reply(user, help_message(user))
            elif is_collections:
                self._reply(user, format_collections_for_user(user))
            elif is_subscription or payment_topic:
                self._reply(user, subscription_detail_message(user))
            else:
                self._reply(user, access_message(user) or payment_help_text())
            return

        if state == AccessState.GRACE and not is_help and not is_subscription and not is_collections:
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
        except Exception:
            logger.exception("Receipt download failed")
            self._reply(user, "Не удалось скачать файл чека. Пришлите ещё раз.")
            return

        # Prefer service campaign if user has open invites
        invites = open_invites_for_user(user)
        if invites:
            if len(invites) > 1:
                pending, _ = PendingAction.objects.get_or_create(user=user)
                pending.pending_kind = "service_invite_pick"
                pending.pending_payload = {
                    "image_b64": base64.b64encode(raw).decode("ascii"),
                    "filename": filename,
                }
                pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
                lines = [
                    "У вас несколько активных сборов. К какому относится этот чек? Ответьте номером:"
                ]
                for i, inv in enumerate(invites, 1):
                    lines.append(
                        f"{i}. {inv.campaign.get_category_display()} — {inv.campaign.title} "
                        f"({inv.amount_due:.0f} ₽)"
                    )
                self._reply(user, "\n".join(lines))
                return
            try:
                receipt = submit_service_receipt(
                    user, raw, invite=invites[0], filename=filename
                )
            except Exception:
                logger.exception("Service receipt processing failed")
                self._reply(
                    user,
                    "Не удалось разобрать чек сервисного сбора. Пришлите более чёткий скрин.",
                )
                return
            msg = (
                f"Чек по «{receipt.campaign.title}» отправлен администратору.\n"
                f"Сумма: {receipt.amount or 'не распознана'} ₽"
                f"{', дата: ' + receipt.transfer_date.strftime('%d.%m.%Y') if receipt.transfer_date else ''}."
            )
            self._reply(user, msg)
            ChatMessage.objects.create(
                user=user,
                role=MessageRole.ASSISTANT,
                text=msg,
                intent="service_receipt",
            )
            return

        try:
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
        pending, _ = PendingAction.objects.get_or_create(user=user)
        if needs_registration(user):
            self._reply(user, start_registration(user, pending))
        else:
            self._reply(user, help_message(user))

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
