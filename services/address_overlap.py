from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass

from database.models import AdminTask, AdminTaskKind, AdminTaskStatus, BotUser
from panel.admin_tasks import task_address_overlap
from subscriptions.receipts import _extract_json

logger = logging.getLogger(__name__)

# Explicit house / apartment / quarter markers in Russian addresses.
_QUARTER_RE = re.compile(
    r"(?:\bквартал\s*[№#]?\s*(\d+)\b|\b(\d+)\s*квартал\b)",
    re.IGNORECASE,
)
_HOUSE_RE = re.compile(r"\b(?:д|дом)\.?\s*[№#]?\s*(\d+[а-яa-z]?)\b", re.IGNORECASE)
_APT_RE = re.compile(r"\b(?:кв|квартира)\.?\s*[№#]?\s*(\d+)\b", re.IGNORECASE)
_BUILDING_RE = re.compile(
    r"\b(?:к|корп|корпус|стр|строение)\.?\s*[№#]?\s*(\d+[а-яa-z]?)\b",
    re.IGNORECASE,
)


def normalize_address(address: str) -> str:
    text = (address or "").lower().replace("ё", "е")
    text = re.sub(r"[\"'«»]", "", text)
    text = re.sub(
        r"\b(г|гор|город|ул|улица|пр|проспект|пер|переулок|д|дом|кв|квартира|корп|корпус|стр|строение)\b\.?",
        " ",
        text,
    )
    text = re.sub(r"[^\w\d]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def address_tokens(address: str) -> set[str]:
    return {t for t in normalize_address(address).split() if len(t) > 1}


def extract_house_parts(address: str) -> dict[str, str | None]:
    """
    Pull structured parts so «квартал 24, дом 1» ≠ «квартал 24, дом 2».

    Neighbors share street/quarter tokens but not the house number.
    """
    text = (address or "").lower().replace("ё", "е")
    quarter = None
    m = _QUARTER_RE.search(text)
    if m:
        quarter = (m.group(1) or m.group(2) or "").lower() or None

    house = None
    m = _HOUSE_RE.search(text)
    if m:
        house = m.group(1).lower()

    apt = None
    m = _APT_RE.search(text)
    if m:
        apt = m.group(1).lower()

    building = None
    m = _BUILDING_RE.search(text)
    if m:
        building = m.group(1).lower()

    if not house:
        # Fallback: last short number that is not the quarter / apt / building.
        nums = re.findall(r"\b(\d+[а-яa-z]?)\b", text)
        skip = {x for x in (quarter, apt, building) if x}
        # Also skip 4-digit years and long ids
        candidates = [
            n.lower()
            for n in nums
            if n.lower() not in skip and not (n.isdigit() and len(n) >= 4)
        ]
        if candidates:
            house = candidates[-1]

    return {
        "quarter": quarter,
        "house": house,
        "apt": apt,
        "building": building,
    }


def same_household_parts(a: dict[str, str | None], b: dict[str, str | None]) -> bool | None:
    """
    Return True/False when house parts clearly agree/disagree.
    None = inconclusive (need token/AI check).
    """
    ha, hb = a.get("house"), b.get("house")
    if ha and hb and ha != hb:
        return False  # different houses → neighbors, not one family
    ba, bb = a.get("building"), b.get("building")
    if ha and hb and ha == hb and ba and bb and ba != bb:
        return False
    aa, ab = a.get("apt"), b.get("apt")
    if ha and hb and ha == hb and aa and ab and aa != ab:
        return False  # same house, different flats
    if ha and hb and ha == hb:
        # Same house; apartments either match or one side unknown
        if aa and ab and aa != ab:
            return False
        return True
    return None


def heuristic_candidates(user: BotUser, *, limit: int = 40) -> list[BotUser]:
    """Find other users whose address likely matches (same household)."""
    addr = (user.address or "").strip()
    if len(addr) < 5:
        return []
    tokens = address_tokens(addr)
    parts = extract_house_parts(addr)
    qs = BotUser.objects.exclude(id=user.id).exclude(address="")
    loc = (user.locality or "").strip()
    if loc:
        qs = qs.filter(locality__icontains=loc[:40])
    candidates: list[tuple[int, BotUser]] = []
    for other in qs.iterator():
        other_tokens = address_tokens(other.address)
        if not other_tokens:
            continue
        other_parts = extract_house_parts(other.address)
        household = same_household_parts(parts, other_parts)
        if household is False:
            continue
        overlap = tokens & other_tokens
        score = len(overlap)
        if score < 1:
            continue
        if household is True:
            score += 3
        elif parts.get("house") and other_parts.get("house"):
            # both have house but inconclusive path shouldn't happen often
            pass
        else:
            # Without clear house match require stronger token overlap
            if score < 3:
                continue
        if score >= 2 or household is True:
            candidates.append((score, other))
    candidates.sort(key=lambda x: -x[0])
    return [u for _, u in candidates[:limit]]


@dataclass
class OverlapMatch:
    candidate: BotUser
    probability: float
    reason: str


def ai_compare_addresses(
    user: BotUser,
    candidates: list[BotUser],
) -> list[OverlapMatch]:
    """Ask Yandex GPT which candidate addresses are the same household."""
    if not candidates:
        return []
    try:
        from ai.factory import get_llm_provider
    except Exception:
        logger.exception("AI provider unavailable")
        return []

    payload = {
        "target": {
            "address": user.address,
            "locality": user.locality,
            "parts": extract_house_parts(user.address),
        },
        "candidates": [
            {
                "candidate_id": c.id,
                "address": c.address,
                "locality": c.locality,
                "parts": extract_house_parts(c.address),
            }
            for c in candidates
        ],
    }
    system = (
        "Ты сравниваешь адреса жителей России.\n"
        "Нужно найти только ОДНО домохозяйство: тот же дом и та же квартира "
        "(или тот же частный дом без квартиры).\n"
        "ВАЖНО:\n"
        "- Разные номера домов (дом 1 и дом 2, даже в одном квартале/на одной улице) "
        "— это СОСЕДИ, не семья. same_household_probability = 0.\n"
        "- Один квартал / одна улица при разных домах — НЕ совпадение.\n"
        "- Игнорируй только сокращения (ул/улица, д/дом) и порядок слов, "
        "если номер дома тот же.\n"
        "- Если номера домов различаются — НЕ включай кандидата в matches.\n"
        "Ответь ТОЛЬКО JSON без пояснений:\n"
        "{\n"
        '  "matches": [\n'
        '    {"candidate_id": 1, "same_household_probability": 0.0, "reason": "..."}\n'
        "  ]\n"
        "}\n"
        "Включай только кандидатов с вероятностью >= 0.85."
    )
    try:
        llm = get_llm_provider()
        raw = llm.complete_text(
            system,
            json.dumps(payload, ensure_ascii=False),
            temperature=0.1,
            max_tokens=1200,
        )
        data = _extract_json(raw)
    except Exception:
        logger.exception("Address AI compare failed for user %s", user.id)
        return []

    by_id = {c.id: c for c in candidates}
    matches: list[OverlapMatch] = []
    target_parts = extract_house_parts(user.address)
    for item in data.get("matches") or []:
        try:
            cid = int(item.get("candidate_id"))
            prob = float(item.get("same_household_probability") or 0)
        except (TypeError, ValueError):
            continue
        if cid not in by_id or prob < 0.85:
            continue
        cand = by_id[cid]
        # Hard reject different house numbers even if the model is wrong
        if same_household_parts(target_parts, extract_house_parts(cand.address)) is False:
            continue
        matches.append(
            OverlapMatch(
                candidate=cand,
                probability=prob,
                reason=str(item.get("reason") or "совпадение адресов"),
            )
        )
    return matches


def find_address_matches(user: BotUser, *, use_ai: bool = True) -> list[OverlapMatch]:
    candidates = heuristic_candidates(user)
    if not candidates:
        return []
    if use_ai:
        ai_matches = ai_compare_addresses(user, candidates)
        # Prefer AI result; empty means «no same household»
        return ai_matches
    # Heuristic-only: keep only clear same-house matches
    parts = extract_house_parts(user.address)
    soft: list[OverlapMatch] = []
    for c in candidates[:8]:
        household = same_household_parts(parts, extract_house_parts(c.address))
        if household is False:
            continue
        if household is True:
            soft.append(
                OverlapMatch(
                    candidate=c,
                    probability=0.9,
                    reason="тот же номер дома",
                )
            )
    return soft


def stable_group_id(user_ids: list[int]) -> int:
    key = ",".join(str(i) for i in sorted(set(user_ids)))
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % (10**9)


def _resolved_overlap_keys() -> tuple[set[int], set[frozenset[int]]]:
    """source_ids and member-sets already closed by admin (done/dismissed)."""
    source_ids: set[int] = set()
    member_sets: set[frozenset[int]] = set()
    qs = AdminTask.objects.filter(
        kind=AdminTaskKind.ADDRESS_OVERLAP,
        source_model="AddressOverlap",
        status__in={AdminTaskStatus.DONE, AdminTaskStatus.DISMISSED},
    ).only("source_id", "meta")
    for task in qs.iterator():
        if task.source_id is not None:
            source_ids.add(int(task.source_id))
        ids = (task.meta or {}).get("user_ids") or []
        try:
            member_sets.add(frozenset(int(i) for i in ids))
        except (TypeError, ValueError):
            continue
    return source_ids, member_sets


def scan_all_addresses(*, use_ai: bool = True) -> list[dict]:
    """
    Scan all users with addresses; create AdminTask for overlap groups.
    Skips groups the admin already resolved (Готово / Скрыть).
    Returns list of created/updated group summaries.
    """
    users = list(BotUser.objects.exclude(address="").order_by("id"))
    seen_pairs: set[tuple[int, int]] = set()
    resolved_sources, resolved_sets = _resolved_overlap_keys()
    # Pairs that already appeared in a resolved task — do not resurface alone
    resolved_pairs: set[tuple[int, int]] = set()
    for members in resolved_sets:
        ids = sorted(members)
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                resolved_pairs.add((a, b))

    groups: list[dict] = []
    for user in users:
        matches = find_address_matches(user, use_ai=use_ai)
        if not matches:
            continue
        # Drop candidates already resolved with this user as a pair
        matches = [
            m
            for m in matches
            if (min(user.id, m.candidate.id), max(user.id, m.candidate.id))
            not in resolved_pairs
        ]
        if not matches:
            continue
        member_ids = [user.id] + [m.candidate.id for m in matches]
        new_pair = False
        for mid in member_ids:
            if mid == user.id:
                continue
            pair = (min(user.id, mid), max(user.id, mid))
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                new_pair = True
        if not new_pair:
            continue
        reason = "; ".join(
            f"#{m.candidate.id} ({m.probability:.0%}: {m.reason})" for m in matches[:5]
        )
        addresses = [user.address] + [m.candidate.address for m in matches]
        member_ids = sorted(set(member_ids))
        source_id = stable_group_id(member_ids)
        member_key = frozenset(member_ids)
        if source_id in resolved_sources or member_key in resolved_sets:
            continue
        task = task_address_overlap(
            group_key=",".join(str(i) for i in member_ids),
            user_ids=member_ids,
            addresses=addresses,
            reason=reason or "возможное совпадение адресов",
            source_id=source_id,
        )
        # If upsert returned an already-resolved task, do not count it
        if task.status != AdminTaskStatus.OPEN:
            continue
        groups.append(
            {
                "task_id": task.id,
                "user_ids": member_ids,
                "reason": reason,
            }
        )
    return groups
