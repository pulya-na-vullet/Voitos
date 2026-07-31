from __future__ import annotations

import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from ai.factory import get_runtime_settings
from ai.yandex import normalize_yandex_model
from bot.client import MaxApiError, MaxClient
from bot.status import set_bot_error, set_bot_status
from database.dump import create_db_dump
from database.models import (
    ActivityKind,
    ActivityLog,
    AppSettings,
    BotRuntimeStatus,
    BotUser,
    ChatMessage,
    MemoryItem,
    PaymentReceipt,
    ReceiptStatus,
    Reminder,
    TaskItem,
)
from logs.service import log_activity
from subscriptions.service import (
    approve_receipt,
    approved_user_message,
    reject_receipt,
    rejected_user_message,
)

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


def _notify_user(user: BotUser, text: str) -> None:
    cfg = get_runtime_settings()
    if not cfg.max_bot_token:
        return
    client = MaxClient(cfg.max_bot_token)
    try:
        if user.chat_id:
            client.send_message(text, chat_id=user.chat_id)
        else:
            client.send_message(text, user_id=user.max_user_id)
    except Exception:
        try:
            client.send_message(text, user_id=user.max_user_id)
        except Exception:
            pass


def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("panel:users")
    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("panel:users")
        error = "Неверный логин или пароль"
    return render(request, "panel/login.html", {"error": error})


@login_required
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("panel:login")


