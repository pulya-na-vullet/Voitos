from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from dateutil import parser as date_parser
from django.utils import timezone

from ai.factory import AINotConfiguredError, get_llm_provider
from database.models import MemoryCategory

logger = logging.getLogger(__name__)


INTENT_SYSTEM_PROMPT = """Ты классификатор намерений персонального помощника Voitos.
Отвечай ТОЛЬКО валидным JSON без markdown.

Возможные intent:
- force_remember — пользователь явно просит запомнить (Запомни это / Запомни: ...)
- force_forget — пользователь явно просит НЕ запоминать (Не запоминай)
- save_memory — сообщение содержит важную личную информацию, которую стоит сохранить
- create_reminder — просьба напомнить о чём-то в будущем
- create_task — формулировка задачи / дела, которое нужно сделать
- complete_task — пользователь сообщает, что задачу выполнил
- delete_task — удалить задачу
- delete_reminder — удалить напоминание
- list_tasks — показать задачи / что нужно сделать
- list_reminders — показать напоминания
- search_memory — вопрос о сохранённой памяти (что помнишь про ...)
- list_memory — что ты помнишь обо мне / общий обзор памяти
- chat — обычный вопрос или разговор

Категории памяти: purchases, home, car, finance, health, preferences, people, ideas, other

Правила:
- Погода, новости, сиюминутные факты без личной ценности → should_save=false, intent=chat
- "Купила холодильник Bosch" → save_memory, category=purchases или home
- "Напомни 20 числа оплатить коммуналку" → create_reminder
- "Нужно купить подарок маме" → create_task
- "Выполнил подарок" / "Сделал ..." → complete_task
- Короткие спокойные ответы потом даст система, тебе нужна только классификация

JSON схема:
{
  "intent": "...",
  "should_save": true/false,
  "memory_text": "краткая формулировка факта или null",
  "category": "preferences|...",
  "task_text": "текст задачи или null",
  "reminder_text": "текст напоминания или null",
  "due_at": "ISO-8601 дата/время или относительное описание или null",
  "due_hint": "человекочитаемая дата или null",
  "query": "поисковый запрос или фрагмент для удаления/выполнения или null",
  "confidence": 0.0-1.0
}
"""


@dataclass
class IntentResult:
    intent: str = "chat"
    should_save: bool = False
    memory_text: str | None = None
    category: str = MemoryCategory.OTHER
    task_text: str | None = None
    reminder_text: str | None = None
    due_at: datetime | None = None
    due_hint: str | None = None
    query: str | None = None
    confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


