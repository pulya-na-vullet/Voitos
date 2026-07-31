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
- create_reminder — просьба напомнить о чём-то в будущем (в т.ч. ежедневно)
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
- "Сделай каждодневное напоминание..." / "каждый день" / "каждое утро" → create_reminder, repeat=daily
- "Нужно купить подарок маме" → create_task
- "Выполнил подарок" / "Сделал ..." → complete_task
- Если время неясно — due_at=null, needs_time_clarify=true (НЕ ставь ночь 01:00 и не выдумывай)
- «Утро» / завтрак без часа → due_hint="утро", НЕ 01:00
- Короткие спокойные ответы потом даст система, тебе нужна только классификация

JSON схема:
{
  "intent": "...",
  "should_save": true/false,
  "memory_text": "краткая формулировка факта или null",
  "category": "preferences|...",
  "task_text": "текст задачи или null",
  "reminder_text": "текст напоминания или null",
  "repeat": "none|daily",
  "due_at": "ISO-8601 дата/время или относительное описание или null",
  "due_hint": "человекочитаемая дата или null",
  "needs_time_clarify": false,
  "query": "поисковый запрос или фрагмент для удаления/выполнения или null",
  "confidence": 0.0-1.0
}
"""


DEFAULT_MORNING_HOUR = 9
DEFAULT_DAY_HOUR = 14
DEFAULT_EVENING_HOUR = 19


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
    repeat: str = "none"
    needs_time_clarify: bool = False
    query: str | None = None
    confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReminderSchedule:
    due_at: datetime | None
    repeat: str = "none"
    needs_clarification: bool = False
    clarify_question: str = ""
    time_known: bool = False


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


def detect_reminder_repeat(text: str) -> str:
    lower = (text or "").lower().replace("ё", "е")
    if re.search(
        r"кажд\w*|ежедневн\w*|каждый\s+день|каждое\s+утро|по\s+утрам|каждый\s+день",
        lower,
    ):
        return "daily"
    return "none"


def extract_reminder_body(user_text: str, llm_text: str | None = None) -> str:
    text = (user_text or "").strip()
    m = re.search(
        r"(?:с\s+)?текст(?:ом)?\s*:\s*[«\"'](.+?)[»\"']",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    m = re.search(r"[«\"](.+?)[»\"]", text, flags=re.DOTALL)
    if m and len(m.group(1).strip()) >= 3:
        return m.group(1).strip()
    body = (llm_text or text).strip()
    body = re.sub(
        r"^(сделай\s+мне\s+|сделай\s+|создай\s+)?(кажд\w+\s+|ежедневн\w+\s+)?"
        r"напоминани[ея]\s*",
        "",
        body,
        flags=re.IGNORECASE,
    ).strip()
    body = re.sub(r"^напомни\s+", "", body, flags=re.IGNORECASE).strip()
    body = re.sub(r"^(что\s+)?с\s+текстом\s*:\s*", "", body, flags=re.IGNORECASE).strip()
    return body or text


def _extract_clock(lower: str) -> tuple[int, int] | None:
    """Extract explicit clock time; ignore bare digits without time context."""
    m = re.search(
        r"(?:^|[^\d])(?:в\s+)?(\d{1,2})[:\.](\d{2})\s*(утра|вечера|дня)?",
        lower,
    )
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        period = m.group(3)
    else:
        m = re.search(
            r"(?:^|[^\d])в\s+(\d{1,2})(?:\s*(утра|вечера|дня))?(?!\s*(?:числа|-го|[./]\d))",
            lower,
        )
        if not m:
            # bare "9 утра" / "9 вечера"
            m = re.search(r"(?:^|[^\d])(\d{1,2})\s*(утра|вечера|дня)\b", lower)
            if not m:
                return None
            hour = int(m.group(1))
            minute = 0
            period = m.group(2)
        else:
            hour = int(m.group(1))
            minute = 0
            period = m.group(2)
    if hour > 23 or minute > 59:
        return None
    if period == "вечера" and hour < 12:
        hour += 12
    if period == "утра" and hour == 12:
        hour = 0
    if period == "дня" and hour < 12:
        hour += 12
    return hour, minute


def _daypart_hour(lower: str) -> int | None:
    if re.search(r"\bутром\b|\bутра\b|доброе\s+утро|на\s+завтрак|завтрак|по\s+утрам|каждое\s+утро", lower):
        return DEFAULT_MORNING_HOUR
    if re.search(r"\bвечером\b|\bвечера\b", lower):
        return DEFAULT_EVENING_HOUR
    if re.search(r"\bднем\b|\bднём\b", lower):
        return DEFAULT_DAY_HOUR
    return None


def _next_at_clock(now: datetime, hour: int, minute: int = 0) -> datetime:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


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

    clock = _extract_clock(lower)
    daypart = _daypart_hour(lower)
    hour = clock[0] if clock else (daypart if daypart is not None else 10)
    minute = clock[1] if clock else 0
    time_known = clock is not None or daypart is not None

    base_day = None
    if "послезавтра" in lower:
        base_day = now + timedelta(days=2)
    elif "завтра" in lower:
        base_day = now + timedelta(days=1)
    elif "сегодня" in lower:
        base_day = now

    if base_day is not None:
        if not re.search(r"через\s+\d*\s*мин", lower):
            candidate = base_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if "сегодня" in lower and candidate <= now:
                if time_known:
                    candidate = candidate + timedelta(days=1)
                else:
                    candidate = now + timedelta(minutes=1)
            return candidate

    # Daily / daypart-only without explicit day → next occurrence
    if clock is None and daypart is not None and detect_reminder_repeat(lower) == "daily":
        return _next_at_clock(now, daypart, 0)
    if clock is not None and detect_reminder_repeat(lower) == "daily":
        return _next_at_clock(now, clock[0], clock[1])
    if clock is None and daypart is not None and not base_day:
        # "напомни утром ..." without day → next morning
        return _next_at_clock(now, daypart, 0)
    if clock is not None and not base_day and re.search(r"\b(в\s+\d|утра|вечера|дня)\b", lower):
        # "напомни в 9:00 ..." without day
        if not re.search(r"\d{1,2}[./]\d{1,2}", lower) and not re.search(r"\d{1,2}\s*(-?го|числа)", lower):
            return _next_at_clock(now, clock[0], clock[1])

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
        hh = int(m.group(4)) if m.group(4) else hour
        mm = int(m.group(5)) if m.group(5) else minute
        try:
            candidate = now.replace(
                year=year, month=month, day=day, hour=hh, minute=mm, second=0, microsecond=0
            )
        except ValueError:
            candidate = None
        if candidate is not None:
            if candidate.year < now.year - 1:
                candidate = candidate.replace(year=now.year)
            if candidate <= now and not m.group(3):
                try:
                    candidate = candidate.replace(year=now.year + 1)
                except ValueError:
                    pass
            return candidate

    # --- "20 числа" / "20-го" ---
    m = re.search(r"(\d{1,2})\s*(-?го|числа)", lower)
    if m:
        day = int(m.group(1))
        hh = hour if time_known else 10
        try:
            candidate = now.replace(day=day, hour=hh, minute=minute, second=0, microsecond=0)
        except ValueError:
            candidate = now.replace(
                day=min(day, 28), hour=hh, minute=minute, second=0, microsecond=0
            )
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


def _llm_due_acceptable(due: datetime, user_text: str, reminder_text: str = "") -> bool:
    """Reject hallucinated night times when user meant morning / gave no clock."""
    now = timezone.localtime()
    if timezone.is_naive(due):
        due = timezone.make_aware(due, timezone.get_current_timezone())
    if due.year < now.year or due <= now - timedelta(minutes=1):
        return False
    blob = f"{user_text} {reminder_text}".lower().replace("ё", "е")
    clock = _extract_clock(blob)
    if clock is not None:
        return True
    # Night hours without explicit request are almost always LLM junk for «утро»
    if 0 <= due.hour <= 5:
        if re.search(r"\bноч\w*|\bчас\s+ноч|\bв\s+[01]?\d[:\.]", blob):
            return True
        return False
    if _daypart_hour(blob) is not None:
        # Prefer our daypart defaults over LLM clock
        return False
    return True


def resolve_reminder_schedule(
    user_text: str,
    llm_due: datetime | None = None,
    hint: str | None = None,
    reminder_text: str = "",
    llm_repeat: str | None = None,
    time_answer_only: bool = False,
) -> ReminderSchedule:
    """
    Build a reminder schedule from user words.

    If time is unclear — needs_clarification=True (do not invent 01:00 / tomorrow silently).
    """
    now = timezone.localtime()
    blob = " ".join(
        p for p in [user_text or "", reminder_text or "", hint or ""] if p
    ).lower().replace("ё", "е")
    repeat = detect_reminder_repeat(blob)
    if llm_repeat == "daily":
        repeat = "daily"

    clock = _extract_clock(blob)
    daypart = _daypart_hour(blob)
    parsed = _parse_due(user_text, hint)
    if parsed is None and reminder_text:
        parsed = _parse_due(reminder_text, hint)
    if parsed is None and time_answer_only:
        parsed = _parse_due(user_text, None)

    time_known = clock is not None or daypart is not None
    if parsed and ("через" in blob or re.search(r"\d{1,2}[./]\d{1,2}", blob) or re.search(r"\d{1,2}\s*(-?го|числа)", blob)):
        time_known = True
    if parsed and "через" in (user_text or "").lower():
        time_known = True

    # Daily morning / breakfast without explicit hour → 09:00 every day
    if repeat == "daily" and daypart is not None and clock is None:
        due = _next_at_clock(now, daypart, 0)
        return ReminderSchedule(due_at=due, repeat=repeat, time_known=True)

    if repeat == "daily" and clock is not None:
        due = _next_at_clock(now, clock[0], clock[1])
        return ReminderSchedule(due_at=due, repeat=repeat, time_known=True)

    if parsed and time_known and parsed > now - timedelta(seconds=30):
        return ReminderSchedule(due_at=parsed, repeat=repeat, time_known=True)

    if parsed and "через" in (user_text or "").lower() and parsed > now - timedelta(seconds=30):
        return ReminderSchedule(due_at=parsed, repeat=repeat, time_known=True)

    # One-shot with clear day+time from parser (20 числа, завтра в 15:00, etc.)
    if parsed and parsed > now - timedelta(seconds=30):
        # Day-of-month / date without clock still counts as scheduled (default 10:00)
        if re.search(r"\d{1,2}\s*(-?го|числа)|\d{1,2}[./]\d{1,2}|завтра|послезавтра|сегодня", blob):
            return ReminderSchedule(due_at=parsed, repeat=repeat, time_known=True)

    if llm_due is not None and _llm_due_acceptable(llm_due, user_text, reminder_text):
        due = llm_due
        if timezone.is_naive(due):
            due = timezone.make_aware(due, timezone.get_current_timezone())
        return ReminderSchedule(due_at=due, repeat=repeat, time_known=True)

    # Relative "через" without successful parse → +1 min
    if "через" in blob and ("мин" in blob or "час" in blob):
        return ReminderSchedule(
            due_at=now + timedelta(minutes=1),
            repeat=repeat,
            time_known=True,
        )

    if repeat == "daily":
        q = "Во сколько напоминать каждый день? Например: «в 9 утра» или «в 8:30»."
    else:
        q = "Когда напомнить? Укажи день и время, например: «завтра в 10:00» или «каждый день в 9 утра»."
    return ReminderSchedule(
        due_at=None,
        repeat=repeat,
        needs_clarification=True,
        clarify_question=q,
        time_known=False,
    )


def resolve_reminder_due(
    user_text: str, llm_due: datetime | None = None, hint: str | None = None
) -> datetime:
    """Backward-compatible helper: returns a due datetime or raises if unclear."""
    schedule = resolve_reminder_schedule(user_text, llm_due=llm_due, hint=hint)
    if schedule.due_at is not None:
        return schedule.due_at
    now = timezone.localtime()
    if "через" in (user_text or "").lower() or "минут" in (user_text or "").lower():
        return now + timedelta(minutes=1)
    return _next_at_clock(now, 10, 0)


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
            "«через 1 минуту» = сейчас+1 минута. Не выдумывай прошлые годы. "
            "Не ставь 01:00 для «утро» — пиши due_hint=утро или needs_time_clarify=true."
        )
        raw_text = llm.complete_text(system, text, temperature=0.1, max_tokens=600)
        data = _extract_json(raw_text)
        intent = (data.get("intent") or "chat").strip()
        # Always prefer deterministic parse from user text for reminders
        due = _parse_due(text, data.get("due_hint") or data.get("due_at"))
        if due is None:
            due = _parse_due(data.get("due_at"), data.get("due_hint"))
        repeat = data.get("repeat") or detect_reminder_repeat(text)
        if repeat not in {"none", "daily"}:
            repeat = detect_reminder_repeat(text)
        return IntentResult(
            intent=intent,
            should_save=bool(data.get("should_save")),
            memory_text=data.get("memory_text") or None,
            category=data.get("category") or MemoryCategory.OTHER,
            task_text=data.get("task_text") or None,
            reminder_text=data.get("reminder_text") or None,
            due_at=due,
            due_hint=data.get("due_hint"),
            repeat=repeat,
            needs_time_clarify=bool(data.get("needs_time_clarify")),
            query=data.get("query"),
            confidence=float(data.get("confidence") or 0.5),
            raw=data,
        )

    def _heuristic_fallback(self, text: str) -> IntentResult:
        lower = text.lower()
        if "напомн" in lower or "напоминани" in lower:
            due = _parse_due(text, None)
            return IntentResult(
                intent="create_reminder",
                reminder_text=extract_reminder_body(text),
                due_at=due,
                repeat=detect_reminder_repeat(text),
                needs_time_clarify=due is None,
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