@login_required
def users_list(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    users = BotUser.objects.all()
    if q:
        users = users.filter(
            Q(display_name__icontains=q)
            | Q(username__icontains=q)
            | Q(max_user_id__icontains=q)
        )
    bot_status = BotRuntimeStatus.load()
    pending_receipts = PaymentReceipt.objects.filter(status=ReceiptStatus.PENDING).count()
    return render(
        request,
        "panel/users.html",
        {
            "users": users[:500],
            "q": q,
            "bot_status": bot_status,
            "pending_receipts": pending_receipts,
            "stats": {
                "users": BotUser.objects.count(),
                "active": sum(1 for u in BotUser.objects.all() if u.access_state() == "active"),
                "receipts": PaymentReceipt.objects.count(),
            },
        },
    )


@login_required
def user_dashboard(request: HttpRequest, user_id: int) -> HttpResponse:
    bot_user = get_object_or_404(BotUser, pk=user_id)
    since = timezone.now() - timedelta(days=14)
    activity_qs = (
        ActivityLog.objects.filter(user=bot_user, created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    return render(
        request,
        "panel/user_dashboard.html",
        {
            "bot_user": bot_user,
            "access_state": bot_user.access_state(),
            "stats": {
                "messages": ChatMessage.objects.filter(user=bot_user).count(),
                "memories": MemoryItem.objects.filter(user=bot_user).count(),
                "tasks": TaskItem.objects.filter(user=bot_user).count(),
                "reminders": Reminder.objects.filter(user=bot_user, is_done=False).count(),
                "receipts": PaymentReceipt.objects.filter(user=bot_user).count(),
            },
            "recent_messages": ChatMessage.objects.filter(user=bot_user)[:15],
            "recent_logs": ActivityLog.objects.filter(user=bot_user)[:15],
            "receipts": PaymentReceipt.objects.filter(user=bot_user)[:10],
            "chart_labels_json": json.dumps(
                [row["day"].strftime("%d.%m") for row in activity_qs], ensure_ascii=False
            ),
            "chart_values_json": json.dumps([row["count"] for row in activity_qs]),
        },
    )


@login_required
def user_messages(request: HttpRequest, user_id: int) -> HttpResponse:
    bot_user = get_object_or_404(BotUser, pk=user_id)
    qs = ChatMessage.objects.filter(user=bot_user)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(text__icontains=q)
    return render(
        request,
        "panel/messages.html",
        {"bot_user": bot_user, "items": qs[:300], "q": q},
    )


@login_required
def user_memories(request: HttpRequest, user_id: int) -> HttpResponse:
    bot_user = get_object_or_404(BotUser, pk=user_id)
    return render(
        request,
        "panel/memories.html",
        {"bot_user": bot_user, "items": MemoryItem.objects.filter(user=bot_user)[:500]},
    )


@login_required
def user_tasks(request: HttpRequest, user_id: int) -> HttpResponse:
    bot_user = get_object_or_404(BotUser, pk=user_id)
    return render(
        request,
        "panel/tasks.html",
        {"bot_user": bot_user, "items": TaskItem.objects.filter(user=bot_user)[:500]},
    )


@login_required
def user_reminders(request: HttpRequest, user_id: int) -> HttpResponse:
    bot_user = get_object_or_404(BotUser, pk=user_id)
    return render(
        request,
        "panel/reminders.html",
        {"bot_user": bot_user, "items": Reminder.objects.filter(user=bot_user)[:500]},
    )


@login_required
def user_logs(request: HttpRequest, user_id: int) -> HttpResponse:
    bot_user = get_object_or_404(BotUser, pk=user_id)
    qs = ActivityLog.objects.filter(user=bot_user)
    kind = request.GET.get("kind", "").strip()
    if kind:
        qs = qs.filter(kind=kind)
    return render(
        request,
        "panel/logs.html",
        {
            "bot_user": bot_user,
            "items": qs[:500],
            "kinds": ActivityKind.choices,
            "kind": kind,
        },
    )


@login_required
def receipts_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "").strip()
    qs = PaymentReceipt.objects.select_related("user").all()
    if status:
        qs = qs.filter(status=status)
    return render(
        request,
        "panel/receipts.html",
        {
            "items": qs[:300],
            "status": status,
            "statuses": ReceiptStatus.choices,
            "pending_count": PaymentReceipt.objects.filter(status=ReceiptStatus.PENDING).count(),
        },
    )


@login_required
def user_receipts(request: HttpRequest, user_id: int) -> HttpResponse:
    bot_user = get_object_or_404(BotUser, pk=user_id)
    return render(
        request,
        "panel/receipts.html",
        {
            "bot_user": bot_user,
            "items": PaymentReceipt.objects.filter(user=bot_user)[:300],
            "statuses": ReceiptStatus.choices,
            "status": "",
            "pending_count": PaymentReceipt.objects.filter(
                user=bot_user, status=ReceiptStatus.PENDING
            ).count(),
        },
    )


@login_required
@require_POST
def receipt_approve(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(PaymentReceipt, pk=pk)
    comment = request.POST.get("comment", "").strip()
    try:
        approve_receipt(receipt, comment=comment)
        _notify_user(receipt.user, approved_user_message(receipt))
        messages.success(request, f"Чек #{pk} принят, подписка продлена.")
    except ValueError as exc:
        messages.error(request, str(exc))
    next_url = request.POST.get("next") or "panel:receipts"
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect(next_url)


@login_required
@require_POST
def receipt_reject(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(PaymentReceipt, pk=pk)
    comment = request.POST.get("comment", "").strip() or "Реквизиты не подтверждены"
    reject_receipt(receipt, comment=comment)
    _notify_user(receipt.user, rejected_user_message(receipt))
    messages.success(request, f"Чек #{pk} отклонён, пользователь уведомлён.")
    next_url = request.POST.get("next") or "panel:receipts"
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect(next_url)


@login_required
@require_http_methods(["GET", "POST"])
def settings_view(request: HttpRequest) -> HttpResponse:
    cfg = AppSettings.load()
    if request.method == "POST":
        new_token = request.POST.get("max_bot_token", "").strip()
        new_key = request.POST.get("yandex_api_key", "").strip()
        if new_token:
            cfg.max_bot_token = new_token
        if new_key:
            cfg.yandex_api_key = new_key
        cfg.allowed_max_user_id = ""  # multi-user
        cfg.yandex_folder_id = request.POST.get("yandex_folder_id", "").strip()
        cfg.yandex_model = normalize_yandex_model(
            request.POST.get("yandex_model", "yandexgpt-lite").strip() or "yandexgpt-lite"
        )
        cfg.bot_display_name = request.POST.get("bot_display_name", "Voitos").strip() or "Voitos"
        cfg.payment_phone = request.POST.get("payment_phone", cfg.payment_phone).strip() or cfg.payment_phone
        cfg.payment_name = request.POST.get("payment_name", cfg.payment_name).strip() or cfg.payment_name
        try:
            cfg.subscription_price_rub = int(request.POST.get("subscription_price_rub") or 100)
        except ValueError:
            cfg.subscription_price_rub = 100
        try:
            cfg.grace_days = int(request.POST.get("grace_days") or 2)
        except ValueError:
            cfg.grace_days = 2
        cfg.save()
        log_activity(kind=ActivityKind.SETTINGS, title="Обновлены настройки", detail="Из панели")
        warn = _model_warning(cfg.yandex_model)
        if warn:
            messages.warning(request, warn)
        messages.success(request, "Настройки сохранены.")
        return redirect("panel:settings")
    return render(
        request,
        "panel/settings.html",
        {
            "cfg": cfg,
            "model_warning": _model_warning(cfg.yandex_model),
            "has_token": bool(cfg.max_bot_token),
            "has_api_key": bool(cfg.yandex_api_key),
            "bot_status": BotRuntimeStatus.load(),
        },
    )


@login_required
@require_POST
def check_max(request: HttpRequest) -> HttpResponse:
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
        messages.success(request, f"Связь с MAX OK: {name}. Webhook снято: {removed}.")
    except MaxApiError as exc:
        set_bot_error(str(exc))
        messages.error(request, f"MAX API ошибка: {exc}")
    except Exception as exc:
        set_bot_error(str(exc))
        messages.error(request, f"Ошибка проверки: {exc}")
    return redirect("panel:users")


@login_required
@require_POST
def delete_memory(request: HttpRequest, pk: int) -> HttpResponse:
    from memory.service import MemoryService

    item = get_object_or_404(MemoryItem, pk=pk)
    uid = item.user_id
    MemoryService().delete(item.id)
    messages.success(request, "Память удалена")
    return redirect(request.POST.get("next") or f"/panel/users/{uid}/memories/")


@login_required
@require_POST
def delete_task(request: HttpRequest, pk: int) -> HttpResponse:
    from tasks.service import TaskService

    item = get_object_or_404(TaskItem, pk=pk)
    uid = item.user_id
    TaskService().delete(pk)
    messages.success(request, "Задача удалена")
    return redirect(request.POST.get("next") or f"/panel/users/{uid}/tasks/")


@login_required
@require_POST
def delete_reminder(request: HttpRequest, pk: int) -> HttpResponse:
    from reminders.service import ReminderService

    item = get_object_or_404(Reminder, pk=pk)
    uid = item.user_id
    ReminderService().delete(pk)
    messages.success(request, "Напоминание удалено")
    return redirect(request.POST.get("next") or f"/panel/users/{uid}/reminders/")


@login_required
@require_POST
def delete_message(request: HttpRequest, pk: int) -> HttpResponse:
    item = get_object_or_404(ChatMessage, pk=pk)
    uid = item.user_id
    item.delete()
    messages.success(request, "Сообщение удалено")
    return redirect(request.POST.get("next") or f"/panel/users/{uid}/messages/")


@login_required
@require_POST
def dump_now(request: HttpRequest) -> HttpResponse:
    path = create_db_dump()
    messages.success(request, f"Дамп создан: {path.name}")
    return redirect("panel:users")


# Backward-compatible aliases
@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    return redirect("panel:users")