FORCE_REMEMBER_RE = re.compile(
    r"^\s*(запомни\s+это\.?|запомни[:\s].*|сохрани\s+это\.?)\s*$",
    re.IGNORECASE,
)
FORCE_FORGET_RE = re.compile(
    r"^\s*(не\s+запоминай\.?|не\s+сохраняй\.?|забудь\s+это\.?)\s*$",
    re.IGNORECASE,
)
LIST_TASKS_RE = re.compile(
    r"(что\s+мне\s+нужно\s+сделать|покажи\s+мои\s+задачи|мои\s+задачи|список\s+задач)",
    re.IGNORECASE,
)
LIST_REMINDERS_RE = re.compile(
    r"(покажи\s+мои\s+напоминания|мои\s+напоминания|список\s+напоминаний)",
    re.IGNORECASE,
)
LIST_MEMORY_RE = re.compile(
    r"(что\s+ты\s+помнишь\s+обо\s+мне|что\s+ты\s+знаешь\s+обо\s+мне)",
    re.IGNORECASE,
)
SEARCH_MEMORY_RE = re.compile(
    r"(что\s+ты\s+помнишь\s+про|что\s+ты\s+знаешь\s+про|какие\s+идеи|что\s+помнишь\s+о)",
    re.IGNORECASE,
)
DELETE_TASK_RE = re.compile(r"удали(ть)?\s+задач", re.IGNORECASE)
DELETE_REMINDER_RE = re.compile(r"удали(ть)?\s+напомина", re.IGNORECASE)
COMPLETE_TASK_RE = re.compile(
    r"^(выполнил[аи]?|сделал[аи]?|готово[:\s]|отметь\s+выполнен)",
    re.IGNORECASE,
)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _parse_due(value: str | None, hint: str | None = None) -> datetime | None:
    """Parse reminder due time from Russian / ISO text. Never trust fuzzy junk dates."""
    raw = " ".join(p for p in [(value or "").strip(), (hint or "").strip()] if p)
    if not raw:
        return None
    now = timezone.localtime()
    lower = raw.lower().replace("ё", "е")

    # --- Relative: через N единиц ---
    if "через полгода" in lower or "через 6 месяцев" in lower:
        return now + timedelta(days=182)

    # через минуту / через час / через день (без числа = 1)
    m = re.search(
        r"через\s+(?:(\d+)\s*)?(минут[уыа]?|мин\.?|час(?:а|ов)?|день|дня|дней|недел[июяь]|месяц(?:а|ев)?)",
        lower,
    )
    if m:
        n = int(m.group(1) or "1")
        unit = m.group(2)
        if unit.startswith("мин"):
            return now + timedelta(minutes=n)
        if unit.startswith("час"):
            return now + timedelta(hours=n)
        if unit.startswith(("день", "дня", "дней")):
            return now + timedelta(days=n)
        if unit.startswith("недел"):
            return now + timedelta(weeks=n)
        if unit.startswith("месяц"):
            return now + timedelta(days=30 * n)

    # --- сегодня / завтра / послезавтра + optional time ---
    hour, minute = 10, 0
    am_pm = re.search(
        r"(?:в\s+)?(\d{1,2})(?:[:\.](\d{2}))?\s*(утра|вечера|дня)?",
        lower,
    )
    if am_pm:
        hour = int(am_pm.group(1))
        minute = int(am_pm.group(2) or 0)
        period = am_pm.group(3)
        if period == "вечера" and hour < 12:
            hour += 12
        if period == "утра" and hour == 12:
            hour = 0
        if period == "дня" and hour < 12:
            hour += 12

    base_day = None
    if "послезавтра" in lower:
        base_day = now + timedelta(days=2)
    elif "завтра" in lower:
        base_day = now + timedelta(days=1)
    elif "сегодня" in lower:
        base_day = now

    if base_day is not None:
        # If only relative minutes already handled above; here set clock time on day
        # When "сегодня" + "через N минут" already returned. If plain сегодня/завтра:
        if not re.search(r"через\s+\d*\s*мин", lower):
            candidate = base_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if "сегодня" in lower and candidate <= now:
                # If time already passed today, push to tomorrow unless user said "через"
                if am_pm:
                    candidate = candidate + timedelta(days=1)
                else:
                    candidate = now + timedelta(minutes=1)
            return candidate

    # --- Explicit DD.MM.YYYY or DD.MM ---
    m = re.search(
        r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?:\s+[вв]?\s*(\d{1,2})[:\.](\d{2}))?",
        lower,
    )
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.year
        if year < 100:
            year += 2000
        hh = int(m.group(4)) if m.group(4) else (hour if am_pm else 10)
        mm = int(m.group(5)) if m.group(5) else (minute if am_pm else 0)
        try:
            candidate = now.replace(
                year=year, month=month, day=day, hour=hh, minute=mm, second=0, microsecond=0
            )
        except ValueError:
            candidate = None
        if candidate is not None:
            if candidate.year < now.year - 1:
                # Refuse absurd past years from bad parsers
                candidate = candidate.replace(year=now.year)
            if candidate <= now and not m.group(3):
                # DD.MM without year already passed → next year
                try:
                    candidate = candidate.replace(year=now.year + 1)
                except ValueError:
                    pass
            return candidate

    # --- "20 числа" / "20-го" ---
    m = re.search(r"(\d{1,2})\s*(-?го|числа)", lower)
    if m:
        day = int(m.group(1))
        try:
            candidate = now.replace(day=day, hour=10, minute=0, second=0, microsecond=0)
        except ValueError:
            candidate = now.replace(day=min(day, 28), hour=10, minute=0, second=0, microsecond=0)
        if candidate <= now:
            month = candidate.month + 1
            year = candidate.year
            if month > 12:
                month = 1
                year += 1
            candidate = candidate.replace(year=year, month=month)
        return candidate

    # --- Strict ISO / numeric only (no fuzzy Russian free-text) ---
    iso_like = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ].*", iso_like) or re.fullmatch(
        r"\d{1,2}[./]\d{1,2}[./]\d{2,4}.*", iso_like
    ):
        try:
            dt = date_parser.parse(iso_like, dayfirst=True, fuzzy=False)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            return dt
        except (ValueError, OverflowError, TypeError):
            pass

    logger.warning("Could not parse due date reliably: %s", raw[:120])
    return None


def resolve_reminder_due(user_text: str, llm_due: datetime | None = None, hint: str | None = None) -> datetime:
    """Prefer parsing the user's words; discard LLM dates that are in the past."""
    now = timezone.localtime()
    parsed = _parse_due(user_text, hint)
    if parsed and parsed > now - timedelta(seconds=30):
        return parsed

    if llm_due is not None:
        due = llm_due
        if timezone.is_naive(due):
            due = timezone.make_aware(due, timezone.get_current_timezone())
        # Ignore clearly wrong LLM dates (past year or far past)
        if due.year >= now.year and due > now - timedelta(minutes=1):
            return due

    # Fallback: 1 minute from now for "через …"/urgent phrasing, else tomorrow 10:00
    lower = user_text.lower()
    if "через" in lower or "минут" in lower:
        return now + timedelta(minutes=1)
    due = now + timedelta(days=1)
    return due.replace(hour=10, minute=0, second=0, microsecond=0)


