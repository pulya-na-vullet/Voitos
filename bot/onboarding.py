"""Комикс-онбординг Voitos: 5 коротких историй + месяц подписки за 5/5."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from database.models import ActivityKind, ActivityLog, BotUser, PendingAction

ONBOARDING_KIND = "comic_onboarding"

# Каталог картинок для отправки в MAX (сжатые JPEG).
_STATIC_DIR = Path(settings.BASE_DIR) / "static" / "bot" / "onboarding"
# Оригиналы PNG (для панели / архива).
_ASSETS_DIR = Path(settings.BASE_DIR) / "assets" / "onboarding"


@dataclass(frozen=True)
class ComicStory:
    code: str
    title: str
    caption: str
    image_name: str  # без пути, .jpg в static


STORIES: tuple[ComicStory, ...] = (
    ComicStory(
        code="snow",
        title="Уборка снега",
        caption="Снег завалил двор — Voitos собирает соседей и технику без переплат.",
        image_name="01_snow.jpg",
    ),
    ComicStory(
        code="playground",
        title="Ремонт детской площадки",
        caption="Сломалась площадка — через Voitos двор скидывается и зовёт мастера.",
        image_name="02_playground.jpg",
    ),
    ComicStory(
        code="electrician",
        title="Вызов электрика",
        caption="Искры из розетки — вызвали электрика в Voitos: приехал и починил.",
        image_name="03_electrician.jpg",
    ),
    ComicStory(
        code="manicure",
        title="Маникюр",
        caption="Нужен мастер на дом — в Voitos находите маникюр рядом и записываетесь.",
        image_name="04_manicure.jpg",
    ),
    ComicStory(
        code="computer",
        title="Ремонт компьютера",
        caption="Синий экран — Voitos находит проверенного мастера по компьютерам.",
        image_name="05_computer.jpg",
    ),
)

STORY_BY_CODE = {s.code: s for s in STORIES}
STORY_CODES = [s.code for s in STORIES]

_YES = {"да", "yes", "y", "+", "ага", "угу", "хочу", "давай", "поехали", "начнём", "начнем"}
_NO = {"нет", "no", "n", "-", "не", "позже", "не сейчас", "отмена", "стоп"}
_NEXT = {
    "далее",
    "дальше",
    "следующая",
    "следующий",
    "next",
    "ок",
    "ok",
    "готово",
    "понял",
    "поняла",
    "продолжить",
    "ещё",
    "еще",
}


def intro_blurb() -> str:
    """Коротко при первом знакомстве / после анкеты."""
    return (
        "Есть короткое обучение — 5 комиксов, как работает Voitos.\n"
        "Пройдёте все пять — подарим месяц подписки.\n"
        "Напишите «обучение» или «да», когда будете готовы."
    )


def offer_message() -> str:
    return (
        "Расскажу о Voitos в 5 коротких комиксах.\n"
        "За все пять — месяц подписки в подарок.\n\n"
        "Начать сейчас? Напишите «да».\n"
        "Позже — «обучение»."
    )


def progress_list(user: BotUser) -> list[str]:
    raw = user.onboarding_steps or []
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if str(x) in STORY_BY_CODE]


def progress_count(user: BotUser) -> int:
    return len(progress_list(user))


def is_complete(user: BotUser) -> bool:
    if user.onboarding_completed_at or user.onboarding_reward_granted:
        return True
    return progress_count(user) >= len(STORIES)


def panel_progress(user: BotUser) -> dict:
    done = set(progress_list(user))
    # Если награда уже выдана — считаем все шаги пройденными (на случай рассинхрона JSON).
    if user.onboarding_reward_granted or user.onboarding_completed_at:
        done = set(STORY_CODES)
    steps = [
        {
            "code": s.code,
            "title": s.title,
            "done": s.code in done,
            "image": f"bot/onboarding/{s.image_name}",
        }
        for s in STORIES
    ]
    done_count = sum(1 for s in steps if s["done"])
    return {
        "steps": steps,
        "done_count": done_count,
        "total": len(steps),
        "completed": is_complete(user) or done_count >= len(steps),
        "reward_granted": bool(user.onboarding_reward_granted),
        "completed_at": user.onboarding_completed_at,
    }


def story_image_path(story: ComicStory) -> Path | None:
    jpg = _STATIC_DIR / story.image_name
    if jpg.is_file():
        return jpg
    png = _ASSETS_DIR / story.image_name.replace(".jpg", ".png")
    if png.is_file():
        return png
    return None


def _set_attach(pending: PendingAction, story: ComicStory | None) -> None:
    payload = dict(pending.pending_payload or {})
    if story is None:
        payload.pop("attach_image", None)
    else:
        path = story_image_path(story)
        if path:
            payload["attach_image"] = str(path)
        else:
            payload.pop("attach_image", None)
    pending.pending_payload = payload


def _mark_step(user: BotUser, code: str) -> None:
    done = progress_list(user)
    if code not in done:
        done.append(code)
        user.onboarding_steps = done
        user.save(update_fields=["onboarding_steps", "last_seen_at"])


def _grant_reward_if_needed(user: BotUser) -> str:
    """Пометить завершение и выдать месяц один раз."""
    now = timezone.now()
    # Актуальные сроки из БД — иначе можно затереть подписку устаревшим объектом в памяти.
    user.refresh_from_db(
        fields=[
            "subscription_until",
            "grace_until",
            "onboarding_steps",
            "onboarding_completed_at",
            "onboarding_reward_granted",
        ]
    )
    user.onboarding_steps = list(STORY_CODES)
    if not user.onboarding_completed_at:
        user.onboarding_completed_at = now

    reward_line = ""
    if not user.onboarding_reward_granted:
        user.onboarding_reward_granted = True
        user.save(
            update_fields=[
                "onboarding_steps",
                "onboarding_completed_at",
                "onboarding_reward_granted",
                "last_seen_at",
            ]
        )
        user.extend_subscription(months=1)
        until = user.subscription_until
        until_s = timezone.localtime(until).strftime("%d.%m.%Y") if until else "—"
        reward_line = f"\n\nПодарок: +1 месяц подписки (до {until_s})."
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.OTHER,
            title="Онбординг: месяц подписки",
            detail=f"Выдан месяц за 5/5 комиксов, подписка до {until_s}",
            meta={"onboarding_reward": True},
        )
    else:
        user.save(
            update_fields=[
                "onboarding_steps",
                "onboarding_completed_at",
                "last_seen_at",
            ]
        )

    return (
        "Готово! Вы прошли все 5 историй про Voitos."
        f"{reward_line}\n\n"
        "Дальше можно писать «помощь», «вызвать мастера» или просто задачу."
    )


def start_onboarding(user: BotUser, pending: PendingAction, *, force: bool = False) -> str:
    if is_complete(user) and not force:
        pending.clear_pending()
        until = user.subscription_until
        until_s = timezone.localtime(until).strftime("%d.%m.%Y") if until else "—"
        extra = (
            f" Месяц уже начислен (подписка до {until_s})."
            if user.onboarding_reward_granted
            else ""
        )
        return f"Обучение уже пройдено.{extra}"

    done = progress_list(user)
    next_idx = len(done)
    if next_idx >= len(STORIES):
        pending.clear_pending()
        return _grant_reward_if_needed(user)

    pending.pending_kind = ONBOARDING_KIND
    pending.pending_payload = {"step": "offer" if next_idx == 0 and not force else "story", "index": next_idx}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    if next_idx == 0 and not force:
        _set_attach(pending, None)
        pending.save(update_fields=["pending_payload", "updated_at"])
        return offer_message()
    return _show_story(user, pending, next_idx)


def _show_story(user: BotUser, pending: PendingAction, index: int) -> str:
    if index < 0 or index >= len(STORIES):
        pending.clear_pending()
        return _grant_reward_if_needed(user)

    story = STORIES[index]
    pending.pending_kind = ONBOARDING_KIND
    pending.pending_payload = {"step": "story", "index": index, "code": story.code}
    _set_attach(pending, story)
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])

    n = index + 1
    total = len(STORIES)
    tail = (
        f"История {n} из {total}. Напишите «далее»."
        if n < total
        else f"История {n} из {total} — последняя. Напишите «далее», чтобы получить подарок."
    )
    return f"{story.title}\n{story.caption}\n\n{tail}"


def handle_onboarding_step(user: BotUser, text: str, pending: PendingAction) -> str:
    payload = dict(pending.pending_payload or {})
    step = payload.get("step") or "offer"
    raw = (text or "").strip()
    low = raw.lower().replace("ё", "е").strip().rstrip(".!")

    if low in _NO or low in {"отмена", "стоп"}:
        pending.clear_pending()
        return (
            "Хорошо, обучение можно пройти позже — напишите «обучение».\n"
            "За 5 комиксов дарим месяц подписки."
        )

    if step == "offer":
        if low in _YES or low in {"обучение", "комиксы", "истории"}:
            return _show_story(user, pending, progress_count(user))
        return (
            "Напишите «да», чтобы начать комиксы, или «позже», чтобы отложить."
        )

    if step == "story":
        index = int(payload.get("index") or 0)
        code = payload.get("code") or (STORIES[index].code if 0 <= index < len(STORIES) else "")
        if low not in _NEXT and low not in _YES:
            # Повторно показать подсказку, картинку снова прикрепим
            story = STORIES[index] if 0 <= index < len(STORIES) else None
            if story:
                _set_attach(pending, story)
                pending.save(update_fields=["pending_payload", "updated_at"])
            return "Чтобы продолжить, напишите «далее». Отложить — «позже»."

        if code:
            _mark_step(user, code)
            ActivityLog.objects.create(
                user=user,
                kind=ActivityKind.OTHER,
                title=f"Онбординг: {story_title(code)}",
                detail=f"Просмотрена история {code}",
                meta={"onboarding_step": code},
            )

        next_index = index + 1
        if next_index >= len(STORIES):
            pending.clear_pending()
            return _grant_reward_if_needed(user)
        return _show_story(user, pending, next_index)

    return start_onboarding(user, pending)


def story_title(code: str) -> str:
    s = STORY_BY_CODE.get(code)
    return s.title if s else code


def pop_attach_image(pending: PendingAction) -> str | None:
    """Достать путь картинки для отправки и убрать из payload (одноразово)."""
    payload = dict(pending.pending_payload or {})
    path = payload.pop("attach_image", None)
    if path is not None:
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
    return path if isinstance(path, str) and path.strip() else None


def registration_finish_addon() -> str:
    return (
        "\n\n────────────\n"
        + intro_blurb()
    )
