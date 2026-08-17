"""Месячный опрос качества работы менеджера группы (1–5 + комментарий)."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Callable

from django.db.models import Avg, Count
from django.utils import timezone

from database.models import (
    BotUser,
    ManagerSurveyAILog,
    ManagerSurveyPeriod,
    ManagerSurveyPeriodStatus,
    ManagerSurveyResponse,
    PendingAction,
    ServiceGroup,
)
from services.work_request_rating import parse_score

logger = logging.getLogger(__name__)

PENDING_KIND = "manager_survey"
SURVEY_INTERVAL = timedelta(days=30)
COLLECTING_DURATION = timedelta(days=7)
# Комментарии с оценкой ≤ этого порога собираем для ОС / ИИ.
FEEDBACK_SCORE_MAX = 4


def survey_ask_message(period: ManagerSurveyPeriod) -> str:
    mgr_name = period.manager.get_full_name() or period.manager.username
    return (
        f"Опрос качества работы менеджера группы «{period.group.name}».\n"
        f"Менеджер: {mgr_name}.\n\n"
        "Оцените работу менеджера от 1 до 5, где 5 — отлично.\n"
        "Напишите число от 1 до 5."
    )


def _can_start_survey(group: ServiceGroup, now) -> bool:
    if not group.manager_id:
        return False
    if ManagerSurveyPeriod.objects.filter(
        group=group, status=ManagerSurveyPeriodStatus.COLLECTING
    ).exists():
        return False
    last = (
        ManagerSurveyPeriod.objects.filter(group=group)
        .order_by("-started_at")
        .first()
    )
    # Первая рассылка — сразу при старте фичи (периода ещё не было).
    if last is None:
        return True
    return (now - last.started_at) >= SURVEY_INTERVAL


def start_manager_survey(
    group: ServiceGroup,
    *,
    send_fn: Callable | None = None,
    now=None,
    force: bool = False,
) -> ManagerSurveyPeriod | None:
    now = now or timezone.now()
    if not group.manager_id:
        return None
    if not force and not _can_start_survey(group, now):
        return None

    period = ManagerSurveyPeriod.objects.create(
        group=group,
        manager_id=group.manager_id,
        status=ManagerSurveyPeriodStatus.COLLECTING,
        ends_at=now + COLLECTING_DURATION,
    )
    text = survey_ask_message(period)
    manager_bot_id = None
    profile = getattr(group.manager, "panel_profile", None)
    if profile and profile.bot_user_id:
        manager_bot_id = profile.bot_user_id

    for user in group.members.all():
        if manager_bot_id and int(user.id) == int(manager_bot_id):
            continue  # менеджер сам себя не оценивает
        pending, _ = PendingAction.objects.get_or_create(user=user)
        if not pending.pending_kind:
            pending.pending_kind = PENDING_KIND
            pending.pending_payload = {
                "period_id": period.id,
                "step": "score",
            }
            pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        elif pending.pending_kind == PENDING_KIND:
            pending.pending_payload = {"period_id": period.id, "step": "score"}
            pending.save(update_fields=["pending_payload", "updated_at"])
        if send_fn:
            try:
                send_fn(user, text)
            except Exception:
                logger.exception(
                    "Failed to send manager survey #%s to %s",
                    period.id,
                    user.max_user_id,
                )
    return period


def handle_manager_survey_step(
    user: BotUser,
    text: str,
    pending: PendingAction,
) -> str | None:
    if pending.pending_kind != PENDING_KIND:
        return None
    payload = dict(pending.pending_payload or {})
    period = (
        ManagerSurveyPeriod.objects.select_related("group", "manager")
        .filter(pk=payload.get("period_id"))
        .first()
    )
    if period is None or period.status != ManagerSurveyPeriodStatus.COLLECTING:
        pending.clear_pending()
        return "Этот опрос уже завершён. Спасибо!"
    if timezone.now() > period.ends_at:
        pending.clear_pending()
        return "Срок опроса истёк. Спасибо!"

    step = payload.get("step") or "score"
    raw = (text or "").strip()

    if step == "score":
        score = parse_score(raw)
        if score is None:
            return "Нужна оценка числом от 1 до 5 (5 — максимум)."
        payload["score"] = score
        payload["step"] = "comment"
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        if score <= FEEDBACK_SCORE_MAX:
            return (
                f"Оценка {score}/5 принята.\n"
                "Напишите, что можно улучшить в работе менеджера "
                "(короткий комментарий)."
            )
        return (
            f"Оценка {score}/5 принята.\n"
            "Можете добавить комментарий или написать «пропустить»."
        )

    if step == "comment":
        score = int(payload.get("score") or 0)
        if score < 1 or score > 5:
            payload["step"] = "score"
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            return "Сначала напишите оценку от 1 до 5."
        lower = raw.lower()
        skip_tokens = {"пропустить", "skip", "-", "нет", "без комментария", "не надо"}
        if lower in skip_tokens:
            if score <= FEEDBACK_SCORE_MAX and not payload.get("skip_ok"):
                payload["skip_ok"] = True
                pending.pending_payload = payload
                pending.save(update_fields=["pending_payload", "updated_at"])
                return (
                    "Для оценки 1–4 желателен короткий комментарий — что улучшить?\n"
                    "Или напишите «пропустить» ещё раз, чтобы без текста."
                )
            comment = ""
        else:
            comment = raw[:2000]

        ManagerSurveyResponse.objects.update_or_create(
            period=period,
            user=user,
            defaults={"score": score, "comment": comment},
        )
        pending.clear_pending()
        return (
            f"Спасибо! Сохранили оценку {score}/5"
            + (" и комментарий." if comment else ".")
        )

    pending.clear_pending()
    return "Опрос завершён."


def feedback_responses_qs(period: ManagerSurveyPeriod):
    """Ответы с оценкой 1–4 (и любые с комментарием) для панели / ИИ."""
    return period.responses.filter(score__lte=FEEDBACK_SCORE_MAX).select_related("user")


def period_stats(period: ManagerSurveyPeriod) -> dict:
    agg = period.responses.aggregate(avg=Avg("score"), cnt=Count("id"))
    avg = agg["avg"]
    return {
        "avg": round(float(avg), 2) if avg is not None else None,
        "count": int(agg["cnt"] or 0),
        "low_count": period.responses.filter(score__lte=FEEDBACK_SCORE_MAX).count(),
    }


def summarize_period_with_ai(period: ManagerSurveyPeriod) -> str:
    """Саммари ОС за период; пишет лог сообщений ИИ по менеджеру."""
    rows = list(
        period.responses.filter(score__lte=FEEDBACK_SCORE_MAX)
        .exclude(comment="")
        .select_related("user")
        .order_by("score", "-created_at")
    )
    if not rows:
        # Если нет комментариев 1–4 — кратко по всем оценкам
        all_scores = list(period.responses.values_list("score", flat=True))
        if not all_scores:
            summary = "За период ответов нет."
        else:
            avg = sum(all_scores) / len(all_scores)
            summary = (
                f"Комментариев с оценкой 1–4 нет. "
                f"Средняя оценка: {avg:.1f} из {len(all_scores)} ответов."
            )
        period.ai_summary = summary
        period.ai_summarized_at = timezone.now()
        period.save(update_fields=["ai_summary", "ai_summarized_at"])
        ManagerSurveyAILog.objects.create(
            manager_id=period.manager_id,
            period=period,
            role="assistant",
            content=summary,
        )
        return summary

    lines = []
    for r in rows:
        name = str(r.user)
        lines.append(f"- {r.score}/5 · {name}: {r.comment[:500]}")
    blob = "\n".join(lines)
    system = (
        "Ты анализируешь обратную связь жителей о работе менеджера соседской группы.\n"
        "По комментариям с оценками 1–4 дай краткое саммари на русском (5–8 предложений):\n"
        "сильные стороны (если есть), проблемы, конкретные рекомендации менеджеру.\n"
        "Без вступления и без markdown — только текст саммари."
    )
    user_prompt = (
        f"Группа: {period.group.name}\n"
        f"Менеджер: {period.manager.username}\n"
        f"Период с {timezone.localtime(period.started_at):%d.%m.%Y}\n\n"
        f"Отзывы:\n{blob}"
    )

    ManagerSurveyAILog.objects.create(
        manager_id=period.manager_id,
        period=period,
        role="system",
        content=system,
    )
    ManagerSurveyAILog.objects.create(
        manager_id=period.manager_id,
        period=period,
        role="user",
        content=user_prompt,
    )

    try:
        from ai.factory import get_llm_provider

        llm = get_llm_provider()
        summary = llm.complete_text(system, user_prompt, temperature=0.2, max_tokens=800)
        summary = (summary or "").strip() or "ИИ вернул пустой ответ."
        ManagerSurveyAILog.objects.create(
            manager_id=period.manager_id,
            period=period,
            role="assistant",
            content=summary,
        )
    except Exception as exc:
        logger.exception("Manager survey AI summary failed for period %s", period.id)
        summary = (
            f"Не удалось получить саммари ИИ ({exc}). "
            f"Сырые отзывы ({len(rows)}):\n" + blob[:1500]
        )
        ManagerSurveyAILog.objects.create(
            manager_id=period.manager_id,
            period=period,
            role="error",
            content=str(exc)[:2000],
        )

    period.ai_summary = summary
    period.ai_summarized_at = timezone.now()
    period.save(update_fields=["ai_summary", "ai_summarized_at"])
    return summary


def close_period(period: ManagerSurveyPeriod, *, run_ai: bool = True) -> ManagerSurveyPeriod:
    if period.status == ManagerSurveyPeriodStatus.CLOSED:
        if run_ai and not period.ai_summary:
            summarize_period_with_ai(period)
        return period
    period.status = ManagerSurveyPeriodStatus.CLOSED
    period.closed_at = timezone.now()
    period.save(update_fields=["status", "closed_at"])
    if run_ai:
        summarize_period_with_ai(period)
    return period


def process_manager_surveys(*, send_fn: Callable | None = None, now=None) -> dict[str, int]:
    """Тик: закрыть истекшие опросы + стартовать новые (сразу первый, далее раз в месяц)."""
    now = now or timezone.now()
    stats = {"closed": 0, "started": 0, "summarized": 0}
    due = ManagerSurveyPeriod.objects.filter(
        status=ManagerSurveyPeriodStatus.COLLECTING,
        ends_at__lte=now,
    ).select_related("group", "manager")
    for period in due:
        close_period(period, run_ai=True)
        stats["closed"] += 1
        stats["summarized"] += 1

    groups = (
        ServiceGroup.objects.filter(manager_id__isnull=False)
        .annotate(n=Count("members"))
        .filter(n__gt=0)
        .select_related("manager", "manager__panel_profile")
    )
    for group in groups:
        period = start_manager_survey(group, send_fn=send_fn, now=now)
        if period is not None:
            stats["started"] += 1
    return stats