class IntentAnalyzer:
    """Hybrid rule + LLM intent detection for predictable MVP behavior."""

    def analyze(self, text: str) -> IntentResult:
        cleaned = text.strip()
        rule = self._rule_based(cleaned)
        if rule:
            return rule
        try:
            return self._llm_based(cleaned)
        except AINotConfiguredError:
            logger.warning("Yandex AI not configured — using heuristic intent fallback")
            return self._heuristic_fallback(cleaned)
        except Exception:
            logger.exception("LLM intent analysis failed, falling back to heuristics")
            return self._heuristic_fallback(cleaned)

    def _rule_based(self, text: str) -> IntentResult | None:
        if FORCE_REMEMBER_RE.match(text) or text.lower().startswith("запомни это"):
            return IntentResult(intent="force_remember", confidence=1.0)
        if FORCE_FORGET_RE.match(text):
            return IntentResult(intent="force_forget", confidence=1.0)
        if LIST_TASKS_RE.search(text):
            return IntentResult(intent="list_tasks", confidence=1.0)
        if LIST_REMINDERS_RE.search(text):
            return IntentResult(intent="list_reminders", confidence=1.0)
        if LIST_MEMORY_RE.search(text):
            return IntentResult(intent="list_memory", confidence=1.0)
        if SEARCH_MEMORY_RE.search(text):
            return IntentResult(intent="search_memory", query=text, confidence=0.95)
        if DELETE_TASK_RE.search(text):
            return IntentResult(intent="delete_task", query=text, confidence=0.95)
        if DELETE_REMINDER_RE.search(text):
            return IntentResult(intent="delete_reminder", query=text, confidence=0.95)
        if COMPLETE_TASK_RE.search(text):
            return IntentResult(intent="complete_task", query=text, confidence=0.9)
        if text.lower().startswith("запомни"):
            body = re.sub(r"^запомни[:\s]+", "", text, flags=re.IGNORECASE).strip()
            return IntentResult(
                intent="save_memory",
                should_save=True,
                memory_text=body or text,
                category=MemoryCategory.OTHER,
                confidence=0.95,
            )
        return None

    def _llm_based(self, text: str) -> IntentResult:
        llm = get_llm_provider()
        now = timezone.localtime()
        system = (
            INTENT_SYSTEM_PROMPT
            + f"\n\nСейчас: {now.strftime('%Y-%m-%d %H:%M')} (Europe/Moscow). "
            "Для due_at всегда используй реальный будущий момент в ISO-8601 от этой даты. "
            "«через 1 минуту» = сейчас+1 минута. Не выдумывай прошлые годы."
        )
        raw_text = llm.complete_text(system, text, temperature=0.1, max_tokens=600)
        data = _extract_json(raw_text)
        intent = (data.get("intent") or "chat").strip()
        # Always prefer deterministic parse from user text for reminders
        due = _parse_due(text, data.get("due_hint") or data.get("due_at"))
        if due is None:
            due = _parse_due(data.get("due_at"), data.get("due_hint"))
        return IntentResult(
            intent=intent,
            should_save=bool(data.get("should_save")),
            memory_text=data.get("memory_text") or None,
            category=data.get("category") or MemoryCategory.OTHER,
            task_text=data.get("task_text") or None,
            reminder_text=data.get("reminder_text") or None,
            due_at=due,
            due_hint=data.get("due_hint"),
            query=data.get("query"),
            confidence=float(data.get("confidence") or 0.5),
            raw=data,
        )

    def _heuristic_fallback(self, text: str) -> IntentResult:
        lower = text.lower()
        if "напомн" in lower:
            due = _parse_due(text, None)
            return IntentResult(
                intent="create_reminder",
                reminder_text=text,
                due_at=due or (timezone.localtime() + timedelta(days=1)),
                confidence=0.4,
            )
        task_markers = ("нужно", "надо", "купить", "позвонить", "сделать", "записаться")
        if any(m in lower for m in task_markers) and "?" not in text:
            # Prefer reminder if "напомни" already handled; tasks for todo phrasing
            if lower.startswith(("нужно", "надо", "купить", "позвонить")):
                return IntentResult(intent="create_task", task_text=text, confidence=0.45)

        # Personal facts worth remembering without LLM
        skip_ephemeral = any(
            x in lower
            for x in ("сегодня идёт", "сегодня идет", "сейчас дождь", "какая погода", "новост")
        )
        personal_markers = (
            "купил",
            "купила",
            "у меня",
            "моя",
            "мой",
            "люблю",
            "аллерги",
            "работаю",
            "живу",
            "зовут",
            "день рождения",
            "машина",
            "авто",
        )
        if not skip_ephemeral and "?" not in text and any(m in lower for m in personal_markers):
            category = MemoryCategory.OTHER
            if any(x in lower for x in ("купил", "купила", "покуп")):
                category = MemoryCategory.PURCHASES
            elif "аллерги" in lower or "здоров" in lower:
                category = MemoryCategory.HEALTH
            elif any(x in lower for x in ("люблю", "предпочит", "ненавиж")):
                category = MemoryCategory.PREFERENCES
            elif any(x in lower for x in ("машина", "авто", "машин")):
                category = MemoryCategory.CAR
            elif any(x in lower for x in ("дом", "квартир")):
                category = MemoryCategory.HOME
            return IntentResult(
                intent="save_memory",
                should_save=True,
                memory_text=text,
                category=category,
                confidence=0.55,
            )

        return IntentResult(intent="chat", confidence=0.3)
