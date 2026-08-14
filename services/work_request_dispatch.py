"""Автоподбор исполнителя на заявку: роль + НП, ИИ-ранжирование, ответ 20 мин."""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    BotUser,
    ContractorProfile,
    ContractorStatus,
    PendingAction,
    WorkRequest,
    WorkRequestOffer,
    WorkRequestOfferStatus,
    WorkRequestStatus,
)

logger = logging.getLogger(__name__)

WORK_OFFER_MINUTES = 20
WORK_OFFER_PENDING = "work_request_offer_reply"

_YES = {"да", "yes", "y", "+", "ага", "угу", "принято", "согласен", "ок", "1"}
_NO = {"нет", "no", "n", "-", "отказ", "отказаться", "2"}


def normalize_locality(value: str) -> str:
    text = (value or "").lower().replace("ё", "е").strip()
    text = re.sub(
        r"\b(г|гор|город|пгт|село|деревня|пос|поселок|посёлок|рп)\b\.?",
        " ",
        text,
    )
    text = re.sub(r"[^\w\d]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def localities_match(a: str, b: str) -> bool:
    na, nb = normalize_locality(a), normalize_locality(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 3 and len(nb) >= 3 and (na in nb or nb in na):
        return True
    return False


def request_locality(req: WorkRequest) -> str:
    return (req.client_locality or getattr(req.user, "locality", "") or "").strip()


def verified_contractors_for_role(role) -> list[ContractorProfile]:
    if role is None:
        return []
    qs = (
        ContractorProfile.objects.filter(status=ContractorStatus.VERIFIED)
        .filter(Q(role=role) | Q(equipment_type=role.code))
        .select_related("user", "role")
        .order_by("id")
    )
    return list(qs)


def heuristic_candidates(req: WorkRequest) -> list[tuple[ContractorProfile, float, str]]:
    """Кандидаты той же роли в том же / похожем НП."""
    loc = request_locality(req)
    out: list[tuple[ContractorProfile, float, str]] = []
    for c in verified_contractors_for_role(req.role):
        cloc = (c.locality or getattr(c.user, "locality", "") or "").strip()
        if loc and localities_match(loc, cloc):
            out.append((c, 1.0, f"НП совпадает: {cloc}"))
        elif not loc and cloc:
            # У жителя НП не указан — слабый кандидат, ИИ может уточнить
            out.append((c, 0.35, f"НП жителя пуст; исполнитель: {cloc}"))
        elif not loc and not cloc:
            out.append((c, 0.2, "НП не указаны"))
    out.sort(key=lambda x: (-x[1], x[0].id))
    return out


def ai_rank_candidates(
    req: WorkRequest,
    candidates: list[tuple[ContractorProfile, float, str]],
) -> list[tuple[ContractorProfile, float, str]]:
    """ИИ уточняет, кто подходит по району/описанию. При ошибке — эвристика."""
    if not candidates:
        return []
    if len(candidates) == 1 and candidates[0][1] >= 1.0:
        return candidates
    try:
        from ai.factory import AINotConfiguredError, get_llm_provider

        try:
            llm = get_llm_provider()
        except AINotConfiguredError:
            return candidates
    except Exception:
        logger.exception("AI rank: no llm")
        return candidates

    loc = request_locality(req)
    lines = []
    for i, (c, score, reason) in enumerate(candidates, start=1):
        cloc = (c.locality or getattr(c.user, "locality", "") or "").strip()
        lines.append(
            f"{i}. id={c.id}; имя={c.user}; нп={cloc or '—'}; "
            f"эвристика={score:.2f} ({reason})"
        )
    prompt = (
        "Ты диспетчер заявок на мастеров.\n"
        f"Заявка #{req.id}: роль «{req.role.name}».\n"
        f"НП жителя: {loc or 'не указан'}.\n"
        f"Описание: {(req.description or '')[:500]}\n\n"
        "Кандидаты:\n"
        + "\n".join(lines)
        + "\n\nВерни JSON: {\"order\": [id,...], \"note\": \"кратко\"}.\n"
        "В order — id исполнителей от лучшего к худшему, только тех, "
        "кто реально подходит по населённому пункту/району. "
        "Если никто не подходит — {\"order\": [], \"note\": \"...\"}."
    )
    try:
        raw = llm.complete_text(
            "Отвечай только валидным JSON без markdown.",
            prompt,
            temperature=0.1,
            max_tokens=400,
        )
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        order = [int(x) for x in (data.get("order") or [])]
        note = str(data.get("note") or "")[:500]
        by_id = {c.id: (c, score, reason) for c, score, reason in candidates}
        ranked: list[tuple[ContractorProfile, float, str]] = []
        for i, cid in enumerate(order):
            if cid in by_id:
                c, score, reason = by_id[cid]
                ranked.append((c, 2.0 - i * 0.01, note or reason))
        if note:
            req.dispatch_note = note
            req.save(update_fields=["dispatch_note", "updated_at"])
        return ranked if ranked else candidates
    except Exception:
        logger.exception("AI rank failed for work request %s", req.id)
        return candidates


def excluded_contractor_ids(req: WorkRequest) -> set[int]:
    return set(
        req.offers.exclude(status=WorkRequestOfferStatus.CANCELLED).values_list(
            "contractor_id", flat=True
        )
    )


def active_offer(req: WorkRequest) -> WorkRequestOffer | None:
    return (
        req.offers.filter(status=WorkRequestOfferStatus.OFFERED)
        .select_related("contractor", "contractor__user")
        .order_by("-offered_at")
        .first()
    )


def offer_message(offer: WorkRequestOffer) -> str:
    req = offer.work_request
    loc = request_locality(req) or "не указан"
    deadline = ""
    if offer.respond_deadline:
        deadline = timezone.localtime(offer.respond_deadline).strftime("%H:%M")
    return (
        f"Новая заявка #{req.id}: {req.role.name}.\n"
        f"НП: {loc}\n"
        f"Описание: {(req.description or '').strip()[:800]}\n\n"
        f"Ответьте в течение {WORK_OFFER_MINUTES} мин"
        + (f" (до {deadline})" if deadline else "")
        + ":\n"
        "1 / да — беру заказ\n"
        "2 / нет — отказываюсь\n"
        "Если не ответите, заказ уйдёт другому исполнителю."
    )


def _default_send_fn():
    def send_fn(user: BotUser, text: str) -> None:
        from ai.factory import get_runtime_settings
        from bot.client import MaxClient

        cfg = get_runtime_settings()
        token = (cfg.max_bot_token or "").strip()
        if not token:
            logger.warning("No MAX token — skip notify %s", user.max_user_id)
            return
        client = MaxClient(token)
        if user.chat_id:
            try:
                client.send_message(text, chat_id=user.chat_id)
                return
            except Exception:
                logger.exception("chat_id send failed for %s", user.max_user_id)
        client.send_message(text, user_id=user.max_user_id)

    return send_fn


def send_offer(
    req: WorkRequest,
    contractor: ContractorProfile,
    *,
    score: float = 0,
    reason: str = "",
    send_fn=None,
) -> WorkRequestOffer:
    send_fn = send_fn or _default_send_fn()
    now = timezone.now()
    deadline = now + timedelta(minutes=WORK_OFFER_MINUTES)
    offer, created = WorkRequestOffer.objects.get_or_create(
        work_request=req,
        contractor=contractor,
        defaults={
            "status": WorkRequestOfferStatus.OFFERED,
            "respond_deadline": deadline,
            "rank_score": score,
            "rank_reason": (reason or "")[:255],
        },
    )
    if not created:
        if offer.status == WorkRequestOfferStatus.OFFERED:
            return offer
        offer.status = WorkRequestOfferStatus.OFFERED
        offer.respond_deadline = deadline
        offer.responded_at = None
        offer.rank_score = score
        offer.rank_reason = (reason or "")[:255]
        offer.offered_at = now
        offer.save()

    req.status = WorkRequestStatus.OFFERING
    if reason:
        req.dispatch_note = (reason or "")[:2000]
    req.save(update_fields=["status", "dispatch_note", "updated_at"])

    text = offer_message(offer)
    ActivityLog.objects.create(
        user=contractor.user,
        kind=ActivityKind.CONTRACTOR_OFFER,
        title=f"Заявка #{req.id}: предложение",
        detail=text[:500],
        meta={"work_request_id": req.id, "offer_id": offer.id},
    )
    try:
        send_fn(contractor.user, text)
    except Exception:
        logger.exception("Failed to send work offer to %s", contractor.id)

    pending, _ = PendingAction.objects.get_or_create(user=contractor.user)
    pending.pending_kind = WORK_OFFER_PENDING
    pending.pending_payload = {"offer_id": offer.id, "work_request_id": req.id}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return offer


def notify_client_no_executor(req: WorkRequest, *, send_fn=None) -> bool:
    """Один раз сообщить жителю, что по району нет исполнителя."""
    if req.no_executor_notified_at:
        return False
    if req.status in {
        WorkRequestStatus.IN_PROGRESS,
        WorkRequestStatus.DONE,
        WorkRequestStatus.CANCELLED,
    }:
        return False
    send_fn = send_fn or _default_send_fn()
    loc = request_locality(req) or "вашему району"
    text = (
        f"По заявке #{req.id} ({req.role.name}) "
        f"на текущий момент у нас нет исполнителя по данному району"
        + (f" ({loc})" if request_locality(req) else "")
        + ".\nМы сообщим, когда появится подходящий мастер."
    )
    try:
        send_fn(req.user, text)
    except Exception:
        logger.exception("Failed to notify client no executor for WR %s", req.id)
        return False
    req.no_executor_notified_at = timezone.now()
    req.status = WorkRequestStatus.PENDING
    req.dispatch_note = (req.dispatch_note + "\n" if req.dispatch_note else "") + (
        "Нет исполнителя по НП — клиент уведомлён."
    )
    req.save(
        update_fields=[
            "no_executor_notified_at",
            "status",
            "dispatch_note",
            "updated_at",
        ]
    )
    return True


def try_dispatch_request(req: WorkRequest, *, send_fn=None, use_ai: bool = True) -> WorkRequestOffer | None:
    """Подобрать следующего исполнителя или уведомить клиента об отсутствии."""
    req = WorkRequest.objects.select_related("user", "role", "assigned_contractor").get(
        pk=req.pk
    )
    if req.status in {
        WorkRequestStatus.DONE,
        WorkRequestStatus.CANCELLED,
        WorkRequestStatus.IN_PROGRESS,
    }:
        return None
    if active_offer(req):
        return None

    excluded = excluded_contractor_ids(req)
    raw = heuristic_candidates(req)
    raw = [(c, s, r) for c, s, r in raw if c.id not in excluded]
    ranked = ai_rank_candidates(req, raw) if use_ai else raw
    ranked = [(c, s, r) for c, s, r in ranked if c.id not in excluded]

    if not ranked:
        notify_client_no_executor(req, send_fn=send_fn)
        return None

    contractor, score, reason = ranked[0]
    return send_offer(
        req, contractor, score=score, reason=reason, send_fn=send_fn
    )


def dispatch_open_requests(*, send_fn=None, use_ai: bool = True) -> int:
    """Пройти новые/ожидающие заявки без активного оффера."""
    qs = WorkRequest.objects.filter(
        status__in=[WorkRequestStatus.PENDING, WorkRequestStatus.OFFERING]
    ).select_related("user", "role")
    n = 0
    for req in qs:
        if active_offer(req):
            continue
        if try_dispatch_request(req, send_fn=send_fn, use_ai=use_ai):
            n += 1
    return n


def dispatch_for_new_contractor(
    contractor: ContractorProfile, *, send_fn=None, use_ai: bool = True
) -> int:
    """После проверки исполнителя — предложить ему подходящие старые заявки."""
    if contractor.status != ContractorStatus.VERIFIED:
        return 0
    role = contractor.role
    if role is None and contractor.equipment_type:
        from database.models import ExecutorRole

        role = ExecutorRole.objects.filter(code=contractor.equipment_type).first()
    if role is None:
        return 0

    cloc = (contractor.locality or getattr(contractor.user, "locality", "") or "").strip()
    qs = (
        WorkRequest.objects.filter(
            role=role,
            status__in=[WorkRequestStatus.PENDING, WorkRequestStatus.OFFERING],
        )
        .select_related("user", "role")
        .order_by("created_at")
    )
    n = 0
    for req in qs:
        if active_offer(req):
            continue
        if req.offers.filter(contractor=contractor).exists():
            continue
        loc = request_locality(req)
        matched = False
        reason = ""
        if loc and cloc and localities_match(loc, cloc):
            matched = True
            reason = f"НП совпадает: {cloc}"
        elif not loc:
            # НП жителя пуст — спросим ИИ / возьмём как слабого кандидата
            ranked = (
                ai_rank_candidates(
                    req,
                    [(contractor, 0.4, f"НП исполнителя: {cloc or '—'}")],
                )
                if use_ai
                else []
            )
            if ranked and ranked[0][0].id == contractor.id:
                matched = True
                reason = ranked[0][2]
            elif not use_ai:
                matched = True
                reason = "НП жителя не указан"
        if not matched:
            continue
        if req.no_executor_notified_at:
            req.no_executor_notified_at = None
            req.save(update_fields=["no_executor_notified_at", "updated_at"])
        send_offer(req, contractor, score=1.0, reason=reason, send_fn=send_fn)
        n += 1
    return n


def expire_stale_work_offers(*, send_fn=None) -> int:
    """Истёкшие офферы (20 мин) → следующий исполнитель."""
    send_fn = send_fn or _default_send_fn()
    now = timezone.now()
    stale = (
        WorkRequestOffer.objects.filter(
            status=WorkRequestOfferStatus.OFFERED,
            respond_deadline__lt=now,
        )
        .select_related("work_request", "contractor", "contractor__user")
    )
    count = 0
    for offer in stale:
        offer.status = WorkRequestOfferStatus.EXPIRED
        offer.responded_at = now
        offer.save(update_fields=["status", "responded_at"])
        count += 1
        pending = PendingAction.objects.filter(user=offer.contractor.user).first()
        if (
            pending
            and pending.pending_kind == WORK_OFFER_PENDING
            and (pending.pending_payload or {}).get("offer_id") == offer.id
        ):
            pending.clear_pending()
        try:
            send_fn(
                offer.contractor.user,
                f"Время ответа по заявке #{offer.work_request_id} истекло "
                f"({WORK_OFFER_MINUTES} мин). Заказ передан другому исполнителю.",
            )
        except Exception:
            logger.exception("expire notify failed offer %s", offer.id)
        try_dispatch_request(offer.work_request, send_fn=send_fn)
    return count


def accept_offer(offer: WorkRequestOffer, *, send_fn=None) -> str:
    send_fn = send_fn or _default_send_fn()
    req = offer.work_request
    now = timezone.now()
    offer.status = WorkRequestOfferStatus.ACCEPTED
    offer.responded_at = now
    offer.save(update_fields=["status", "responded_at"])

    # Отменить прочие активные офферы
    for other in req.offers.filter(status=WorkRequestOfferStatus.OFFERED).exclude(
        pk=offer.id
    ):
        other.status = WorkRequestOfferStatus.CANCELLED
        other.responded_at = now
        other.save(update_fields=["status", "responded_at"])

    req.status = WorkRequestStatus.IN_PROGRESS
    req.assigned_contractor = offer.contractor
    req.save(
        update_fields=["status", "assigned_contractor", "updated_at"]
    )

    contractor = offer.contractor
    c_user = contractor.user
    phone = (contractor.phone or c_user.phone or "").strip()
    client_text = (
        f"По заявке #{req.id} найден исполнитель: {c_user}.\n"
        f"Роль: {req.role.name}\n"
        + (f"Телефон: {phone}\n" if phone else "")
        + "Он свяжется с вами для выполнения работ."
    )
    exec_text = (
        f"Вы приняли заявку #{req.id}.\n"
        f"Клиент: {req.user}\n"
        f"НП: {request_locality(req) or '—'}\n"
        f"Телефон клиента: {(req.user.phone or '—')}\n"
        f"Описание: {(req.description or '')[:500]}"
    )
    try:
        send_fn(req.user, client_text)
    except Exception:
        logger.exception("notify client accept WR %s", req.id)
    try:
        send_fn(c_user, exec_text)
    except Exception:
        logger.exception("notify contractor accept WR %s", req.id)

    pending = PendingAction.objects.filter(user=c_user).first()
    if pending and pending.pending_kind == WORK_OFFER_PENDING:
        pending.clear_pending()
    return "Спасибо! Заявка закреплена за вами. Контакты клиента отправлены в чат."


def decline_offer(offer: WorkRequestOffer, *, send_fn=None) -> str:
    send_fn = send_fn or _default_send_fn()
    offer.status = WorkRequestOfferStatus.DECLINED
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "responded_at"])
    pending = PendingAction.objects.filter(user=offer.contractor.user).first()
    if pending and pending.pending_kind == WORK_OFFER_PENDING:
        pending.clear_pending()
    try_dispatch_request(offer.work_request, send_fn=send_fn)
    return "Отказ принят. Заявка будет предложена другому исполнителю."


