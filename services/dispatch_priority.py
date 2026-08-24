"""Приоритет команды Voitos и загрузка слотов на сегодня."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

from django.utils import timezone

from database.models import (
    AssignmentStatus,
    CampaignAssignment,
    ContractorProfile,
    WorkRequestOffer,
    WorkRequestOfferStatus,
)

_PEER_ACTIVE = {
    AssignmentStatus.OFFERED,
    AssignmentStatus.COUNTER_OFFER,
    AssignmentStatus.ACCEPTED,
}


def contractor_has_capacity_today(contractor: ContractorProfile) -> bool:
    """
    Есть ли у исполнителя свободный слот на сегодня.

    Учитываем открытый оффер заявки, назначения на сборы и календарь
    согласованных визитов. Если окон календаря нет, но активных дел тоже нет
    (вечер / выходной) — считаем свободным, чтобы не блокировать диспетчеризацию.
    """
    if contractor is None:
        return False
    if WorkRequestOffer.objects.filter(
        contractor=contractor,
        status=WorkRequestOfferStatus.OFFERED,
    ).exists():
        return False

    now = timezone.localtime(timezone.now())
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    if CampaignAssignment.objects.filter(
        contractor=contractor,
        status__in=_PEER_ACTIVE,
        scheduled_at__gte=day_start,
        scheduled_at__lt=day_end,
    ).exists():
        # Уже есть сервисная задача на сегодня — проверим свободные окна календаря.
        pass

    from services.master_booking import busy_intervals_for_user, free_slots_for_contractor

    free = free_slots_for_contractor(contractor, days=1, now=now)
    if free:
        return True

    busy = busy_intervals_for_user(
        contractor.user_id,
        range_start=day_start,
        range_end=day_end,
    )
    has_campaign_today = CampaignAssignment.objects.filter(
        contractor=contractor,
        status__in=_PEER_ACTIVE,
        scheduled_at__gte=day_start,
        scheduled_at__lt=day_end,
    ).exists()
    if busy or has_campaign_today:
        return False
    return True


def prioritize_dispatch_candidates(
    candidates: list[tuple[ContractorProfile, float, str]],
) -> list[tuple[ContractorProfile, float, str]]:
    """
    Сначала члены команды Voitos со свободным слотом на сегодня.
    Если таких нет (все заняты или команды нет) — низкоприоритетные.
    """
    if not candidates:
        return []
    team_free: list[tuple[ContractorProfile, float, str]] = []
    others: list[tuple[ContractorProfile, float, str]] = []
    for item in candidates:
        c, score, reason = item
        if getattr(c, "is_voitos_team", False) and contractor_has_capacity_today(c):
            team_free.append(
                (c, score, f"{reason}; приоритет Voitos" if reason else "приоритет Voitos")
            )
        elif not getattr(c, "is_voitos_team", False):
            others.append(item)
        # члены команды без слотов на сегодня пропускаем — каскад на остальных
    pool = team_free if team_free else others
    pool.sort(key=lambda x: (-x[1], 0 if getattr(x[0], "is_voitos_team", False) else 1, x[0].id))
    return pool


def campaign_work_address(campaign) -> str:
    """Адрес работ: название группы (напр. «9 аллея») + НП сбора."""
    parts: list[str] = []
    group = getattr(campaign, "group", None)
    if group is not None:
        name = (group.name or "").strip()
        if name:
            parts.append(name)
    loc = (getattr(campaign, "locality", None) or "").strip()
    if loc and loc not in parts:
        parts.append(loc)
    if not parts:
        title = (getattr(campaign, "title", None) or "").strip()
        if title:
            parts.append(title)
    return ", ".join(parts)


def maps_route_urls(address: str) -> dict[str, str]:
    """Ссылки маршрута от текущего местоположения в Яндекс.Карты и 2ГИС."""
    addr = (address or "").strip()
    if not addr:
        return {"yandex_maps_url": "", "dgis_maps_url": "", "address": ""}
    q = quote(addr)
    return {
        "address": addr,
        "yandex_maps_url": f"https://yandex.ru/maps/?rtext=~{q}&rtt=auto",
        "dgis_maps_url": f"https://2gis.ru/search/{q}",
    }


def peer_contact_payload(assignment: CampaignAssignment) -> dict:
    contractor = assignment.contractor
    user = contractor.user
    phone = (contractor.phone or user.phone or "").strip()
    uname = (user.username or "").strip().lstrip("@")
    from services.contractors import max_profile_link

    return {
        "assignment_id": assignment.id,
        "name": str(user),
        "role_label": assignment.get_equipment_type_display(),
        "equipment_label": (contractor.equipment_label or "").strip(),
        "phone": phone[:64],
        "max_username": uname[:64],
        "max_profile_url": max_profile_link(user),
        "plate_number": (contractor.plate_number or "").strip()[:32],
    }
