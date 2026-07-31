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
from decimal import Decimal, InvalidOperation

from database.models import (
    ActivityKind,
    ActivityLog,
    AppSettings,
    BotRuntimeStatus,
    BotUser,
    CampaignStatus,
    ChatMessage,
    MemoryItem,
    PaymentReceipt,
    ProfileStatus,
    ReceiptStatus,
    Reminder,
    ServiceCampaign,
    ServiceCategory,
    ServiceGroup,
    ServiceReceipt,
    TaskItem,
)
from logs.service import log_activity
from services.ranking import citizen_stats, ranking_list
from services.service import (
    approve_service_receipt,
    approved_service_message,
    invite_new_members_to_group_campaigns,
    launch_campaign_to_group,
    offer_to_users,
    reject_service_receipt,
    rejected_service_message,
)
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
            | Q(real_name__icontains=q)
            | Q(locality__icontains=q)
            | Q(max_user_id__icontains=q)
        )
    bot_status = BotRuntimeStatus.load()
    pending_receipts = PaymentReceipt.objects.filter(status=ReceiptStatus.PENDING).count()
    pending_profiles = BotUser.objects.filter(profile_status=ProfileStatus.PENDING_REVIEW).count()
    pending_service = ServiceReceipt.objects.filter(status=ReceiptStatus.PENDING).count()
    return render(
        request,
        "panel/users.html",
        {
            "users": users[:500],
            "q": q,
            "bot_status": bot_status,
            "pending_receipts": pending_receipts,
            "pending_profiles": pending_profiles,
            "pending_service": pending_service,
            "tax_warning": AppSettings.load().tax_limit_warning(),
            "stats": {
                "users": BotUser.objects.count(),
                "active": sum(1 for u in BotUser.objects.all() if u.access_state() == "active"),
                "receipts": PaymentReceipt.objects.count(),
            },
        },
    )


