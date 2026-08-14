"""Каталог ролей исполнителей и подсказки для кампаний."""

from __future__ import annotations

import re

from database.models import EquipmentType, ExecutorRole, ServiceCampaign, ServiceCategory


def active_roles():
    return ExecutorRole.objects.filter(is_active=True).order_by("sort_order", "name")


def role_by_code(code: str) -> ExecutorRole | None:
    code = (code or "").strip().lower()
    if not code:
        return None
    return ExecutorRole.objects.filter(code=code, is_active=True).first()


def ensure_default_equipment_roles() -> None:
    """Гарантирует tractor/truck в каталоге (на случай пустой БД в тестах)."""
    defaults = [
        ("tractor", "Трактор-погрузчик", True, True, True, False, 10),
        ("truck", "Камаз / грузовой", True, True, True, True, 20),
    ]
    for code, name, equip, snow, road, haul, sort in defaults:
        ExecutorRole.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "is_equipment": equip,
                "for_snow": snow,
                "for_road": road,
                "for_snow_haul": haul,
                "sort_order": sort,
                "is_active": True,
            },
        )


def suggested_role_codes_for_campaign(campaign: ServiceCampaign) -> list[str]:
    ensure_default_equipment_roles()
    if campaign.category == ServiceCategory.SNOW:
        codes = list(
            ExecutorRole.objects.filter(is_active=True, for_snow=True)
            .exclude(for_snow_haul=True)
            .values_list("code", flat=True)
        )
        if campaign.needs_snow_haul:
            codes.extend(
                list(
                    ExecutorRole.objects.filter(
                        is_active=True, for_snow_haul=True
                    ).values_list("code", flat=True)
                )
            )
        if not codes:
            codes = [EquipmentType.TRACTOR]
            if campaign.needs_snow_haul:
                codes.append(EquipmentType.TRUCK)
        return codes
    if campaign.category == ServiceCategory.ROAD:
        codes = list(
            ExecutorRole.objects.filter(is_active=True, for_road=True).values_list(
                "code", flat=True
            )
        )
        return codes or [EquipmentType.TRACTOR, EquipmentType.TRUCK]
    return []


def format_roles_list(roles=None) -> str:
    roles = list(roles if roles is not None else active_roles())
    if not roles:
        return "Список ролей пуст — обратитесь к администратору."
    lines = []
    for i, role in enumerate(roles, start=1):
        mark = " 📄" if role.requires_qualification_docs else ""
        lines.append(f"{i}. {role.name}{mark}")
    return "\n".join(lines)


def match_role_from_text(text: str, roles=None) -> ExecutorRole | None:
    """Сопоставить текст с ролью: номер из списка или подстрока названия/кода."""
    roles = list(roles if roles is not None else active_roles())
    raw = (text or "").strip().lower().replace("ё", "е")
    if not raw or not roles:
        return None
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(roles):
            return roles[idx]
    # прямые алиасы для техники
    aliases = {
        "трактор": "tractor",
        "тракторист": "tractor",
        "погрузчик": "tractor",
        "камаз": "truck",
        "грузовик": "truck",
        "грузовой": "truck",
        "разнорабочий": "handyman",
        "каменщик": "mason",
        "плиточник": "tiler",
        "электрик": "electrician",
        "сварщик": "welder",
        "грузчик": "loader",
        "компьютерный мастер": "computer_master",
        "компьютерщик": "computer_master",
        "маникюр": "manicure",
        "мастер по маникюру": "manicure",
        "репетитор математики": "tutor_math",
        "математика": "tutor_math",
        "репетитор русского": "tutor_russian",
        "русский язык": "tutor_russian",
        "репетитор биологии": "tutor_biology",
        "биология": "tutor_biology",
        "репетитор английского": "tutor_english",
        "английский": "tutor_english",
    }
    for key, code in aliases.items():
        if key in raw:
            for role in roles:
                if role.code == code:
                    return role
    for role in roles:
        name = role.name.lower().replace("ё", "е")
        if name in raw or role.code in raw:
            return role
        # отдельные слова названия
        if len(name) >= 4 and name in raw:
            return role
    return None


def extract_role_from_call_phrase(text: str) -> ExecutorRole | None:
    """«нужен электрик», «вызови грузчика» → роль."""
    raw = (text or "").strip().lower().replace("ё", "е")
    m = re.search(
        r"(?:нужен|нужна|нужно|вызвать|вызови|позови|требуется|ищу)\s+(.+)$",
        raw,
    )
    chunk = m.group(1).strip() if m else raw
    return match_role_from_text(chunk)