def handle_work_offer_reply(
    user: BotUser, text: str, pending: PendingAction
) -> str | None:
    if pending.pending_kind != WORK_OFFER_PENDING:
        return None
    payload = dict(pending.pending_payload or {})
    offer = (
        WorkRequestOffer.objects.select_related(
            "work_request", "work_request__role", "work_request__user", "contractor"
        )
        .filter(pk=payload.get("offer_id"))
        .first()
    )
    if not offer or offer.contractor.user_id != user.id:
        pending.clear_pending()
        return "Предложение по заявке не найдено или устарело."
    if offer.status != WorkRequestOfferStatus.OFFERED:
        pending.clear_pending()
        return "Это предложение уже обработано."
    if offer.respond_deadline and offer.respond_deadline < timezone.now():
        offer.status = WorkRequestOfferStatus.EXPIRED
        offer.responded_at = timezone.now()
        offer.save(update_fields=["status", "responded_at"])
        pending.clear_pending()
        try_dispatch_request(offer.work_request)
        return (
            f"Время ответа истекло ({WORK_OFFER_MINUTES} мин). "
            "Заявка передана дальше."
        )

    raw = (text or "").strip().lower().replace("ё", "е")
    if raw in _YES:
        return accept_offer(offer)
    if raw in _NO:
        return decline_offer(offer)
    return (
        "Ответьте:\n"
        "1 / да — беру заказ\n"
        "2 / нет — отказываюсь"
    )
