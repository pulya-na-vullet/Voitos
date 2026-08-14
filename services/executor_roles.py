"""Каталог ролей исполнителей и подсказки для кампаний."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from database.models import EquipmentType, ExecutorRole, ServiceCampaign, ServiceCategory

# Системные признаки: если админ вводит совпадающую подпись — включаем boolean для бота.
SYSTEM_FLAG_DEFS: list[tuple[str, str]] = [
    ("requires_qualification_docs", "нужны подтверждающие документы"),
    ("is_equipment", "техника (госномер)"),
    ("for_snow", "снег"),
    ("for_road", "дорога"),
    ("for_snow_haul", "вывоз снега"),
]
SYSTEM_FLAG_CODES = {code for code, _ in SYSTEM_FLAG_DEFS}
SYSTEM_FLAG_LABELS = dict(SYSTEM_FLAG_DEFS)
_SYSTEM_LABEL_TO_CODE = {
    label.lower(): code for code, label in SYSTEM_FLAG_DEFS
}
# Доп. алиасы подписей → системный code
_SYSTEM_LABEL_TO_CODE.update(
    {
        "нужны подтверждающие документы о квалификации": "requires_qualification_docs",
        "техника (госномер / модель)": "is_equipment",
        "для уборки снега": "for_snow",
        "для дорожных работ": "for_road",
        "нужен при вывозе снега": "for_snow_haul",
    }
)


def active_roles():
    return ExecutorRole.objects.filter(is_active=True).order_by("id")


def role_by_code(code: str) -> ExecutorRole | None:
    code = (code or "").strip().lower()
    if not code:
        return None
    return ExecutorRole.objects.filter(code=code, is_active=True).first()


def generate_role_code() -> str:
    """Служебный код роли (админ его не вводит)."""
    for _ in range(32):
        code = f"r_{uuid.uuid4().hex[:12]}"
        if not ExecutorRole.objects.filter(code=code).exists():
            return code
    raise RuntimeError("Не удалось сгенерировать уникальный код роли")


def _normalize_flag_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    label = str(raw.get("label") or "").strip()[:128]
    code = str(raw.get("code") or "").strip().lower()[:64]
    if not label and code in SYSTEM_FLAG_LABELS:
        label = SYSTEM_FLAG_LABELS[code]
    if not label:
        return None
    # Подпись админа может включить системный признак
    mapped = _SYSTEM_LABEL_TO_CODE.get(label.lower().replace("ё", "е"))
    if mapped:
        code = mapped
        label = SYSTEM_FLAG_LABELS[mapped]
    elif code in SYSTEM_FLAG_LABELS:
        label = SYSTEM_FLAG_LABELS[code]
    elif not code:
        code = make_custom_flag_code(label)
    on = raw.get("on", True)
    if isinstance(on, str):
        on = on.strip().lower() in {"1", "true", "on", "yes"}
    else:
        on = bool(on)
    return {"code": code, "label": label, "on": on}


def flags_from_role(role: ExecutorRole) -> list[dict[str, Any]]:
    """Только то, что админ сохранил в flags (без автоподстановки)."""
    stored = role.flags if isinstance(role.flags, list) else []
    return [item for item in (_normalize_flag_item(x) for x in stored) if item]


# Документы и техника задаются отдельными галочками в форме, не в списке признаков.
FORM_SYSTEM_FLAG_CODES = frozenset(
    {"requires_qualification_docs", "is_equipment"}
)


def custom_flags_from_role(role: ExecutorRole) -> list[dict[str, Any]]:
    """Свободные признаки для UI (без документов/техники)."""
    return [
        f
        for f in flags_from_role(role)
        if f["code"] not in FORM_SYSTEM_FLAG_CODES
    ]


def apply_flags_to_role(role: ExecutorRole, flags: list[dict[str, Any]]) -> None:
    """Записать flags и синхронизировать системные boolean-поля."""
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in flags:
        norm = _normalize_flag_item(item)
        if not norm or norm["code"] in seen:
            continue
        # Признак в списке = включён
        norm["on"] = True
        seen.add(norm["code"])
        cleaned.append(norm)
    role.flags = cleaned
    enabled = {f["code"] for f in cleaned}
    for code, _label in SYSTEM_FLAG_DEFS:
        setattr(role, code, code in enabled)


def apply_role_form_fields(role: ExecutorRole, post) -> None:
    """Признаки из JSON + галочки документов/техники из формы."""
    flags = [
        f
        for f in parse_flags_from_post(post)
        if f["code"] not in FORM_SYSTEM_FLAG_CODES
    ]
    if post.get("requires_qualification_docs"):
        flags.insert(
            0,
            {
                "code": "requires_qualification_docs",
                "label": SYSTEM_FLAG_LABELS["requires_qualification_docs"],
                "on": True,
            },
        )
    if post.get("is_equipment"):
        flags.insert(
            0 if not post.get("requires_qualification_docs") else 1,
            {
                "code": "is_equipment",
                "label": SYSTEM_FLAG_LABELS["is_equipment"],
                "on": True,
            },
        )
    apply_flags_to_role(role, flags)


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
    out: list[dict[str, Any]] = []
    for i, code in enumerate(codes):
        label = labels[i] if i < len(labels) else ""
        item = _normalize_flag_item({"code": code, "label": label, "on": True})
        if item:
            out.append(item)
    return out


def make_custom_flag_code(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (label or "").lower().replace("ё", "е")).strip("_")
    slug = (slug or "flag")[:40]
    return f"custom_{slug}_{uuid.uuid4().hex[:6]}"


def ensure_default_equipment_roles() -> None:
    """Больше не сидируем роли — каталог заполняет администратор вручную."""
    return


def suggested_role_codes_for_campaign(campaign: ServiceCampaign) -> list[str]:
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
    # прямые алиасы для техники / частых ролей (если такие роли есть в каталоге)
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
        if len(name) >= 4 and name in raw:
            return role
    # совпадение по ключевому слову из названия роли («электрик» в «Электрик»)
    for role in roles:
        name = role.name.lower().replace("ё", "е")
        for part in re.split(r"[\s/,\-]+", name):
            if len(part) >= 4 and part in raw:
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
