"""Автоподбор исполнителя на заявку: роль + НП, ИИ-ранжирование, ответ 20 мин."""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from django.db.models import Q
from django.db import transaction
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


SELF_ASSIGNMENT_MSG = (
    "Нельзя назначить себя исполнителем своей заявки. Выберите другого мастера."
)


def is_self_assignment(client_user_id: int | None, contractor: ContractorProfile | None) -> bool:
    if not client_user_id or contractor is None:
        return False
    return int(contractor.user_id) == int(client_user_id)


def assert_not_self_assignment(client_user_id: int | None, contractor: ContractorProfile) -> None:
    if is_self_assignment(client_user_id, contractor):
        raise ValueError(SELF_ASSIGNMENT_MSG)


def heuristic_candidates(req: WorkRequest) -> list[tuple[ContractorProfile, float, str]]:
    """Кандидаты той же роли в том же / похожем НП (не сам заявитель)."""
    loc = request_locality(req)
    out: list[tuple[ContractorProfile, float, str]] = []
    for c in verified_contractors_for_role(req.role):
        if is_self_assignment(req.user_id, c):
            continue
        cloc = (c.locality or getattr(c.user, "locality", "") or "").strip()
        if loc and localities_match(loc, cloc):
            out.append((c, 1.0, f"НП совпадает: {cloc}"))
        elif not loc and cloc:
            # У жителя НП не указан — слабый кандидат, ИИ может уточнить
            out.append((c, 0.35, f"НП жителя пуст; исполнитель: {cloc}"))
        elif not loc and not cloc:
            out.append((c, 0.2, "НП не указаны"))
    out.sort(
        key=lambda x: (
            -x[1],
            0 if getattr(x[0], "is_voitos_team", False) else 1,
            x[0].id,
        )
    )
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


def declined_contractor_ids(req: WorkRequest) -> set[int]:
    """Мастера, которые уже отказались — система больше не назначает их на эту заявку."""
    return set(
        req.offers.filter(status=WorkRequestOfferStatus.DECLINED).values_list(
            "contractor_id", flat=True
        )
    )


