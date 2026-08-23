"""Запись клиента к мастеру: свободные окна календаря и предварительный слот."""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    BotUser,
    ContractorProfile,
    ContractorStatus,
    WorkRequest,
    WorkRequestCommissionStatus,
    WorkRequestStatus,
)
from services.work_request_dispatch import (
    assert_not_self_assignment,
    is_self_assignment,
    localities_match,
)
from services.work_request_schedule import format_slot_label, parse_slot_datetime_range

logger = logging.getLogger(__name__)

# Роли без клиентской записи (остаётся автоподбор / офферы).
DISPATCH_ONLY_CODES = frozenset({"tractor", "truck", "computer_master"})
_DISPATCH_ONLY_NAME = re.compile(
    r"(тракторист|трактор[\s\-]?погруз|компьютерн|"
    r"водител\w*\s+грузов|камаз|грузов\w*\s+маш)",
    re.IGNORECASE,
)

COMMISSION_BLOCK_MSG = (
    "Мастер пока не может принять ваш заказ — сначала нужно оплатить "
    "комиссию по прошлому заказу."
)

MASTER_COMMISSION_BLOCK_MSG = (
    "Сначала оплатите комиссию по прошлому заказу — "
    "после этого сможете подтвердить запись и взять заказ в работу."
)

# Рабочие часы по умолчанию (локальная TZ): пн–сб.
_SLOT_MINUTES = 60
_DAY_START_HOUR = 9
_DAY_END_HOUR = 18
_BOOKING_HORIZON_DAYS = 2


def role_is_dispatch_only(role) -> bool:
    """Тракторист / комп. мастер / водитель грузовика — без прямой записи."""
    if role is None:
        return True
    code = (getattr(role, "code", None) or "").strip().lower()
    if code in DISPATCH_ONLY_CODES:
        return True
    name = (getattr(role, "name", None) or "").strip()
    if name and _DISPATCH_ONLY_NAME.search(name.replace("ё", "е")):
        return True
    return False


def role_uses_client_booking(role) -> bool:
    if role is None:
        return False
    if hasattr(role, "client_books_master"):
        return bool(role.client_books_master)
    return not role_is_dispatch_only(role)


def contractor_blocked_for_commission(contractor: ContractorProfile) -> bool:
    """Неоплаченная / на проверке / отклонённая комиссия по любому заказу УЗ."""
    if contractor is None:
        return True
    return WorkRequest.objects.filter(
        assigned_contractor__user_id=contractor.user_id,
        commission_status__in=[
            WorkRequestCommissionStatus.AWAITING,
            WorkRequestCommissionStatus.PENDING_REVIEW,
            WorkRequestCommissionStatus.REJECTED,
        ],
    ).exists()


def _active_busy_statuses() -> set[str]:
    return {
        WorkRequestStatus.SCHEDULING,
        WorkRequestStatus.IN_PROGRESS,
        WorkRequestStatus.AWAITING_CLIENT,
        WorkRequestStatus.AWAITING_COMMISSION,
    }


def busy_intervals_for_user(
    user_id: int,
    *,
    range_start,
    range_end,
    exclude_wr_id: int | None = None,
) -> list[tuple]:
    """
    Занятые интервалы исполнителя по ВСЕМ его ролям (один BotUser).

    Если мастер записан к клиенту сантехником на 10:00–11:00,
    это же окно недоступно для записи к нему как электрику.
    Учитываем согласованные / предзаписанные agreed_slot, включая черновик
    с client_prebooked (фото ещё не отправлены).
    """
    from django.db.models import Q

    qs = (
        WorkRequest.objects.filter(assigned_contractor__user_id=user_id)
        .filter(
            Q(status__in=_active_busy_statuses())
            | Q(
                status=WorkRequestStatus.DRAFT,
                client_prebooked=True,
            )
        )
        .exclude(agreed_slot="")
        .only("id", "agreed_slot")
    )
    if exclude_wr_id:
        qs = qs.exclude(pk=exclude_wr_id)
    now = timezone.localtime(timezone.now())
    out: list[tuple] = []
    for wr in qs[:300]:
        parsed = parse_slot_datetime_range(wr.agreed_slot or "", ref_now=now)
        if not parsed:
            continue
        start, end = parsed
        if end <= range_start or start >= range_end:
            continue
        out.append((start, end))
    out.sort(key=lambda x: x[0])
    return out


