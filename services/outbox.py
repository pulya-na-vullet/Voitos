"""Очередь исходящих MAX-рассылок (без Redis).

Все массовые уведомления кладутся в ScheduledBotMessage и уходят
фоновым воркером с ограничением пачки — HTTP-запрос панели не ждёт MAX.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from django.utils import timezone

from database.models import BotUser, ContractorPayout, ScheduledBotMessage, ServiceCampaign

logger = logging.getLogger(__name__)

KIND_CAMPAIGN_OFFER = "campaign.offer"
KIND_CAMPAIGN_RESEND = "campaign.resend"
KIND_CAMPAIGN_NOTICE = "campaign.notice"
KIND_CAMPAIGN_WORK_DONE = "campaign.work_done"
KIND_CAMPAIGN_PAYOUT = "campaign.payout"
KIND_GROUP_ADDED = "group.added"
KIND_WISH_BALLOT = "wish.ballot"
KIND_MANAGER_SURVEY = "manager.survey"
KIND_VOLUNTEER_ASK = "volunteer.ask"
KIND_RESIDENTS_ASSIGN = "campaign.residents_assign"
KIND_UNPAID_REMIND = "campaign.unpaid_remind"
KIND_CONTRACTOR_OFFER = "contractor.offer"

OUTBOX_BATCH = 40
OUTBOX_MAX_ATTEMPTS = 8


def enqueue(
    user: BotUser,
    text: str,
    *,
    kind: str,
    meta: dict | None = None,
    send_at=None,
) -> ScheduledBotMessage:
    return ScheduledBotMessage.objects.create(
        user=user,
        kind=kind[:64],
        text=text or "",
        send_at=send_at or timezone.now(),
        meta=dict(meta or {}),
    )


def deliver(
    user: BotUser,
    text: str,
    *,
    kind: str,
    meta: dict | None = None,
    send_fn=None,
    send_media_fn=None,
) -> bool:
    """Всегда в очередь. Если передан send_fn (тесты) — отправить сразу."""
    msg = enqueue(user, text, kind=kind, meta=meta)
    if send_fn is None and send_media_fn is None:
        return True
    try:
        _dispatch(msg, send_fn=send_fn, send_media_fn=send_media_fn)
        return True
    except Exception:
        logger.exception("Outbox flush failed id=%s kind=%s", msg.id, kind)
        return False


def _images_from_meta(meta: dict) -> list[tuple[bytes, str]]:
    images: list[tuple[bytes, str]] = []
    offer_id = meta.get("offer_photos_campaign_id")
    if offer_id:
        from services.service import campaign_offer_image_payloads

        campaign = ServiceCampaign.objects.filter(pk=int(offer_id)).first()
        if campaign:
            images.extend(campaign_offer_image_payloads(campaign))
    result_id = meta.get("result_photos_campaign_id")
    if result_id:
        campaign = (
            ServiceCampaign.objects.filter(pk=int(result_id))
            .prefetch_related("result_photos")
            .first()
        )
        if campaign:
            for photo in campaign.result_photos.all()[:2]:
                try:
                    with photo.image.open("rb") as fh:
                        images.append((fh.read(), Path(photo.image.name).name))
                except Exception:
                    logger.exception("Could not read result photo %s", photo.id)
    payout_ids = meta.get("payout_ids") or []
    if payout_ids:
        for p in ContractorPayout.objects.filter(pk__in=payout_ids)[:4]:
            try:
                with p.receipt_image.open("rb") as fh:
                    images.append((fh.read(), Path(p.receipt_image.name).name))
            except Exception:
                logger.exception("Could not read payout receipt %s", p.id)
    payout_id = meta.get("payout_id")
    if payout_id:
        p = ContractorPayout.objects.filter(pk=int(payout_id)).first()
        if p:
            try:
                with p.receipt_image.open("rb") as fh:
                    images.append((fh.read(), Path(p.receipt_image.name).name))
            except Exception:
                logger.exception("Could not read payout receipt %s", p.id)
    return images[:4]


def _default_send():
    from services.work_request_dispatch import _default_send_fn

    return _default_send_fn()


def _send_max_with_images(user: BotUser, text: str, images: list[tuple[bytes, str]]) -> None:
    from ai.factory import get_runtime_settings
    from bot.client import MaxClient

    cfg = get_runtime_settings()
    token = (cfg.max_bot_token or "").strip()
    if not token:
        raise RuntimeError("MAX bot token missing")
    client = MaxClient(token)
    attachments = None
    if images:
        tokens: list[str] = []
        try:
            for raw, filename in images[:2]:
                tokens.append(client.upload_image(raw, filename or "photo.jpg"))
            attachments = client.image_attachments(tokens)
        except Exception:
            logger.exception("MAX image upload failed; sending text-only")
            attachments = None
    if user.chat_id:
        try:
            client.send_message(text, chat_id=user.chat_id, attachments=attachments)
            return
        except Exception:
            logger.exception("chat_id send failed for %s, fallback user_id", user.max_user_id)
    client.send_message(text, user_id=user.max_user_id, attachments=attachments)


def _dispatch(
    msg: ScheduledBotMessage,
    *,
    send_fn=None,
    send_media_fn=None,
    mark_sent: bool = True,
) -> None:
    images = _images_from_meta(msg.meta or {})
    if images and send_media_fn is not None:
        send_media_fn(msg.user, msg.text, images)
    elif send_fn is not None:
        send_fn(msg.user, msg.text)
    elif images:
        _send_max_with_images(msg.user, msg.text, images)
    else:
        send = _default_send()
        if send is None:
            raise RuntimeError("MAX send_fn unavailable")
        send(msg.user, msg.text)
    if mark_sent:
        msg.sent_at = timezone.now()
        msg.save(update_fields=["sent_at"])


def process_outbox(*, limit: int = OUTBOX_BATCH, send_fn=None, send_media_fn=None) -> int:
    """Отправить пачку накопившихся сообщений.

    Сначала атомарно помечаем запись sent_at (claim), потом ходим в MAX —
    SQLite не держит блокировку на время HTTP.
    """
    from services.work_request_completion import (
        SCHEDULED_KIND_CLIENT_CONFIRM,
        on_client_confirm_message_sent,
    )

    now = timezone.now()
    ids = list(
        ScheduledBotMessage.objects.filter(
            sent_at__isnull=True,
            cancelled_at__isnull=True,
            send_at__lte=now,
        )
        .order_by("send_at", "id")
        .values_list("id", flat=True)[: max(1, limit)]
    )
    n = 0
    for msg_id in ids:
        claimed_at = timezone.now()
        claimed = ScheduledBotMessage.objects.filter(
            pk=msg_id,
            sent_at__isnull=True,
            cancelled_at__isnull=True,
        ).update(sent_at=claimed_at)
        if not claimed:
            continue
        msg = (
            ScheduledBotMessage.objects.select_related("user").filter(pk=msg_id).first()
        )
        if msg is None:
            continue
        try:
            _dispatch(
                msg,
                send_fn=send_fn,
                send_media_fn=send_media_fn,
                mark_sent=False,
            )
            n += 1
            if msg.kind == SCHEDULED_KIND_CLIENT_CONFIRM:
                on_client_confirm_message_sent(msg)
        except Exception:
            logger.exception("Failed outbox message %s", msg_id)
            try:
                meta = dict(msg.meta or {})
                attempts = int(meta.get("attempts") or 0) + 1
                meta["attempts"] = attempts
                msg.sent_at = None
                msg.meta = meta
                fields = ["sent_at", "meta", "send_at"]
                if attempts >= OUTBOX_MAX_ATTEMPTS:
                    msg.cancelled_at = timezone.now()
                    fields.append("cancelled_at")
                else:
                    delay = min(300, 5 * (2 ** min(attempts, 6)))
                    msg.send_at = timezone.now() + timedelta(seconds=delay)
                msg.save(update_fields=fields)
            except Exception:
                logger.exception("Failed to backoff outbox %s", msg_id)
    return n


def pending_count() -> int:
    return ScheduledBotMessage.objects.filter(
        sent_at__isnull=True, cancelled_at__isnull=True
    ).count()


def run_outbox_loop(stop_event=None, interval_seconds: float = 1.0) -> None:
    """Фоновый воркер: быстро снимает очередь, пауза если пусто."""
    logger.info("MAX outbox worker started")
    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("MAX outbox worker stopping")
            return
        try:
            n = process_outbox()
            if n:
                logger.info("Outbox sent %s message(s), pending=%s", n, pending_count())
        except Exception:
            logger.exception("Outbox loop error")
            n = 0
        wait = 0.25 if n else interval_seconds
        if stop_event is not None:
            if stop_event.wait(wait):
                logger.info("MAX outbox worker stopping")
                return
        else:
            import time

            time.sleep(wait)