def excluded_contractor_ids(req: WorkRequest) -> set[int]:
    """Кого автоподбор пропускает: отказы, активные/принятые/истёкшие офферы."""
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
    address = (getattr(req.user, "address", None) or "").strip() or "не указан"
    deadline = ""
    if offer.respond_deadline:
        deadline = timezone.localtime(offer.respond_deadline).strftime("%H:%M")
    return (
        f"Новая заявка #{req.id}: {req.role.name}.\n"
        f"Населённый пункт: {loc}\n"
        f"Адрес: {address}\n"
        f"Описание: {(req.description or '').strip()[:800]}\n\n"
        f"Ответьте за {WORK_OFFER_MINUTES} мин"
        + (f" (до {deadline})" if deadline else "")
        + ":\n"
        "1 / да — беру\n"
        "2 / нет — отказываюсь\n"
        "Без ответа заказ уйдёт другому мастеру."
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
        # Короткий таймаут: уведомление не должно блокировать HTTP submit заявки.
        send_kwargs = {"timeout": 8, "retries": 1}
        if user.chat_id:
            try:
                client.send_message(text, chat_id=user.chat_id, **send_kwargs)
                return
            except Exception:
                logger.exception("chat_id send failed for %s", user.max_user_id)
        client.send_message(text, user_id=user.max_user_id, **send_kwargs)

    return send_fn


def schedule_dispatch_request(req_id: int, *, use_ai: bool = True) -> None:
    """Автоподбор в фоне — submit/create сразу отвечают клиенту."""
    import threading

    from django.db import connection, transaction

    def _run() -> None:
        try:
            req = WorkRequest.objects.filter(pk=req_id).first()
            if req is None:
                return
            try_dispatch_request(req, use_ai=use_ai)
        except Exception:
            logger.exception("background dispatch failed for WR %s", req_id)
        finally:
            connection.close()

    def _start() -> None:
        threading.Thread(
            target=_run,
            name=f"wr-dispatch-{req_id}",
            daemon=True,
        ).start()

    # После commit транзакции теста/запроса — иначе SQLite lock / гонка.
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(_start)
    else:
        _start()


def send_offer(
    req: WorkRequest,
    contractor: ContractorProfile,
    *,
    score: float = 0,
    reason: str = "",
    send_fn=None,
) -> WorkRequestOffer:
    from services.work_request_completion import contractor_blocked_for_new_offers

    assert_not_self_assignment(req.user_id, contractor)
    if contractor_blocked_for_new_offers(contractor):
        raise ValueError(
            "Исполнитель временно не получает заявки "
            "(не закрыта комиссия 10% по предыдущей работе)."
        )
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
        if offer.status == WorkRequestOfferStatus.DECLINED:
            raise ValueError(
                "Этот мастер уже отказался от заявки — "
                "система не может назначить его исполнителем повторно."
            )
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
        WorkRequestStatus.SCHEDULING,
        WorkRequestStatus.IN_PROGRESS,
        WorkRequestStatus.AWAITING_CLIENT,
        WorkRequestStatus.AWAITING_COMMISSION,
        WorkRequestStatus.DONE,
        WorkRequestStatus.CANCELLED,
    }:
        return False
    send_fn = send_fn or _default_send_fn()
    loc = request_locality(req) or "вашему району"
    text = (
        f"По заявке #{req.id} ({req.role.name}) пока нет мастера "
        f"по району"
        + (f" «{loc}»" if request_locality(req) else "")
        + ".\nНапишем, когда найдём."
    )
    try:
        send_fn(req.user, text)
    except Exception:
        logger.exception("Failed to notify client no executor for WR %s", req.id)
        return False
    try:
        from api.emit import emit_app_event

        emit_app_event(
            req.user,
            ntype="work_request.no_executor",
            title="Мастера пока нет",
            body=text[:500],
            entity_type="work_request",
            entity_id=req.id,
        )
    except Exception:
        logger.exception("app inbox no_executor WR %s", req.id)
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
        WorkRequestStatus.DRAFT,
        WorkRequestStatus.DONE,
        WorkRequestStatus.CANCELLED,
        WorkRequestStatus.SCHEDULING,
        WorkRequestStatus.IN_PROGRESS,
        WorkRequestStatus.AWAITING_CLIENT,
        WorkRequestStatus.AWAITING_COMMISSION,
    }:
        return None
    if active_offer(req):
        return None

    excluded = excluded_contractor_ids(req)
    from services.dispatch_priority import prioritize_dispatch_candidates
    from services.work_request_completion import contractor_blocked_for_new_offers

    raw = heuristic_candidates(req)
    raw = [
        (c, s, r)
        for c, s, r in raw
        if c.id not in excluded and not contractor_blocked_for_new_offers(c)
    ]
    # Сначала Voitos-команда со свободным слотом на сегодня; иначе — остальные.
    raw = prioritize_dispatch_candidates(raw)
    ranked = ai_rank_candidates(req, raw) if use_ai else raw
    ranked = [
        (c, s, r)
        for c, s, r in ranked
        if c.id not in excluded and not contractor_blocked_for_new_offers(c)
    ]
    ranked = prioritize_dispatch_candidates(ranked)

    if not ranked:
        notify_client_no_executor(req, send_fn=send_fn)
        return None

    contractor, score, reason = ranked[0]
    try:
        return send_offer(
            req, contractor, score=score, reason=reason, send_fn=send_fn
        )
    except ValueError:
        notify_client_no_executor(req, send_fn=send_fn)
        return None


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
        from services.work_request_completion import contractor_blocked_for_new_offers

        if contractor_blocked_for_new_offers(contractor):
            continue
        if req.no_executor_notified_at:
            req.no_executor_notified_at = None
            req.save(update_fields=["no_executor_notified_at", "updated_at"])
        try:
            send_offer(req, contractor, score=1.0, reason=reason, send_fn=send_fn)
        except ValueError:
            continue
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
    now = timezone.now()
    with transaction.atomic():
        offer = WorkRequestOffer.objects.select_for_update().select_related(
            "work_request",
            "work_request__role",
            "work_request__user",
            "contractor",
            "contractor__user",
        ).get(pk=offer.pk)
        if offer.status != WorkRequestOfferStatus.OFFERED:
            return "Это предложение уже обработано."
        req = WorkRequest.objects.select_for_update().select_related(
            "user", "role"
        ).get(pk=offer.work_request_id)
        if req.status not in {
            WorkRequestStatus.PENDING,
            WorkRequestStatus.OFFERING,
        }:
            return "Заявка уже не доступна для принятия."
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

        contractor = offer.contractor
        c_user = contractor.user
        if is_self_assignment(req.user_id, contractor):
            offer.status = WorkRequestOfferStatus.CANCELLED
            offer.responded_at = now
            offer.save(update_fields=["status", "responded_at"])
            return SELF_ASSIGNMENT_MSG
        req.assigned_contractor = contractor
        # Найденный исполнитель → всегда согласование времени, затем «в работе».
        req.status = WorkRequestStatus.SCHEDULING
        req.proposed_slots = []
        req.agreed_slot = ""
        req.schedule_agreed_at = None
        req.save(
            update_fields=[
                "status",
                "assigned_contractor",
                "proposed_slots",
                "agreed_slot",
                "schedule_agreed_at",
                "updated_at",
            ]
        )

    from services.contractors import format_executor_contacts_block

    contacts = format_executor_contacts_block(contractor)
    address = (req.user.address or "").strip() or "—"

    pending = PendingAction.objects.filter(user=c_user).first()
    if pending and pending.pending_kind == WORK_OFFER_PENDING:
        pending.clear_pending()

    from services.work_request_schedule import start_master_scheduling

    client_text = (
        f"По заявке #{req.id} найден мастер: {c_user}.\n"
        f"Роль: {req.role.name}\n"
        f"{contacts}\n"
        "Статус: согласование времени. Мастер предложит варианты — "
        "выберите удобное окно в приложении или в чате."
    )
    try:
        send_fn(req.user, client_text)
    except Exception:
        logger.exception("notify client accept WR %s", req.id)
    try:
        from api.emit import emit_app_event

        emit_app_event(
            req.user,
            ntype="work_request.assigned",
            title="Мастер назначен — согласуйте время",
            body=client_text[:500],
            entity_type="work_request",
            entity_id=req.id,
        )
    except Exception:
        logger.exception("app inbox assign WR %s", req.id)
    start_master_scheduling(req, send_fn=send_fn)
    return "Заявка ваша. Согласуйте время с клиентом — инструкция в чате."


