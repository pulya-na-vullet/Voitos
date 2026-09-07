"""Канонический населённый пункт: эвристика + ИИ, без Redis.

«Куюки», «куюки», «Куюки, 24 квартал дом 1 строение 1» → «Куюки».
"""

from __future__ import annotations

import logging
import re
from collections import Counter

from subscriptions.receipts import _extract_json

logger = logging.getLogger(__name__)

_TYPE_RE = re.compile(
    r"\b(г|гор|город|пгт|село|деревня|пос|поселок|посёлок|рп)\b\.?",
    re.IGNORECASE,
)
_ADDR_MARK_RE = re.compile(
    r"\b(ул|улица|пр|проспект|пер|переулок|кв|квартира|дом|д|квартал|"
    r"корп|корпус|стр|строение|литер|офис|подъезд)\b",
    re.IGNORECASE,
)
_HAS_DIGIT_RE = re.compile(r"\d")


def normalize_locality(value: str) -> str:
    text = (value or "").lower().replace("ё", "е").strip()
    text = _TYPE_RE.sub(" ", text)
    text = re.sub(r"[^\w\d]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def looks_like_address(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if "," in text and _HAS_DIGIT_RE.search(text):
        return True
    if _ADDR_MARK_RE.search(text) and _HAS_DIGIT_RE.search(text):
        return True
    return False


def _pretty_name(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text:
        return ""
    if " " in text:
        return text.title()
    return text[:1].upper() + text[1:]


def _seed_known_settlements() -> list[str]:
    """Известные НП: поле группы + анкеты жителей и мастеров."""
    from database.models import BotUser, ContractorProfile, ServiceGroup

    found: dict[str, str] = {}

    def _add(raw: str) -> None:
        name = (raw or "").strip()
        if len(name) < 2:
            return
        first = name.split(",")[0].strip()
        if looks_like_address(first) or _HAS_DIGIT_RE.search(first):
            return
        if _ADDR_MARK_RE.search(first):
            return
        key = normalize_locality(first)
        if len(key) < 3:
            return
        pretty = _pretty_name(first)
        prev = found.get(key)
        if prev is None or (prev[:1].islower() and pretty[:1].isupper()):
            found[key] = pretty

    try:
        for loc in ServiceGroup.objects.exclude(locality="").values_list(
            "locality", flat=True
        ):
            _add(loc.split(",")[0] if loc else "")
        for loc in ContractorProfile.objects.exclude(locality="").values_list(
            "locality", flat=True
        )[:2000]:
            _add(loc.split(",")[0] if loc else "")
        for loc in BotUser.objects.exclude(locality="").values_list("locality", flat=True)[
            :2000
        ]:
            _add(loc.split(",")[0] if loc else "")
    except Exception:
        logger.exception("Could not load known settlements")
    return sorted(found.values(), key=lambda s: s.casefold())


def known_settlements() -> list[str]:
    """Канонические НП для подсказок в панели."""
    return _seed_known_settlements()


def match_known_settlement(raw: str, known: list[str]) -> str:
    nraw = normalize_locality(raw)
    if not nraw:
        return ""
    contained = ""
    contained_len = 0
    for name in known:
        nk = normalize_locality(name)
        if len(nk) < 3:
            continue
        if nk == nraw:
            return name
        # НП входит в адрес («Куюки, 24 квартал»), но не наоборот
        # («куюки» не должно стать «Куюки двор»).
        if nk in nraw and len(nk) > contained_len:
            contained = name
            contained_len = len(nk)
    return contained


def heuristic_settlement(raw: str, known: list[str] | None = None) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    known = list(known or ())
    hit = match_known_settlement(text, known)
    if hit:
        return hit
    first = text.split(",")[0].strip()
    first = _TYPE_RE.sub(" ", first)
    first = re.sub(r"\s+", " ", first).strip(" ,.;")
    if first and not looks_like_address(first) and not _HAS_DIGIT_RE.search(first):
        hit = match_known_settlement(first, known)
        return hit or _pretty_name(first)
    stripped = _ADDR_MARK_RE.sub(" ", text)
    stripped = _HAS_DIGIT_RE.sub(" ", stripped)
    stripped = re.sub(r"[^\w\s-]+", " ", stripped, flags=re.UNICODE)
    leftover = re.sub(r"\s+", " ", stripped).strip()
    if leftover:
        hit = match_known_settlement(leftover, known)
        if hit:
            return hit
        token = leftover.split(",")[0].strip()
        if token and not looks_like_address(token):
            return _pretty_name(token)
    return _pretty_name(first) if first else ""


def _ai_settlement(raw: str, known: list[str]) -> str:
    from ai.factory import AINotConfiguredError, get_llm_provider

    try:
        llm = get_llm_provider()
    except AINotConfiguredError:
        return ""
    except Exception:
        logger.exception("LLM provider unavailable for locality")
        return ""
    known_line = ", ".join(known[:40]) or "—"
    system = (
        "Ты нормализуешь населённый пункт в Татарстане / России.\n"
        "Верни ТОЛЬКО JSON: {\"settlement\": \"Название\"}.\n"
        "Название — посёлок, село или город, без улицы, дома, квартала, корпуса.\n"
        "Если текст относится к одному из известных НП — верни его каноническое написание."
    )
    user = f"Известные НП: {known_line}\nТекст пользователя: {raw}"
    try:
        blob = llm.complete_text(system, user, temperature=0.0, max_tokens=120)
        data = _extract_json(blob) or {}
        name = str(data.get("settlement") or "").strip()
        if len(name) < 2 or looks_like_address(name):
            return ""
        hit = match_known_settlement(name, known)
        return hit or _pretty_name(name.split(",")[0])
    except Exception:
        logger.exception("AI locality canonicalize failed")
        return ""


def canonicalize_locality(
    raw: str,
    *,
    extra: str = "",
    use_ai: bool = False,
    known: list[str] | None = None,
) -> str:
    """Свести кривой ввод к названию НП. use_ai — при регистрации исполнителя."""
    blob = ", ".join(p for p in ((raw or "").strip(), (extra or "").strip()) if p)
    if not blob:
        return ""
    names = known if known is not None else _seed_known_settlements()
    heur = heuristic_settlement(blob, names)
    if heur and match_known_settlement(heur, names):
        return heur
    if use_ai:
        ai = _ai_settlement(blob, names)
        if ai:
            return ai
    return heur


def localities_match(a: str, b: str) -> bool:
    na, nb = normalize_locality(a), normalize_locality(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 3 and len(nb) >= 3 and (na in nb or nb in na):
        return True
    ca = heuristic_settlement(a)
    cb = heuristic_settlement(b)
    if ca and cb and normalize_locality(ca) == normalize_locality(cb):
        return True
    return False


def locality_bucket_label(raw: str, known: list[str] | None = None) -> str:
    name = canonicalize_locality(raw, use_ai=False, known=known)
    return name or "Без населённого пункта"


def settlement_for_group(group, known: list[str] | None = None) -> str:
    """НП группы: поле locality, иначе известный город в названии, иначе жители.

    Название «Двор» / «ул. А» само по себе не город — не подставляем его как НП,
    иначе «вызвать мастера» не находит мастеров Куюков.
    """
    if group is None:
        return ""
    names = known if known is not None else _seed_known_settlements()
    explicit = canonicalize_locality(
        getattr(group, "locality", None) or "",
        use_ai=False,
        known=names,
    )
    if explicit:
        return explicit
    raw_name = (group.name or "").strip()
    hit = match_known_settlement(raw_name, names)
    if hit:
        # «Куюки двор» → Куюки, а не само название группы.
        if normalize_locality(hit) == normalize_locality(raw_name):
            others = [
                n
                for n in names
                if normalize_locality(n) != normalize_locality(raw_name)
            ]
            shorter = match_known_settlement(raw_name, others)
            if shorter:
                return shorter
        return hit
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    members = getattr(group, "members", None)
    if members is not None:
        for user in members.all().only("locality", "address"):
            loc = canonicalize_locality(
                user.locality or "",
                extra=user.address or "",
                use_ai=False,
                known=names,
            )
            if not loc:
                continue
            key = normalize_locality(loc)
            counts[key] += 1
            display[key] = loc
    if counts:
        top = counts.most_common(1)[0][0]
        return display[top]
    return ""


def settlement_for_client(user, group=None, *, group_id=None, use_ai: bool = False) -> str:
    """НП для «вызвать мастера»: НП группы, иначе анкета жителя."""
    known = _seed_known_settlements()
    resolved_group = group
    if resolved_group is None and group_id:
        try:
            from services.group_chat import require_group_member

            resolved_group = require_group_member(user, int(group_id))
        except (TypeError, ValueError):
            resolved_group = None
        except Exception:
            logger.exception(
                "Could not resolve group %s for user %s",
                group_id,
                getattr(user, "id", None),
            )
            resolved_group = None
    if resolved_group is not None:
        s = settlement_for_group(resolved_group, known=known)
        if s:
            return s
    return canonicalize_locality(
        getattr(user, "locality", None) or "",
        extra=getattr(user, "address", None) or "",
        use_ai=use_ai,
        known=known,
    )


def apply_canonical_locality(raw: str, *, extra: str = "", use_ai: bool = True) -> str:
    """Запись в анкету: ИИ, если настроен, иначе эвристика."""
    return canonicalize_locality(raw, extra=extra, use_ai=use_ai)[:255]
