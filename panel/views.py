from __future__ import annotations

import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from database.dump import create_db_dump
from database.models import (
    ActivityKind,
    ActivityLog,
    AppSettings,
    BotRuntimeStatus,
    BotUser,
    ChatMessage,
    MemoryItem,
    Reminder,
    TaskItem,
)
from logs.service import log_activity

YANDEX_MODEL_HINTS = ("yandexgpt-lite", "yandexgpt", "yandexgpt-5-pro", "yandexgpt-32k")


def _model_warning(model: str) -> str:
    m = (model or "").strip().lower()
    if not m:
        return ""
    if "deepseek" in m or m not in {x.lower() for x in YANDEX_MODEL_HINTS} and not m.startswith("yandex"):
        return (
            f"Модель «{model}» может не работать через Yandex Foundation Models. "
            "Укажите, например: yandexgpt-lite или yandexgpt."
        )
    return ""


def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("panel:dashboard")
    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("panel:dashboard")
        error = "Неверный логин или пароль"
    return render(request, "panel/login.html", {"error": error})


@login_required
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("panel:login")


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    cfg = AppSettings.load()
    bot_status = BotRuntimeStatus.load()
    since = timezone.now() - timedelta(days=14)
    activity_qs = (
        ActivityLog.objects.filter(created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    chart_labels = [row["day"].strftime("%d.%m") for row in activity_qs]
    chart_values = [row["count"] for row in activity_qs]
    model_warn = _model_warning(cfg.yandex_model)

    context = {
        "cfg": cfg,
        "bot_status": bot_status,
        "model_warning": model_warn,
        "stats": {
            "messages": ChatMessage.objects.count(),
            "memories": MemoryItem.objects.count(),
            "tasks": TaskItem.objects.count(),
            "reminders": Reminder.objects.filter(is_done=False).count(),
            "users": BotUser.objects.count(),
        },
        "recent_messages": ChatMessage.objects.select_related("user")[:15],
        "recent_logs": ActivityLog.objects.select_related("user")[:15],
        "chart_labels_json": json.dumps(chart_labels, ensure_ascii=False),
        "chart_values_json": json.dumps(chart_values),
        "ai_ok": cfg.is_ai_configured(),
        "bot_ok": cfg.is_bot_configured(),
    }
    return render(request, "panel/dashboard.html", context)


@login_required
def messages_view(request: HttpRequest) -> HttpResponse:
    qs = ChatMessage.objects.select_related("user").all()
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(text__icontains=q)
    return render(
        request,
        "panel/messages.html",
        {"items": qs[:300], "q": q},
    )


@login_required
def memories_view(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "panel/memories.html",
        {"items": MemoryItem.objects.select_related("user").all()[:500]},
    )


@login_required
def tasks_view(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "panel/tasks.html",
        {"items": TaskItem.objects.select_related("user").all()[:500]},
    )


@login_required
def reminders_view(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "panel/reminders.html",
        {"items": Reminder.objects.select_related("user").all()[:500]},
    )


@login_required
def logs_view(request: HttpRequest) -> HttpResponse:
    qs = ActivityLog.objects.select_related("user").all()
    kind = request.GET.get("kind", "").strip()
    if kind:
        qs = qs.filter(kind=kind)
    return render(
        request,
        "panel/logs.html",
        {"items": qs[:500], "kinds": ActivityKind.choices, "kind": kind},
    )


@login_required
@require_http_methods(["GET", "POST"])
def settings_view(request: HttpRequest) -> HttpResponse:
    cfg = AppSettings.load()
    if request.method == "POST":
        # Keep existing secrets if password fields submitted empty
        new_token = request.POST.get("max_bot_token", "").strip()
        new_key = request.POST.get("yandex_api_key", "").strip()
        if new_token:
            cfg.max_bot_token = new_token
        if new_key:
            cfg.yandex_api_key = new_key
        cfg.allowed_max_user_id = request.POST.get("allowed_max_user_id", "").strip()
        cfg.yandex_folder_id = request.POST.get("yandex_folder_id", "").strip()
        cfg.yandex_model = request.POST.get("yandex_model", "yandexgpt-lite").strip() or "yandexgpt-lite"
        cfg.bot_display_name = request.POST.get("bot_display_name", "Voitos").strip() or "Voitos"
        cfg.save()
        log_activity(
            kind=ActivityKind.SETTINGS,
            title="Обновлены настройки",
            detail="Из панели администратора",
            user=None,
        )
        warn = _model_warning(cfg.yandex_model)
        if warn:
            messages.warning(request, warn)
        messages.success(request, "Настройки сохранены. Бот подхватит токен автоматически.")
        return redirect("panel:settings")
    return render(
        request,
        "panel/settings.html",
        {
            "cfg": cfg,
            "model_warning": _model_warning(cfg.yandex_model),
            "has_token": bool(cfg.max_bot_token),
            "has_api_key": bool(cfg.yandex_api_key),
        },
    )


@login_required
@require_POST
def check_max(request: HttpRequest) -> HttpResponse:
    """Manual connectivity check: GET /me + clear webhooks."""
    from bot.client import MaxApiError, MaxClient
    from bot.status import set_bot_error, set_bot_status

    cfg = AppSettings.load()
    if not cfg.max_bot_token:
        messages.error(request, "Сначала сохраните токен MAX")
        return redirect("panel:settings")
    try:
        client = MaxClient(cfg.max_bot_token)
        me = client.get_me()
        removed = client.clear_webhooks()
        name = me.get("name") or me.get("username") or me
        set_bot_status(
            state="connected",
            detail=f"Проверка OK: {name}. Webhook снято: {removed}",
            bot_name=str(me.get("name") or ""),
            bot_username=str(me.get("username") or ""),
            clear_error=True,
        )
        messages.success(
            request,
            f"Связь с MAX OK: {name}. Снято webhook-подписок: {removed}. "
            "Теперь напишите боту «привет» ещё раз.",
        )
    except MaxApiError as exc:
        set_bot_error(str(exc))
        messages.error(request, f"MAX API ошибка: {exc}")
    except Exception as exc:
        set_bot_error(str(exc))
        messages.error(request, f"Ошибка проверки: {exc}")
    return redirect("panel:dashboard")


@login_required
@require_POST
def delete_memory(request: HttpRequest, pk: int) -> HttpResponse:
    item = get_object_or_404(MemoryItem, pk=pk)
    from memory.service import MemoryService

    MemoryService().delete(item.id)
    messages.success(request, "Память удалена")
    return redirect(request.POST.get("next") or "panel:memories")


@login_required
@require_POST
def delete_task(request: HttpRequest, pk: int) -> HttpResponse:
    from tasks.service import TaskService

    TaskService().delete(pk)
    messages.success(request, "Задача удалена")
    return redirect(request.POST.get("next") or "panel:tasks")


@login_required
@require_POST
def delete_reminder(request: HttpRequest, pk: int) -> HttpResponse:
    from reminders.service import ReminderService

    ReminderService().delete(pk)
    messages.success(request, "Напоминание удалено")
    return redirect(request.POST.get("next") or "panel:reminders")


@login_required
@require_POST
def delete_message(request: HttpRequest, pk: int) -> HttpResponse:
    item = get_object_or_404(ChatMessage, pk=pk)
    item.delete()
    messages.success(request, "Сообщение удалено")
    return redirect(request.POST.get("next") or "panel:messages")


@login_required
@require_POST
def dump_now(request: HttpRequest) -> HttpResponse:
    path = create_db_dump()
    messages.success(request, f"Дамп создан: {path.name}")
    return redirect("panel:dashboard")


@login_required
def activity_api(request: HttpRequest) -> JsonResponse:
    days = int(request.GET.get("days", 14))
    since = timezone.now() - timedelta(days=days)
    rows = (
        ActivityLog.objects.filter(created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    return JsonResponse(
        {
            "labels": [r["day"].strftime("%Y-%m-%d") for r in rows],
            "values": [r["count"] for r in rows],
        }
    )