def clear_assignment_for_reassign(req: WorkRequest) -> None:
    """Сброс текущего назначения перед повторным поиском / ручным назначением."""
    now = timezone.now()
    for offer in req.offers.filter(status=WorkRequestOfferStatus.OFFERED):
        offer.status = WorkRequestOfferStatus.CANCELLED
        offer.responded_at = now
        offer.save(update_fields=["status", "responded_at"])
    for offer in req.offers.filter(status=WorkRequestOfferStatus.ACCEPTED):
        offer.status = WorkRequestOfferStatus.CANCELLED
        offer.responded_at = now
        offer.save(update_fields=["status", "responded_at"])
    if req.assigned_contractor_id:
        c_user = req.assigned_contractor.user
        pending = PendingAction.objects.filter(user=c_user).first()
        if pending and pending.pending_kind in {
            WORK_OFFER_PENDING,
            "work_request_schedule_master",
        }:
            pending.clear_pending()
    client_pending = PendingAction.objects.filter(user=req.user).first()
    if (
        client_pending
        and client_pending.pending_kind == "work_request_schedule_client"
        and (client_pending.pending_payload or {}).get("work_request_id") == req.id
    ):
        client_pending.clear_pending()
    req.assigned_contractor = None
    req.proposed_slots = []
    req.agreed_slot = ""
    req.schedule_agreed_at = None
    req.master_address = ""
    req.no_executor_notified_at = None
    req.status = WorkRequestStatus.PENDING
    req.save(
        update_fields=[
            "assigned_contractor",
            "proposed_slots",
            "agreed_slot",
            "schedule_agreed_at",
            "master_address",
            "no_executor_notified_at",
            "status",
            "updated_at",
        ]
    )


def admin_reassign_executor(
    req: WorkRequest,
    *,
    contractor_id: int | None = None,
    send_fn=None,
) -> WorkRequestOffer | None:
    """
    Админ/менеджер: повторно назначить исполнителя.
    Отказавшиеся мастера не назначаются (ни авто, ни вручную).
    """
    send_fn = send_fn or _default_send_fn()
    req = WorkRequest.objects.select_related("user", "role", "assigned_contractor").get(
        pk=req.pk
    )
    if req.status in {WorkRequestStatus.DONE, WorkRequestStatus.CANCELLED}:
        raise ValueError("Нельзя переназначать завершённую или отменённую заявку.")

    if contractor_id is not None:
        if contractor_id in declined_contractor_ids(req):
            raise ValueError(
                "Этот мастер уже отказался от заявки — "
                "система не может назначить его исполнителем."
            )
        contractor = (
            ContractorProfile.objects.select_related("user", "role")
            .filter(pk=contractor_id, status=ContractorStatus.VERIFIED)
            .first()
        )
        if contractor is None:
            raise ValueError("Исполнитель не найден или не проверен.")
        assert_not_self_assignment(req.user_id, contractor)

    clear_assignment_for_reassign(req)
    req.refresh_from_db()

    if contractor_id is not None:
        contractor = ContractorProfile.objects.get(pk=contractor_id)
        return send_offer(
            req,
            contractor,
            score=1.0,
            reason="Ручное назначение админом/менеджером",
            send_fn=send_fn,
        )
    return try_dispatch_request(req, send_fn=send_fn)


def decline_offer(offer: WorkRequestOffer, *, send_fn=None) -> str:
    send_fn = send_fn or _default_send_fn()
    offer.status = WorkRequestOfferStatus.DECLINED
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "responded_at"])
    pending = PendingAction.objects.filter(user=offer.contractor.user).first()
    if pending and pending.pending_kind == WORK_OFFER_PENDING:
        pending.clear_pending()

    req = offer.work_request
    client_text = (
        f"Исполнитель по заявке #{req.id} ({req.role.name}) отказался.\n"
        "Ищем другого мастера.\n\n"
        "Если поиск займёт слишком долго — напишите «отменить заявку», "
        "и мы остановим поиск."
    )
    try:
        send_fn(req.user, client_text)
    except Exception:
        logger.exception("notify client decline WR %s", req.id)
    try:
        from api.emit import emit_app_event

        emit_app_event(
            req.user,
            ntype="work_request.executor_declined",
            title="Исполнитель отказался",
            body=client_text[:500],
            entity_type="work_request",
            entity_id=req.id,
        )
    except Exception:
        logger.exception("app inbox decline WR %s", req.id)

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