@login_required
@require_POST
def user_profile_verify(request: HttpRequest, user_id: int) -> HttpResponse:
    bot_user = get_object_or_404(BotUser, pk=user_id)
    action = request.POST.get("action")
    note = request.POST.get("note", "").strip()
    bot_user.profile_admin_note = note
    if action == "verify":
        bot_user.profile_status = ProfileStatus.VERIFIED
        bot_user.profile_verified_at = timezone.now()
        bot_user.locality = request.POST.get("locality", bot_user.locality).strip() or bot_user.locality
        bot_user.real_name = request.POST.get("real_name", bot_user.real_name).strip() or bot_user.real_name
        bot_user.phone = request.POST.get("phone", bot_user.phone).strip() or bot_user.phone
        bot_user.address = request.POST.get("address", bot_user.address).strip() or bot_user.address
        ActivityLog.objects.create(
            user=bot_user,
            kind=ActivityKind.PROFILE_VERIFIED,
            title="Анкета проверена",
            detail=note or "OK",
        )
        _notify_user(bot_user, "Администратор проверил ваши данные. Анкета принята.")
        messages.success(request, "Анкета подтверждена.")
    elif action == "reject":
        bot_user.profile_status = ProfileStatus.REJECTED
        ActivityLog.objects.create(
            user=bot_user,
            kind=ActivityKind.PROFILE_VERIFIED,
            title="Анкета отклонена",
            detail=note or "Отклонено",
        )
        _notify_user(
            bot_user,
            "Администратор отклонил анкету. "
            + (f"Комментарий: {note}. " if note else "")
            + "Напишите «регистрация», чтобы заполнить снова.",
        )
        messages.success(request, "Анкета отклонена, пользователь уведомлён.")
    bot_user.save()
    return redirect("panel:user_dashboard", user_id=user_id)


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
            "citizen": citizen_stats(bot_user),
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
        cfg.service_payee_name = (
            request.POST.get("service_payee_name", cfg.service_payee_name).strip()
            or cfg.service_payee_name
        )
        cfg.service_payee_phone = (
            request.POST.get("service_payee_phone", cfg.service_payee_phone).strip()
            or cfg.service_payee_phone
        )
        cfg.service_payee_status = (
            request.POST.get("service_payee_status", cfg.service_payee_status).strip()
            or cfg.service_payee_status
        )
        try:
            cfg.service_tax_limit = Decimal(
                request.POST.get("service_tax_limit") or cfg.service_tax_limit or "2400000"
            )
        except (InvalidOperation, ValueError):
            pass
        try:
            if request.POST.get("service_tax_collected") not in (None, ""):
                cfg.service_tax_collected = Decimal(request.POST.get("service_tax_collected"))
        except (InvalidOperation, ValueError):
            pass
        cfg.save()
        log_activity(kind=ActivityKind.SETTINGS, title="Обновлены настройки", detail="Из панели")
        warn = _model_warning(cfg.yandex_model)
        if warn:
            messages.warning(request, warn)
        tax_warn = cfg.tax_limit_warning()
        if tax_warn:
            messages.warning(request, tax_warn)
        messages.success(request, "Настройки сохранены.")
        return redirect("panel:settings")
    return render(
        request,
        "panel/settings.html",
        {
            "cfg": cfg,
            "model_warning": _model_warning(cfg.yandex_model),
            "tax_warning": cfg.tax_limit_warning(),
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


@login_required
@require_http_methods(["GET", "POST"])
def services_home(request: HttpRequest) -> HttpResponse:
    cfg = AppSettings.load()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_group":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Укажите название группы")
            else:
                group = ServiceGroup.objects.create(
                    name=name,
                    description=request.POST.get("description", "").strip(),
                )
                messages.success(request, f"Группа «{group.name}» создана. Добавьте участников.")
                return redirect("panel:service_group_edit", pk=group.id)
        elif action == "launch":
            group_id = request.POST.get("group_id")
            group = get_object_or_404(ServiceGroup, pk=group_id)
            category = request.POST.get("category", "").strip()
            try:
                total = Decimal(request.POST.get("total_amount") or "0")
                per_user = Decimal(request.POST.get("amount_per_user") or "0")
            except (InvalidOperation, ValueError):
                total, per_user = Decimal("0"), Decimal("0")
            try:
                campaign, sent = launch_campaign_to_group(
                    category=category,
                    title=request.POST.get("title", "").strip()
                    or dict(ServiceCategory.choices).get(category, "Сбор"),
                    description=request.POST.get("description", "").strip(),
                    group=group,
                    total_amount=total,
                    amount_per_user=per_user,
                    send_fn=_notify_user,
                )
                messages.success(
                    request,
                    f"Сбор запущен для группы «{group.name}»: разослано {sent} сообщ.",
                )
                tax_warn = AppSettings.load().tax_limit_warning()
                if tax_warn:
                    messages.warning(request, tax_warn)
                return redirect("panel:service_campaign_detail", pk=campaign.id)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("panel:services")
        return redirect("panel:services")

    categories = []
    for value, label in ServiceCategory.choices:
        qs = ServiceCampaign.objects.filter(category=value)
        categories.append(
            {
                "value": value,
                "label": label,
                "count": qs.count(),
                "active": qs.filter(status=CampaignStatus.ACTIVE).count(),
                "pending_receipts": ServiceReceipt.objects.filter(
                    campaign__category=value, status=ReceiptStatus.PENDING
                ).count(),
            }
        )
    groups = ServiceGroup.objects.prefetch_related("members").all()
    recent = ServiceCampaign.objects.select_related("group").all()[:20]
    return render(
        request,
        "panel/services_home.html",
        {
            "categories": categories,
            "groups": groups,
            "recent_campaigns": recent,
            "category_choices": ServiceCategory.choices,
            "tax_warning": cfg.tax_limit_warning(),
            "cfg": cfg,
            "pending_service": ServiceReceipt.objects.filter(
                status=ReceiptStatus.PENDING
            ).count(),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def service_group_edit(request: HttpRequest, pk: int) -> HttpResponse:
    group = get_object_or_404(ServiceGroup, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete":
            name = group.name
            group.delete()
            messages.success(request, f"Группа «{name}» удалена")
            return redirect("panel:services")
        group.name = request.POST.get("name", group.name).strip() or group.name
        group.description = request.POST.get("description", "").strip()
        group.save()
        old_ids = set(group.members.values_list("id", flat=True))
        ids = [int(x) for x in request.POST.getlist("user_ids") if str(x).isdigit()]
        group.members.set(BotUser.objects.filter(id__in=ids))
        new_ids = [i for i in ids if i not in old_ids]
        notified = 0
        if new_ids:
            notified = invite_new_members_to_group_campaigns(
                group, new_ids, send_fn=_notify_user
            )
        if notified:
            messages.success(
                request,
                f"Группа сохранена. Новым участникам отправлено сборов: {notified}.",
            )
        else:
            messages.success(request, "Группа сохранена")
        return redirect("panel:service_group_edit", pk=pk)

    member_ids = set(group.members.values_list("id", flat=True))
    users = BotUser.objects.all().order_by("real_name", "display_name")
    return render(
        request,
        "panel/service_group_edit.html",
        {
            "group": group,
            "users": users,
            "member_ids": member_ids,
        },
    )


@login_required
def services_category(request: HttpRequest, category: str) -> HttpResponse:
    if category not in ServiceCategory.values:
        messages.error(request, "Неизвестная категория")
        return redirect("panel:services")
    label = dict(ServiceCategory.choices)[category]
    campaigns = ServiceCampaign.objects.filter(category=category)
    return render(
        request,
        "panel/services_category.html",
        {
            "category": category,
            "category_label": label,
            "campaigns": campaigns,
            "tax_warning": AppSettings.load().tax_limit_warning(),
        },
    )


@login_required
def service_campaign_create(request: HttpRequest, category: str) -> HttpResponse:
    """Legacy URL — campaigns are launched from /services/ to a group."""
    return redirect("panel:services")


@login_required
@require_http_methods(["GET", "POST"])
def service_campaign_detail(request: HttpRequest, pk: int) -> HttpResponse:
    campaign = get_object_or_404(ServiceCampaign.objects.select_related("group"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "resend" and campaign.group_id:
            amount = campaign.amount_per_user or Decimal("0")
            ids = list(campaign.group.members.values_list("id", flat=True))
            if amount <= 0 or not ids:
                messages.error(request, "Нет группы или суммы для рассылки")
            else:
                sent = offer_to_users(campaign, ids, amount, send_fn=_notify_user)
                messages.success(request, f"Повторная рассылка: {sent} сообщ.")
            return redirect("panel:service_campaign_detail", pk=pk)
        if action == "close":
            campaign.status = CampaignStatus.CLOSED
            campaign.closed_at = timezone.now()
            campaign.save(update_fields=["status", "closed_at"])
            messages.success(request, "Мероприятие закрыто")
            return redirect("panel:service_campaign_detail", pk=pk)

    from django.db.models import Sum

    paid = campaign.invites.aggregate(s=Sum("amount_paid"))["s"] or Decimal("0")
    total = Decimal(campaign.total_amount or 0)
    pct = min(100, int(paid * 100 / total)) if total > 0 else 0
    return render(
        request,
        "panel/service_campaign_detail.html",
        {
            "campaign": campaign,
            "invites": campaign.invites.select_related("user").all(),
            "receipts": campaign.receipts.select_related("user", "invite").all()[:200],
            "collected": paid,
            "progress_pct": pct,
            "tax_warning": AppSettings.load().tax_limit_warning(),
            "cfg": AppSettings.load(),
        },
    )


@login_required
@require_POST
def service_receipt_approve(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(ServiceReceipt, pk=pk)
    comment = request.POST.get("comment", "").strip()
    try:
        approve_service_receipt(receipt, comment=comment)
        _notify_user(receipt.user, approved_service_message(receipt))
        messages.success(request, f"Сервис-чек #{pk} принят.")
        tax_warn = AppSettings.load().tax_limit_warning()
        if tax_warn:
            messages.warning(request, tax_warn)
    except ValueError as exc:
        messages.error(request, str(exc))
    next_url = request.POST.get("next") or f"/panel/services/campaigns/{receipt.campaign_id}/"
    return redirect(next_url)


@login_required
@require_POST
def service_receipt_reject(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(ServiceReceipt, pk=pk)
    comment = request.POST.get("comment", "").strip() or "Реквизиты не подтверждены"
    reject_service_receipt(receipt, comment=comment)
    _notify_user(receipt.user, rejected_service_message(receipt))
    messages.success(request, f"Сервис-чек #{pk} отклонён.")
    next_url = request.POST.get("next") or f"/panel/services/campaigns/{receipt.campaign_id}/"
    return redirect(next_url)


@login_required
def services_ranking(request: HttpRequest) -> HttpResponse:
    locality = request.GET.get("locality", "").strip()
    rows = ranking_list(locality=locality)
    localities = (
        BotUser.objects.exclude(locality="")
        .values_list("locality", flat=True)
        .distinct()
        .order_by("locality")
    )
    return render(
        request,
        "panel/services_ranking.html",
        {"rows": rows, "locality": locality, "localities": localities},
    )
