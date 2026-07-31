from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass

from database.models import BotUser
from panel.admin_tasks import task_address_overlap
from subscriptions.receipts import _extract_json

logger = logging.getLogger(__name__)


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


def heuristic_candidates(user: BotUser, *, limit: int = 40) -> list[BotUser]:
    """Find other users whose address likely matches (same locality / token overlap)."""
    addr = (user.address or "").strip()
    if len(addr) < 5:
        return []
    tokens = address_tokens(addr)
    qs = BotUser.objects.exclude(id=user.id).exclude(address="")
    loc = (user.locality or "").strip()
    if loc:
        qs = qs.filter(locality__icontains=loc[:40])
    candidates: list[tuple[int, BotUser]] = []
    for other in qs.iterator():
        other_tokens = address_tokens(other.address)
        if not other_tokens:
            continue
        overlap = tokens & other_tokens
        # Need street-ish overlap + a house number if present
        score = len(overlap)
        if score < 1:
            continue
        digits_a = {t for t in tokens if t.isdigit()}
        digits_b = {t for t in other_tokens if t.isdigit()}
        if digits_a and digits_b and not (digits_a & digits_b):
            continue
        if digits_a and digits_b and (digits_a & digits_b):
            score += 2
        if score >= 2 or (digits_a & digits_b):
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
        },
        "candidates": [
            {
                "candidate_id": c.id,
                "address": c.address,
                "locality": c.locality,
            }
            for c in candidates
        ],
    }
    system = (
        "Ты сравниваешь адреса жителей России. "
        "Определи, относятся ли адреса к одному дому/квартире/домохозяйству. "
        "Игнорируй различия в сокращениях (ул/улица, д/дом). "
        "Ответь ТОЛЬКО JSON без пояснений:\n"
        "{\n"
        '  "matches": [\n'
        '    {"candidate_id": 1, "same_household_probability": 0.0, "reason": "..."}\n'
        "  ]\n"
        "}\n"
        "Включай только кандидатов с вероятностью >= 0.6."
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
    for item in data.get("matches") or []:
        try:
            cid = int(item.get("candidate_id"))
            prob = float(item.get("same_household_probability") or 0)
        except (TypeError, ValueError):
            continue
        if cid not in by_id or prob < 0.6:
            continue
        matches.append(
            OverlapMatch(
                candidate=by_id[cid],
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
        if ai_matches:
            return ai_matches
    # Fallback heuristic as soft matches
    return [
        OverlapMatch(
            candidate=c,
            probability=0.7,
            reason="эвристическое совпадение адреса",
        )
        for c in candidates[:5]
    ]


def stable_group_id(user_ids: list[int]) -> int:
    key = ",".join(str(i) for i in sorted(set(user_ids)))
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % (10**9)


def scan_all_addresses(*, use_ai: bool = True) -> list[dict]:
    """
    Scan all users with addresses; create AdminTask for overlap groups.
    Returns list of created/updated group summaries.
    """
    users = list(BotUser.objects.exclude(address="").order_by("id"))
    seen_pairs: set[tuple[int, int]] = set()
    groups: list[dict] = []
    for user in users:
        matches = find_address_matches(user, use_ai=use_ai)
        if not matches:
            continue
        member_ids = [user.id] + [m.candidate.id for m in matches]
        # Skip if all pairs already covered
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
        task = task_address_overlap(
            group_key=",".join(str(i) for i in member_ids),
            user_ids=member_ids,
            addresses=addresses,
            reason=reason or "возможное совпадение адресов",
            source_id=stable_group_id(member_ids),
        )
        groups.append(
            {
                "task_id": task.id,
                "user_ids": member_ids,
                "reason": reason,
            }
        )
    return groups
