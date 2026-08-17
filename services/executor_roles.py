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
    ("accepts_at_home", "мастер принимает на дому"),
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
    {"requires_qualification_docs", "is_equipment", "accepts_at_home"}
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
    if post.get("accepts_at_home"):
        flags.append(
            {
                "code": "accepts_at_home",
                "label": SYSTEM_FLAG_LABELS["accepts_at_home"],
                "on": True,
            },
        )
    apply_flags_to_role(role, flags)
    # Гарантия boolean даже если флаг не в JSON-списке
    role.accepts_at_home = bool(post.get("accepts_at_home"))
    role.requires_qualification_docs = bool(post.get("requires_qualification_docs"))
    role.is_equipment = bool(post.get("is_equipment"))
    role.requires_work_photos = bool(post.get("requires_work_photos"))


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


def _parse_role_list_number(text: str) -> int | None:
    """«1», «1.», «№2», «число 3» → номер пункта списка (1-based)."""
    raw = (text or "").strip().lower().replace("ё", "е")
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    m = re.match(r"^(?:№\s*)?(\d{1,3})(?:\s*[.)\-:])?\s*$", raw)
    if m:
        return int(m.group(1))
    m = re.match(r"^(?:номер|пункт|вариант)\s*[№#]?\s*(\d{1,3})\s*$", raw)
    if m:
        return int(m.group(1))
    return None


def match_role_from_text(text: str, roles=None) -> ExecutorRole | None:
    """Сопоставить текст с ролью: номер из списка админа или название."""
    roles = list(roles if roles is not None else active_roles())
    raw = (text or "").strip().lower().replace("ё", "е")
    if not raw or not roles:
        return None

    # Голые «мастер/исполнитель» — не роль, нужен выбор из списка
    if _is_generic_executor_phrase(raw):
        return None

    num = _parse_role_list_number(raw)
    if num is not None:
        idx = num - 1
        if 0 <= idx < len(roles):
            return roles[idx]
        return None

    # Точное совпадение названия
    for role in roles:
        name = (role.name or "").lower().replace("ё", "е").strip()
        if name and raw == name:
            return role

    # Полное название роли внутри фразы (длиннее общих слов)
    # Сортируем по длине имени — более специфичные первыми
    named = sorted(
        roles,
        key=lambda r: len((r.name or "").strip()),
        reverse=True,
    )
    for role in named:
        name = (role.name or "").lower().replace("ё", "е").strip()
        if len(name) < 4:
            continue
        if name in raw or (len(raw) >= 4 and raw in name and not _is_generic_executor_phrase(raw)):
            # «мастера» не должно матчить «компьютерный мастер» через raw in name
            if raw in name and raw != name and _is_generic_executor_phrase(raw):
                continue
            if len(raw) < len(name) and _stem_is_generic(raw):
                continue
            return role

    # Слова из названия (длиннее 3 символов), кроме общих «мастер» и т.п.
    for role in named:
        name = (role.name or "").lower().replace("ё", "е")
        for part in re.split(r"[\s/,\-]+", name):
            part = part.strip()
            if len(part) < 4 or _stem_is_generic(part):
                continue
            if part in raw or (len(raw) >= 4 and raw in part):
                return role

    # Устаревшие коды (на случай ручных code=tractor и т.п.)
    aliases = {
        "трактор": "tractor",
        "тракторист": "tractor",
        "погрузчик": "tractor",
        "камаз": "truck",
        "грузовик": "truck",
        "электрик": "electrician",
        "сварщик": "welder",
        "грузчик": "loader",
    }
    for key, code in aliases.items():
        if key in raw:
            for role in roles:
                if role.code == code:
                    return role
            for role in roles:
                name = (role.name or "").lower().replace("ё", "е")
                if key in name:
                    return role
    return None


_GENERIC_EXECUTOR_STEMS = frozenset(
    {
        "мастер",
        "мастера",
        "мастеру",
        "мастером",
        "мастере",
        "исполнитель",
        "исполнителя",
        "исполнителю",
        "исполнителем",
        "специалист",
        "специалиста",
        "специалисту",
        "человека",
        "работника",
        "кого",
        "кого-нибудь",
        "любого",
    }
)


def _stem_is_generic(token: str) -> bool:
    t = (token or "").strip().lower().replace("ё", "е")
    if t in _GENERIC_EXECUTOR_STEMS:
        return True
    # «мастеров», «мастерами»
    for stem in ("мастер", "исполнитель", "специалист"):
        if t.startswith(stem) and len(t) <= len(stem) + 3:
            return True
    return False


def _is_generic_executor_phrase(text: str) -> bool:
    raw = (text or "").strip().lower().replace("ё", "е")
    raw = re.sub(r"[^a-zа-я0-9\s\-]+", " ", raw)
    parts = [p for p in raw.split() if p]
    if not parts:
        return True
    return all(_stem_is_generic(p) for p in parts)


def extract_role_from_call_phrase(text: str) -> ExecutorRole | None:
    """«нужен электрик», «вызови грузчика» → роль. «вызвать мастера» → None (список)."""
    raw = (text or "").strip().lower().replace("ё", "е")
    m = re.search(
        r"(?:нужен|нужна|нужно|вызвать|вызови|позови|требуется|ищу|заказать)\s+(.+)$",
        raw,
    )
    chunk = (m.group(1).strip() if m else raw).strip(" .,!")
    if _is_generic_executor_phrase(chunk):
        return None
    return match_role_from_text(chunk)


def looks_like_work_request_call(text: str) -> bool:
    """
    True для «вызвать мастера», «нужен <роль из каталога>» и т.п.
    Роли из панели подхватываются через match_role_from_text.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    lower = raw.lower().replace("ё", "е")
    # Явные общие фразы
    if re.match(
        r"^\s*(/)?("
        r"вызвать\s+исполнителя|вызови\s+исполнителя|нужен\s+исполнитель|"
        r"заказать\s+мастера|вызвать\s+мастера|нужен\s+мастер|нужна\s+мастер"
        r")\s*[.!]?\s*$",
        lower,
    ):
        return True
    m = re.search(
        r"(?:нужен|нужна|нужно|вызвать|вызови|позови|требуется|ищу|заказать)\s+(.+)$",
        lower,
    )
    if not m:
        return False
    chunk = m.group(1).strip(" .,!")
    if _is_generic_executor_phrase(chunk):
        return True
    return match_role_from_text(chunk) is not None
