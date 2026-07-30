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
    raw = (value or hint or "").strip()
    if not raw:
        return None
    now = timezone.localtime()

    lower = raw.lower()
    # Relative helpers
    if "через полгода" in lower or "через 6 месяцев" in lower:
        return now + timedelta(days=182)
    m = re.search(r"через\s+(\d+)\s*(час|часа|часов|день|дня|дней|недел|месяц)", lower)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("час"):
            return now + timedelta(hours=n)
        if unit.startswith("день") or unit.startswith("дня") or unit.startswith("дней"):
            return now + timedelta(days=n)
        if unit.startswith("недел"):
            return now + timedelta(weeks=n)
        if unit.startswith("месяц"):
            return now + timedelta(days=30 * n)

    # "20 числа" / "20-го"
    m = re.search(r"(\d{1,2})\s*(-?го|числа)", lower)
    if m:
        day = int(m.group(1))
        candidate = now.replace(day=min(day, 28), hour=10, minute=0, second=0, microsecond=0)
        try:
            candidate = now.replace(day=day, hour=10, minute=0, second=0, microsecond=0)
        except ValueError:
            pass
        if candidate <= now:
            month = candidate.month + 1
            year = candidate.year
            if month > 12:
                month = 1
                year += 1
            candidate = candidate.replace(year=year, month=month)
        return candidate

    try:
        dt = date_parser.parse(raw, dayfirst=True, fuzzy=True)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt
    except (ValueError, OverflowError, TypeError):
        logger.warning("Could not parse due date: %s", raw)
        return None


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
        raw_text = llm.complete_text(INTENT_SYSTEM_PROMPT, text, temperature=0.1, max_tokens=600)
        data = _extract_json(raw_text)
        intent = (data.get("intent") or "chat").strip()
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
