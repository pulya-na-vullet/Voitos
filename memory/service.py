from __future__ import annotations

from database.models import ActivityKind, ActivityLog, BotUser, MemoryCategory, MemoryItem


CATEGORY_ALIASES: dict[str, str] = {
    "покупки": MemoryCategory.PURCHASES,
    "purchases": MemoryCategory.PURCHASES,
    "дом": MemoryCategory.HOME,
    "home": MemoryCategory.HOME,
    "автомобиль": MemoryCategory.CAR,
    "машина": MemoryCategory.CAR,
    "авто": MemoryCategory.CAR,
    "car": MemoryCategory.CAR,
    "финансы": MemoryCategory.FINANCE,
    "finance": MemoryCategory.FINANCE,
    "здоровье": MemoryCategory.HEALTH,
    "health": MemoryCategory.HEALTH,
    "предпочтения": MemoryCategory.PREFERENCES,
    "preferences": MemoryCategory.PREFERENCES,
    "люди": MemoryCategory.PEOPLE,
    "people": MemoryCategory.PEOPLE,
    "идеи": MemoryCategory.IDEAS,
    "ideas": MemoryCategory.IDEAS,
    "прочее": MemoryCategory.OTHER,
    "other": MemoryCategory.OTHER,
}


def normalize_category(value: str | None) -> str:
    if not value:
        return MemoryCategory.OTHER
    key = value.strip().lower()
    return CATEGORY_ALIASES.get(key, key if key in MemoryCategory.values else MemoryCategory.OTHER)


class MemoryService:
    def save(
        self,
        user: BotUser,
        text: str,
        category: str = MemoryCategory.OTHER,
        source_message: str = "",
    ) -> MemoryItem:
        item = MemoryItem.objects.create(
            user=user,
            text=text.strip(),
            category=normalize_category(category),
            source_message=source_message or text,
        )
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.MEMORY_SAVE,
            title="Сохранена память",
            detail=item.text,
            meta={"category": item.category, "id": item.id},
        )
        return item

    def search(self, user: BotUser, query: str, limit: int = 20) -> list[MemoryItem]:
        qs = MemoryItem.objects.filter(user=user)
        tokens = [t for t in query.lower().replace("?", "").split() if len(t) > 2]
        stop = {
            "что",
            "ты",
            "помнишь",
            "про",
            "знаешь",
            "какие",
            "моя",
            "мой",
            "мои",
            "обо",
            "мне",
            "сохран",
            "идеи",
            "идея",
        }
        keywords = [t for t in tokens if t not in stop]
        if not keywords:
            return list(qs.order_by("-created_at")[:limit])

        from django.db.models import Q

        q = Q()
        for kw in keywords:
            q |= Q(text__icontains=kw) | Q(category__icontains=kw) | Q(source_message__icontains=kw)
        # Map Russian category words
        for kw in keywords:
            cat = CATEGORY_ALIASES.get(kw)
            if cat:
                q |= Q(category=cat)
        return list(qs.filter(q).order_by("-created_at")[:limit])

    def list_all(self, user: BotUser, limit: int = 50) -> list[MemoryItem]:
        return list(MemoryItem.objects.filter(user=user).order_by("-created_at")[:limit])

    def list_by_category(self, user: BotUser, category: str) -> list[MemoryItem]:
        return list(
            MemoryItem.objects.filter(user=user, category=normalize_category(category)).order_by(
                "-created_at"
            )
        )

    def delete(self, item_id: int) -> bool:
        item = MemoryItem.objects.filter(pk=item_id).first()
        if not item:
            return False
        ActivityLog.objects.create(
            user=item.user,
            kind=ActivityKind.MEMORY_DELETE,
            title="Удалена память",
            detail=item.text,
            meta={"id": item.id},
        )
        item.delete()
        return True

    def format_list(self, items: list[MemoryItem]) -> str:
        if not items:
            return "Пока ничего не помню."
        lines = []
        for item in items:
            lines.append(f"• [{item.get_category_display()}] {item.text}")
        return "\n".join(lines)
