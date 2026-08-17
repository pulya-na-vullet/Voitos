from __future__ import annotations

import re
from django.db.models import Count

from database.models import (
    ActivityKind,
    ActivityLog,
    BotUser,
    NeighborhoodWish,
    ServiceGroup,
    WishTopic,
)

TOPIC_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        WishTopic.ROAD,
        ("дорог", "ям", "яма", "асфальт", "тротуар", "ухаб", " downstream", "проезд", "щебён", "щебен"),
    ),
    (
        WishTopic.PLAYGROUND,
        ("д/п", "площадк", "качел", "песочниц", "детск", "горк", "турник"),
    ),
    (
        WishTopic.LIGHTING,
        ("свет", "фонар", "освещ", "ламп"),
    ),
    (
        WishTopic.SNOW,
        ("снег", "сугроб", "гололед", "гололёд", "расчист", "уборк"),
    ),
    (
        WishTopic.DOGS,
        ("собак", "намордник", "выгул", "поводок", "пёс", "пес", "лай"),
    ),
    (
        WishTopic.TRASH,
        ("мусор", "свалк", "контейнер", "урна", "отход"),
    ),
    (
        WishTopic.PARKING,
        ("парков", "машин на тротуар", "стоянк", "газон машин"),
    ),
    (
        WishTopic.SAFETY,
        ("безопасност", "камер", "огражд", "шлагбаум", "краж", "охран"),
    ),
    (
        WishTopic.GREEN,
        ("озелен", "дерев", "клумб", "газон", "цветник", "кустар"),
    ),
]

CIVIC_MARKERS = (
    "двор",
    "подъезд",
    "улиц",
    "придомов",
    "сосед",
    "нашей групп",
    "в группе",
    "хочу чтобы",
    "хотелось бы",
    "нужно сделать",
    "надо сделать",
    "пора бы",
    "предлагаю",
    "давайте",
    "голосу",
)

WISH_PREFIX_RE = re.compile(
    r"^\s*(пожелание|идея|предложение)\s*[:\-–—]\s*",
    re.IGNORECASE,
)
LIST_WISHES_RE = re.compile(
    r"^\s*(/)?(пожелания|мои\s+пожелания|темы\s+группы|голосование)\s*[.!]?\s*$",
    re.IGNORECASE,
)


def normalize_topic(value: str | None) -> str:
    if not value:
        return WishTopic.OTHER
    key = value.strip().lower()
    aliases = {
        "дороги": WishTopic.ROAD,
        "дорога": WishTopic.ROAD,
        "road": WishTopic.ROAD,
        "площадка": WishTopic.PLAYGROUND,
        "детская": WishTopic.PLAYGROUND,
        "playground": WishTopic.PLAYGROUND,
        "свет": WishTopic.LIGHTING,
        "освещение": WishTopic.LIGHTING,
        "lighting": WishTopic.LIGHTING,
        "снег": WishTopic.SNOW,
        "snow": WishTopic.SNOW,
        "собаки": WishTopic.DOGS,
        "собака": WishTopic.DOGS,
        "намордники": WishTopic.DOGS,
        "dogs": WishTopic.DOGS,
        "мусор": WishTopic.TRASH,
        "trash": WishTopic.TRASH,
        "парковка": WishTopic.PARKING,
        "parking": WishTopic.PARKING,
        "безопасность": WishTopic.SAFETY,
        "safety": WishTopic.SAFETY,
        "озеленение": WishTopic.GREEN,
        "green": WishTopic.GREEN,
        "прочее": WishTopic.OTHER,
        "other": WishTopic.OTHER,
    }
    if key in aliases:
        return aliases[key]
    if key in WishTopic.values:
        return key
    return detect_topic(key)


def detect_topic(text: str) -> str:
    lower = (text or "").lower()
    best = WishTopic.OTHER
    best_hits = 0
    for topic, words in TOPIC_KEYWORDS:
        hits = sum(1 for w in words if w in lower)
        if hits > best_hits:
            best = topic
            best_hits = hits
    return best