def slot_overlaps_user_busy(
    user_id: int,
    start,
    end,
    *,
    exclude_wr_id: int | None = None,
) -> bool:
    """True, если интервал пересекается с любой заявкой мастера (любая роль)."""
    busy = busy_intervals_for_user(
        user_id,
        range_start=start - timedelta(minutes=1),
        range_end=end + timedelta(minutes=1),
        exclude_wr_id=exclude_wr_id,
    )
    return any(_overlaps(start, end, b0, b1) for b0, b1 in busy)


def _overlaps(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


def free_slots_for_contractor(
    contractor: ContractorProfile,
    *,
    days: int = _BOOKING_HORIZON_DAYS,
    slot_minutes: int = _SLOT_MINUTES,
    now=None,
) -> list[dict]:
    """
    Свободные окна мастера на ближайшие days дней.
    Каждое: {day, start_at, end_at, label}.
    """
    now = timezone.localtime(now or timezone.now())
    # Слоты не раньше чем через час.
    earliest = now + timedelta(hours=1)
    horizon_end = (now + timedelta(days=max(1, min(days, 28)))).replace(
        hour=23, minute=59, second=59, microsecond=0
    )
    busy = busy_intervals_for_user(
        contractor.user_id,
        range_start=now.replace(hour=0, minute=0, second=0, microsecond=0),
        range_end=horizon_end + timedelta(days=1),
    )
    slots: list[dict] = []
    day0 = now.date()
    for d in range(0, max(1, min(days, 28))):
        day = day0 + timedelta(days=d)
        # Воскресенье — выходной по умолчанию.
        if day.weekday() == 6:
            continue
        cursor = now.replace(
            year=day.year,
            month=day.month,
            day=day.day,
            hour=_DAY_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
        day_end = cursor.replace(hour=_DAY_END_HOUR, minute=0)
        while cursor + timedelta(minutes=slot_minutes) <= day_end:
            start = cursor
            end = cursor + timedelta(minutes=slot_minutes)
            cursor = end
            if end <= earliest:
                continue
            if any(_overlaps(start, end, b0, b1) for b0, b1 in busy):
                continue
            slots.append(
                {
                    "day": day.isoformat(),
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "label": format_slot_label(start, end),
                }
            )
    return slots


def list_masters_for_role(role, client_user: BotUser) -> list[dict]:
    """Мастера роли в НП клиента (или все verified, если НП пуст)."""
    from services.work_request_dispatch import verified_contractors_for_role

    client_loc = (getattr(client_user, "locality", None) or "").strip()
    items: list[dict] = []
    for c in verified_contractors_for_role(role):
        if is_self_assignment(client_user.id, c):
            continue
        cloc = (c.locality or getattr(c.user, "locality", None) or "").strip()
        if client_loc and cloc and not localities_match(client_loc, cloc):
            continue
        if client_loc and not cloc:
            continue
        blocked = contractor_blocked_for_commission(c)
        free_count = 0
        if not blocked:
            free_count = len(free_slots_for_contractor(c, days=_BOOKING_HORIZON_DAYS))
        items.append(
            {
                "contractor_id": c.id,
                "name": str(c.user),
                "locality": cloc,
                "phone": (c.phone or c.user.phone or "")[:64],
                "can_accept": (not blocked) and free_count > 0,
                "blocked_reason": "commission" if blocked else (
                    "no_slots" if free_count == 0 else ""
                ),
                "blocked_message": COMMISSION_BLOCK_MSG if blocked else (
                    "Сейчас нет свободных окон у этого мастера."
                    if free_count == 0
                    else ""
                ),
                "free_slots_preview": free_count,
            }
        )
    # Сначала доступные
    items.sort(key=lambda x: (0 if x["can_accept"] else 1, x["name"].casefold()))
    return items


def role_has_local_masters(role, client_user: BotUser) -> bool:
    """Есть ли в НП клиента хотя бы один verified-мастер роли (не сам клиент)."""
    from services.work_request_dispatch import verified_contractors_for_role

    if role is None or client_user is None:
        return False
    client_loc = (getattr(client_user, "locality", None) or "").strip()
    for c in verified_contractors_for_role(role):
        if is_self_assignment(client_user.id, c):
            continue
        cloc = (c.locality or getattr(c.user, "locality", None) or "").strip()
        if client_loc and cloc and not localities_match(client_loc, cloc):
            continue
        if client_loc and not cloc:
            continue
        return True
    return False


def roles_for_client_call(client_user: BotUser):
    """
    Активные роли, у которых в НП клиента есть другой мастер.

    Если пользователь сам единственный электрик в посёлке — роль «Электрик»
    ему не показываем (иначе «нет мастеров этой роли»).
    Полный каталог (регистрация исполнителем) — без этого фильтра.
    """
    from database.models import ExecutorRole

    roles = list(
        ExecutorRole.objects.filter(is_active=True).order_by("sort_order", "id")
    )
    if not roles or client_user is None:
        return roles

    client_id = int(client_user.id)
    client_loc = (getattr(client_user, "locality", None) or "").strip()
    profiles = list(
        ContractorProfile.objects.filter(status=ContractorStatus.VERIFIED)
        .exclude(user_id=client_id)
        .select_related("user", "role")
        .order_by("id")
    )
    role_ids: set[int] = set()
    role_codes: set[str] = set()
    for c in profiles:
        cloc = (c.locality or getattr(c.user, "locality", None) or "").strip()
        if client_loc:
            if not cloc or not localities_match(client_loc, cloc):
                continue
        if c.role_id:
            role_ids.add(int(c.role_id))
        code = (c.equipment_type or "").strip()
        if code:
            role_codes.add(code)
        if c.role_id and getattr(c, "role", None) is not None:
            rc = (c.role.code or "").strip()
            if rc:
                role_codes.add(rc)

    return [
        r
        for r in roles
        if int(r.id) in role_ids or (r.code or "").strip() in role_codes
    ]


def create_client_booking(
    *,
    client: BotUser,
    role,
    description: str,
    contractor_id: int,
    slot_label: str,
) -> WorkRequest:
    """Создать заявку с предварительным окном у выбранного мастера."""
    if not role_uses_client_booking(role):
        raise ValueError("Для этой роли запись к мастеру недоступна.")
    desc = (description or "").strip()
    if len(desc) < 5:
        raise ValueError("Опишите задачу подробнее (минимум 5 символов).")
    slot = (slot_label or "").strip()
    if not slot:
        raise ValueError("Выберите время записи.")
    parsed = parse_slot_datetime_range(slot)
    if not parsed:
        raise ValueError("Некорректное окно времени.")
    start, end = parsed
    slot = format_slot_label(start, end)

    contractor = (
        ContractorProfile.objects.select_related("user", "role")
        .filter(pk=contractor_id, status=ContractorStatus.VERIFIED)
        .first()
    )
    if not contractor:
        raise ValueError("Мастер не найден.")
    assert_not_self_assignment(client.id, contractor)
    role_ok = (contractor.role_id and contractor.role_id == role.id) or (
        (contractor.equipment_type or "") == (role.code or "")
    )
    if not role_ok:
        raise ValueError("Мастер не работает по этой роли.")

    if contractor_blocked_for_commission(contractor):
        raise ValueError(COMMISSION_BLOCK_MSG)

    # Слот свободен по всем ролям этого исполнителя?
    if slot_overlaps_user_busy(contractor.user_id, start, end):
        raise ValueError(
            "Это окно уже занято у мастера по другой заявке. Выберите другое время."
        )

    requires_photos = bool(getattr(role, "requires_work_photos", True))
    status = WorkRequestStatus.DRAFT if requires_photos else WorkRequestStatus.SCHEDULING

    with transaction.atomic():
        # Повторная проверка под блокировкой от гонок двух ролей.
        locked = (
            WorkRequest.objects.select_for_update()
            .filter(assigned_contractor__user_id=contractor.user_id)
            .exclude(agreed_slot="")
            .only("id", "agreed_slot", "status", "client_prebooked")
        )
        now = timezone.localtime(timezone.now())
        for other in locked[:300]:
            if other.status not in _active_busy_statuses() and not (
                other.status == WorkRequestStatus.DRAFT and other.client_prebooked
            ):
                continue
            parsed_o = parse_slot_datetime_range(other.agreed_slot or "", ref_now=now)
            if not parsed_o:
                continue
            o0, o1 = parsed_o
            if _overlaps(start, end, o0, o1):
                raise ValueError(
                    "Это окно уже занято у мастера по другой заявке. "
                    "Выберите другое время."
                )

        wr = WorkRequest.objects.create(
            user=client,
            role=role,
            description=desc[:4000],
            status=status,
            client_locality=(client.locality or "").strip()[:255],
            assigned_contractor=contractor,
            agreed_slot=slot[:255],
            proposed_slots=[],
            client_prebooked=True,
            schedule_agreed_at=None,
        )

    if status == WorkRequestStatus.SCHEDULING:
        _notify_master_prebooking(wr)

    try:
        ActivityLog.objects.create(
            user=client,
            kind=ActivityKind.WORK_REQUEST,
            title=f"Запись к мастеру #{wr.id}",
            detail=f"{role.name}: {slot}",
        )
    except Exception:
        logger.exception("activity prebook WR %s", wr.id)
    return wr


def activate_prebooking_after_photos(wr: WorkRequest) -> None:
    """После submit черновика с записью — в scheduling и уведомить мастера."""
    if not wr.client_prebooked or not wr.assigned_contractor_id:
        return
    if wr.status != WorkRequestStatus.DRAFT:
        # Уже pending после стандартного submit — переводим в scheduling
        pass
    wr.status = WorkRequestStatus.SCHEDULING
    wr.save(update_fields=["status", "updated_at"])
    _notify_master_prebooking(wr)


def confirm_client_booking(wr: WorkRequest, master: BotUser) -> str:
    """Мастер подтверждает предварительное окно и берёт заказ в работу."""
    if not wr.assigned_contractor_id or wr.assigned_contractor.user_id != master.id:
        raise ValueError("Вы не назначены на эту заявку.")
    if wr.status != WorkRequestStatus.SCHEDULING:
        raise ValueError("Сейчас нельзя подтвердить эту запись.")
    if not wr.client_prebooked or not (wr.agreed_slot or "").strip():
        raise ValueError("Нет предварительной записи для подтверждения.")
    if contractor_blocked_for_commission(wr.assigned_contractor):
        raise ValueError(MASTER_COMMISSION_BLOCK_MSG)

    from services.work_request_schedule import _confirm_agreed_slot

    msg = _confirm_agreed_slot(wr, wr.agreed_slot, pending=None)
    wr.refresh_from_db()
    # client_prebooked оставляем True как метку источника; schedule_agreed_at уже выставлен
    return msg or "Запись подтверждена — заявка в работе."


def _notify_master_prebooking(wr: WorkRequest) -> None:
    contractor = wr.assigned_contractor
    if not contractor:
        return
    master = contractor.user
    text = (
        f"Клиент записался к вам по заявке #{wr.id} ({wr.role.name}).\n"
        f"Предварительное время: {wr.agreed_slot}\n"
        f"Клиент: {wr.user}\n"
        f"Тел.: {(wr.user.phone or '—')}\n"
        f"Описание: {(wr.description or '')[:400]}\n\n"
        "Подтвердите запись в приложении (Работа) и возьмите заказ в работу. "
        "Если по прошлому заказу не оплачена комиссия — сначала отправьте чек."
    )
    try:
        from services.work_request_dispatch import _default_send_fn

        _default_send_fn()(master, text)
    except Exception:
        logger.exception("notify master prebook WR %s", wr.id)
    try:
        from api.emit import emit_app_event

        emit_app_event(
            master,
            ntype="work_request.prebooked",
            title="Новая запись от клиента",
            body=text[:500],
            entity_type="work_request",
            entity_id=wr.id,
        )
    except Exception:
        logger.exception("app inbox prebook WR %s", wr.id)
    try:
        from api.emit import emit_app_event

        emit_app_event(
            wr.user,
            ntype="work_request.prebooked",
            title="Заявка отправлена мастеру",
            body=(
                f"Вы записались к {master} на {wr.agreed_slot}. "
                "Мастер подтвердит время и возьмёт заказ в работу."
            )[:500],
            entity_type="work_request",
            entity_id=wr.id,
        )
    except Exception:
        logger.exception("app inbox client prebook WR %s", wr.id)
