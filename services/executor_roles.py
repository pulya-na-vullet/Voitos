"""Каталог ролей исполнителей и подсказки для кампаний."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from database.models import EquipmentType, ExecutorRole, ServiceCampaign, ServiceCategory

# Системные признаки: code → boolean-поле на ExecutorRole (логика бота/кампаний).
SYSTEM_FLAG_DEFS: list[tuple[str, str]] = [
    ("requires_qualification_docs", "нужны подтверждающие документы"),
    ("is_equipment", "техника (госномер)"),
    ("for_snow", "снег"),
    ("for_road", "дорога"),
    ("for_snow_haul", "вывоз снега"),
]
SYSTEM_FLAG_CODES = {code for code, _ in SYSTEM_FLAG_DEFS}
SYSTEM_FLAG_LABELS = dict(SYSTEM_FLAG_DEFS)


def active_roles():
    return ExecutorRole.objects.filter(is_active=True).order_by("sort_order", "name")


def role_by_code(code: str) -> ExecutorRole | None:
    code = (code or "").strip().lower()
    if not code:
        return None
    return ExecutorRole.objects.filter(code=code, is_active=True).first()


def _normalize_flag_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code") or "").strip().lower()[:64]
    label = str(raw.get("label") or "").strip()[:128]
    if not label and code in SYSTEM_FLAG_LABELS:
        label = SYSTEM_FLAG_LABELS[code]
    if not code or not label:
        return None
    on = raw.get("on", True)
    if isinstance(on, str):
        on = on.strip().lower() in {"1", "true", "on", "yes"}
    else:
        on = bool(on)
    if code in SYSTEM_FLAG_LABELS:
        label = SYSTEM_FLAG_LABELS[code]
    return {"code": code, "label": label, "on": on}


def flags_from_booleans(role: ExecutorRole) -> list[dict[str, Any]]:
    """Собрать список признаков из boolean-полей (для миграции / fallback)."""
    out: list[dict[str, Any]] = []
    for code, label in SYSTEM_FLAG_DEFS:
        if getattr(role, code, False):
            out.append({"code": code, "label": label, "on": True})
    return out


def flags_from_role(role: ExecutorRole) -> list[dict[str, Any]]:
    stored = role.flags if isinstance(role.flags, list) else []
    normalized = [item for item in (_normalize_flag_item(x) for x in stored) if item]
    if normalized:
        return normalized
    return flags_from_booleans(role)


def apply_flags_to_role(role: ExecutorRole, flags: list[dict[str, Any]]) -> None:
    """Записать flags и синхронизировать системные boolean-поля."""
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in flags:
        norm = _normalize_flag_item(item)
        if not norm or norm["code"] in seen:
            continue
        seen.add(norm["code"])
        cleaned.append(norm)
    role.flags = cleaned
    enabled = {f["code"] for f in cleaned if f.get("on")}
    for code, _label in SYSTEM_FLAG_DEFS:
        setattr(role, code, code in enabled)


def parse_flags_from_post(post) -> list[dict[str, Any]]:
    """Прочитать признаки из POST: flags_json или параллельные списки."""
    raw_json = (post.get("flags_json") or "").strip()
    if raw_json:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            data = []
        if isinstance(data, list):
            return [item for item in (_normalize_flag_item(x) for x in data) if item]

    codes = post.getlist("flag_code")
    labels = post.getlist("flag_label")
    ons = set(post.getlist("flag_on"))
    out: list[dict[str, Any]] = []
    for i, code in enumerate(codes):
        label = labels[i] if i < len(labels) else ""
        item = _normalize_flag_item(
            {"code": code, "label": label, "on": code in ons or str(i) in ons}
        )
        if item:
            out.append(item)
    return out


def make_custom_flag_code(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (label or "").lower().replace("ё", "е")).strip("_")
    slug = (slug or "flag")[:40]
    return f"custom_{slug}_{uuid.uuid4().hex[:6]}"


def ensure_default_equipment_roles() -> None:
    """Гарантирует tractor/truck в каталоге (на случай пустой БД в тестах)."""
    defaults = [
        ("tractor", "Трактор-погрузчик", True, True, True, False, 10),
        ("truck", "Камаз / грузовой", True, True, True, True, 20),
    ]
    for code, name, equip, snow, road, haul, sort in defaults:
        role, created = ExecutorRole.objects.get_or_create(
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
        if created or not role.flags:
            apply_flags_to_role(
                role,
                [
                    {"code": c, "label": SYSTEM_FLAG_LABELS[c], "on": True}
                    for c, on in [
                        ("is_equipment", equip),
                        ("for_snow", snow),
                        ("for_road", road),
                        ("for_snow_haul", haul),
                    ]
                    if on
                ],
            )
            role.save()


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