def looks_like_wish(text: str) -> bool:
    lower = (text or "").lower().strip()
    if not lower or len(lower) < 8:
        return False
    if LIST_WISHES_RE.match(text):
        return False
    if WISH_PREFIX_RE.match(text):
        return True
    if any(m in lower for m in CIVIC_MARKERS):
        return True
    return detect_topic(lower) != WishTopic.OTHER


def extract_wish_text(text: str) -> str:
    cleaned = WISH_PREFIX_RE.sub("", text or "").strip()
    return cleaned or (text or "").strip()


def user_groups(user: BotUser) -> list[ServiceGroup]:
    return list(user.service_groups.order_by("id"))


def capture_wish(
    user: BotUser,
    text: str,
    *,
    group: ServiceGroup | None = None,
    topic: str | None = None,
    confidence: float = 0.5,
    source_message: str = "",
) -> NeighborhoodWish:
    if group is None:
        raise ValueError("Не указана группа жителей")
    body = extract_wish_text(text)
    if not body:
        raise ValueError("Пустое пожелание")
    topic_key = normalize_topic(topic) if topic else detect_topic(body)
    from services.wish_ballot import ensure_open_period

    period = ensure_open_period(group)
    wish = NeighborhoodWish.objects.create(
        user=user,
        group=group,
        period=period,
        text=body,
        source_message=source_message or text,
        topic=topic_key,
        confidence=float(confidence or 0),
    )
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.SERVICE_WISH,
        title=f"Пожелание: {wish.get_topic_display()}",
        detail=wish.text[:500],
        meta={
            "wish_id": wish.id,
            "group_id": group.id,
            "topic": wish.topic,
            "period_id": period.id,
        },
    )
    return wish


def topic_stats(
    group: ServiceGroup | None = None,
    *,
    period=None,
    open_period_only: bool = False,
) -> list[dict]:
    qs = NeighborhoodWish.objects.all()
    if group is not None:
        qs = qs.filter(group=group)
    if period is not None:
        qs = qs.filter(period=period)
    elif open_period_only and group is not None:
        from database.models import WishPeriod, WishPeriodStatus

        open_p = (
            WishPeriod.objects.filter(group=group, status=WishPeriodStatus.OPEN)
            .order_by("-opened_at")
            .first()
        )
        if open_p is None:
            return []
        qs = qs.filter(period=open_p)
    rows = (
        qs.values("topic")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    label = dict(WishTopic.choices)
    return [
        {
            "topic": row["topic"],
            "label": label.get(row["topic"], row["topic"]),
            "count": row["count"],
        }
        for row in rows
    ]


def format_group_stats(group: ServiceGroup, *, limit_examples: int = 3) -> str:
    stats = topic_stats(group)
    total = sum(s["count"] for s in stats)
    if total < 1:
        return (
            f"По группе «{group.name}» пожеланий пока нет.\n"
            "Напишите, что хотите улучшить во дворе — например: "
            "«Хочу чтобы починили дорогу» или «Пожелание: детская площадка»."
        )
    lines = [f"Пожелания группы «{group.name}» — всего {total}:"]
    for s in stats:
        lines.append(f"• {s['label']}: {s['count']}")
        examples = list(
            NeighborhoodWish.objects.filter(group=group, topic=s["topic"])
            .order_by("-created_at")
            .values_list("text", flat=True)[:limit_examples]
        )
        for ex in examples:
            lines.append(f"  — {ex[:120]}")
    return "\n".join(lines)


def format_user_wishes_reply(user: BotUser) -> str:
    groups = user_groups(user)
    if not groups:
        return (
            "Вас пока нет в группе жителей — пожелания привязываются к группе. "
            "Напишите администратору, чтобы добавили в состав."
        )
    if len(groups) == 1:
        return format_group_stats(groups[0])
    parts = [format_group_stats(g) for g in groups]
    return "\n\n".join(parts)


def aggregate_home_stats(limit_groups: int = 12) -> list[dict]:
    """Per-group topic leaders for services home page."""
    out = []
    for group in ServiceGroup.objects.annotate(wish_count=Count("wishes")).order_by("-wish_count", "name")[
        :limit_groups
    ]:
        stats = topic_stats(group)
        out.append(
            {
                "group": group,
                "total": group.wish_count,
                "stats": stats,
                "top": stats[0] if stats else None,
            }
        )
    return out
