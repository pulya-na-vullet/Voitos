from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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
    WorkStage,
    YandexBillingEntry,
)
from logs.service import log_activity
from services.ranking import citizen_stats, ranking_list, sort_ranking_rows
from services.service import (
    WORK_STAGE_NEXT,
    WORK_STAGE_ORDER,
    advance_work_stage,
    approve_service_receipt,
    approved_service_message,
    delete_service_campaign,
    invite_new_members_to_group_campaigns,
    launch_campaign_to_group,
    notify_members_added_to_group,
    reject_service_receipt,
    rejected_service_message,
    resend_to_unpaid,
)
from subscriptions.finance import build_finance_snapshot
from subscriptions.service import (
    approve_receipt,
    approved_user_message,
    delete_receipt,
    deleted_user_message,
    reject_receipt,
    rejected_user_message,
)

YANDEX_MODEL_HINTS = ("yandexgpt-lite", "yandexgpt", "yandexgpt-5-pro", "yandexgpt-32k")
logger = logging.getLogger(__name__)


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


def _notify_user_with_images(
    user: BotUser,
    text: str,
    images: list[tuple[bytes, str]],
    *,
    _token_cache: dict | None = None,
) -> None:
    """Send text + up to 2 images via MAX. Reuses upload tokens via _token_cache.

    If CDN upload fails, falls back to text-only so campaign broadcasts still reach users.
    """
    cfg = get_runtime_settings()
    if not cfg.max_bot_token:
        return
    client = MaxClient(cfg.max_bot_token)
    cache = _token_cache if _token_cache is not None else {}
    if "upload_attempted" not in cache:
        cache["upload_attempted"] = True
        tokens: list[str] = []
        try:
            for raw, filename in images[:2]:
                tokens.append(client.upload_image(raw, filename or "photo.jpg"))
            cache["tokens"] = tokens
            cache["attachments"] = client.image_attachments(tokens)
        except Exception:
            logger.exception("MAX image upload failed; sending text-only notification")
            cache["tokens"] = []
            cache["attachments"] = []
    attachments = cache.get("attachments") or []
    try:
        if user.chat_id:
            client.send_message(text, chat_id=user.chat_id, attachments=attachments or None)
        else:
            client.send_message(text, user_id=user.max_user_id, attachments=attachments or None)
    except Exception:
        try:
            client.send_message(text, user_id=user.max_user_id, attachments=attachments or None)
        except Exception:
            # Last resort: text without attachments
            try:
                _notify_user(user, text)
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
    locality = request.GET.get("locality", "").strip()
    sort = request.GET.get("sort", "-rating").strip() or "-rating"
    rows = ranking_list(locality=locality, q=q)
    rows = sort_ranking_rows(rows, sort=sort)[:500]
    localities = list(
        BotUser.objects.exclude(locality="")
        .exclude(locality__isnull=True)
        .values_list("locality", flat=True)
        .distinct()
        .order_by("locality")
    )
    if locality and locality not in localities:
        localities = [locality, *localities]
    bot_status = BotRuntimeStatus.load()
    pending_receipts = PaymentReceipt.objects.filter(status=ReceiptStatus.PENDING).count()
    pending_profiles = BotUser.objects.filter(profile_status=ProfileStatus.PENDING_REVIEW).count()
    pending_service = ServiceReceipt.objects.filter(status=ReceiptStatus.PENDING).count()
    from database.models import AdminTask, AdminTaskStatus

    open_admin_tasks = AdminTask.objects.filter(status=AdminTaskStatus.OPEN).count()
    return render(
        request,
        "panel/users.html",
        {
            "rows": rows,
            "q": q,
            "locality": locality,
            "sort": sort,
            "localities": localities,
            "bot_status": bot_status,
            "pending_receipts": pending_receipts,
            "pending_profiles": pending_profiles,
            "pending_service": pending_service,
            "open_admin_tasks": open_admin_tasks,
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
    from bot.registration import (
        begin_incomplete_profile_flow,
        missing_profile_fields,
    )
    from database.models import PendingAction

    bot_user = get_object_or_404(BotUser, pk=user_id)
    action = request.POST.get("action")
    note = request.POST.get("note", "").strip()
    # Always apply form edits first
    bot_user.locality = request.POST.get("locality", bot_user.locality).strip()
    bot_user.real_name = request.POST.get("real_name", bot_user.real_name).strip()
    bot_user.phone = request.POST.get("phone", bot_user.phone).strip()
    bot_user.address = request.POST.get("address", bot_user.address).strip()
    bot_user.profile_admin_note = note

    if action == "save":
        missing = missing_profile_fields(bot_user)
        if not missing and bot_user.profile_status in {
            ProfileStatus.INCOMPLETE,
            ProfileStatus.REJECTED,
        }:
            bot_user.profile_status = ProfileStatus.PENDING_REVIEW
            bot_user.profile_submitted_at = timezone.now()
        bot_user.save()
        ActivityLog.objects.create(
            user=bot_user,
            kind=ActivityKind.PROFILE_VERIFIED,
            title="Анкета отредактирована администратором",
            detail=note or "Сохранено из панели",
        )
        if missing:
            labels = ", ".join(label for _, label in missing)
            messages.success(
                request,
                f"Данные сохранены. Ещё не заполнены: {labels}.",
            )
        else:
            messages.success(request, "Данные анкеты сохранены.")
        return redirect("panel:user_dashboard", user_id=user_id)
    if action == "verify":
        missing = missing_profile_fields(bot_user)
        if missing:
            labels = ", ".join(label for _, label in missing)
            messages.error(
                request,
                f"Нельзя подтвердить: не заполнены поля — {labels}. "
                "Дозаполните сами («Сохранить данные») или нажмите «Данных не хватает».",
            )
            bot_user.save()
            return redirect("panel:user_dashboard", user_id=user_id)
        bot_user.profile_status = ProfileStatus.VERIFIED
        bot_user.profile_verified_at = timezone.now()
        ActivityLog.objects.create(
            user=bot_user,
            kind=ActivityKind.PROFILE_VERIFIED,
            title="Анкета проверена",
            detail=note or "OK",
        )
        _notify_user(bot_user, "Администратор проверил ваши данные. Анкета принята.")
        messages.success(request, "Анкета подтверждена.")
    elif action == "incomplete":
        missing = missing_profile_fields(bot_user)
        if not missing:
            messages.error(
                request,
                "Все основные поля заполнены. Если нужно переспросить — очистите нужные поля и сохраните снова.",
            )
            bot_user.save()
            return redirect("panel:user_dashboard", user_id=user_id)
        bot_user.profile_status = ProfileStatus.INCOMPLETE
        bot_user.save()
        pending, _ = PendingAction.objects.get_or_create(user=bot_user)
        text = begin_incomplete_profile_flow(bot_user, pending, admin_note=note)
        _notify_user(bot_user, text)
        ActivityLog.objects.create(
            user=bot_user,
            kind=ActivityKind.PROFILE_VERIFIED,
            title="Запрошено дозаполнение анкеты",
            detail="; ".join(label for _, label in missing),
        )
        labels = ", ".join(label for _, label in missing)
        messages.success(
            request,
            f"Пользователю отправлено: данных не хватает ({labels}).",
        )
        return redirect("panel:user_dashboard", user_id=user_id)
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
    try:
        from panel.admin_tasks import task_profile_review

        task_profile_review(bot_user)
    except Exception:
        pass
    return redirect("panel:user_dashboard", user_id=user_id)


@login_required
def user_dashboard(request: HttpRequest, user_id: int) -> HttpResponse:
    from bot.registration import missing_profile_fields
    from services.address_overlap import heuristic_candidates

    bot_user = get_object_or_404(
        BotUser.objects.select_related("family_payer"),
        pk=user_id,
    )
    since = timezone.now() - timedelta(days=14)
    activity_qs = (
        ActivityLog.objects.filter(user=bot_user, created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    missing_fields = missing_profile_fields(bot_user)
    family_candidates = heuristic_candidates(bot_user)
    family_dependents = list(bot_user.family_dependents.all()[:20])
    # Also allow picking any verified/local neighbor from candidates + dependents + payer
    payer_choices = {u.id: u for u in family_candidates}
    if bot_user.family_payer_id and bot_user.family_payer:
        payer_choices[bot_user.family_payer_id] = bot_user.family_payer
    for u in family_dependents:
        payer_choices[u.id] = u
    return render(
        request,
        "panel/user_dashboard.html",
        {
            "bot_user": bot_user,
            "access_state": bot_user.access_state(),
            "subscription_label": bot_user.subscription_label(),
            "effective_subscription_until": bot_user.effective_subscription_until(),
            "subscription_paid_by": bot_user.subscription_paid_by(),
            "family_dependents": family_dependents,
            "family_payer_choices": sorted(payer_choices.values(), key=lambda u: str(u)),
            "missing_fields": missing_fields,
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
@require_POST
def user_family_link(request: HttpRequest, user_id: int) -> HttpResponse:
    from subscriptions.family import link_family_members, unlink_family_member

    bot_user = get_object_or_404(BotUser, pk=user_id)
    action = (request.POST.get("action") or "link").strip()
    if action == "unlink":
        unlink_family_member(bot_user)
        messages.success(request, "Семейная привязка подписки снята.")
        return redirect("panel:user_dashboard", user_id=user_id)

    raw_ids = request.POST.getlist("member_ids")
    payer_raw = (request.POST.get("payer_id") or "").strip()
    extra_raw = (request.POST.get("extra_member_id") or "").strip()
    try:
        member_ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
    except ValueError:
        member_ids = []
    if extra_raw.isdigit():
        member_ids.append(int(extra_raw))
    if bot_user.id not in member_ids:
        member_ids.insert(0, bot_user.id)
    payer_id = int(payer_raw) if payer_raw.isdigit() else None
    if payer_id is None and extra_raw.isdigit():
        payer_id = int(extra_raw)
    if len(set(member_ids)) < 2:
        messages.error(
            request,
            "Укажите ещё одного члена семьи: отметьте в списке или введите его id.",
        )
        return redirect("panel:user_dashboard", user_id=user_id)
    payer = link_family_members(
        member_ids,
        payer=payer_id,
        note="Связано вручную из карточки пользователя",
    )
    if payer:
        until = (
            timezone.localtime(payer.subscription_until).strftime("%d.%m.%Y")
            if payer.subscription_until
            else "—"
        )
        messages.success(
            request,
            f"Семья связана. Подписка дублируется через {payer} до {until}.",
        )
    else:
        messages.error(request, "Не удалось связать семью.")
    return redirect("panel:user_dashboard", user_id=user_id)


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
@require_http_methods(["GET", "POST"])
def receipts_list(request: HttpRequest) -> HttpResponse:
    if request.method == "POST" and request.POST.get("action") == "add_yandex_spend":
        raw_amount = (request.POST.get("amount") or "").strip().replace(",", ".")
        raw_date = (request.POST.get("for_date") or "").strip()
        note = (request.POST.get("note") or "").strip()
        try:
            amount = Decimal(raw_amount)
            if amount <= 0:
                raise InvalidOperation("amount")
        except (InvalidOperation, ValueError):
            messages.error(request, "Укажите сумму расхода Yandex больше 0.")
            return redirect("panel:receipts")
        try:
            for_date = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else timezone.localdate()
        except ValueError:
            messages.error(request, "Некорректная дата расхода.")
            return redirect("panel:receipts")
        YandexBillingEntry.objects.create(for_date=for_date, amount_rub=amount, note=note)
        messages.success(request, f"Учтён расход Yandex: {amount} ₽ за {for_date.strftime('%d.%m.%Y')}.")
        return redirect("panel:receipts")

    status = request.GET.get("status", "").strip()
    find_dupes = request.GET.get("find_dupes", "").strip() in {"1", "true", "yes"}
    qs = PaymentReceipt.objects.select_related("user").all()
    if status:
        qs = qs.filter(status=status)
    items = list(qs[:300])
    dupe_labels: dict[int, str] = {}
    dupe_groups: list[dict] = []
    dupe_ids: set[int] = set()
    if find_dupes:
        from subscriptions.duplicates import duplicate_labels_for_items

        # Global scan backfills SHA-256 for old receipts and finds all twins
        dupe_labels, dupe_groups = duplicate_labels_for_items(items, global_scan=True)
        dupe_ids = set(dupe_labels.keys())
        # Refresh items so content_hash written during backfill is visible
        id_order = [i.id for i in items]
        refreshed = {
            r.id: r
            for r in PaymentReceipt.objects.select_related("user").filter(id__in=id_order)
        }
        items = [refreshed[i] for i in id_order if i in refreshed]
    # Attach label for template convenience (also mirrored in dupe_labels dict)
    for item in items:
        item.dupe_label = dupe_labels.get(item.id, "")
        item.is_dupe = item.id in dupe_ids
    finance = build_finance_snapshot()
    return render(
        request,
        "panel/receipts.html",
        {
            "items": items,
            "status": status,
            "statuses": ReceiptStatus.choices,
            "pending_count": finance["pending_count"],
            "finance": finance,
            "today": timezone.localdate().isoformat(),
            "find_dupes": find_dupes,
            "dupe_groups": dupe_groups,
            "dupe_labels": dupe_labels,
            "dupe_ids": dupe_ids,
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
    force_duplicate = request.POST.get("force_duplicate") in {"1", "on", "true", "yes"}
    raw_amount = (request.POST.get("amount") or "").strip().replace(",", ".")
    try:
        amount = Decimal(raw_amount) if raw_amount else None
    except (InvalidOperation, ValueError):
        amount = None
        messages.error(request, "Некорректная сумма. Укажите число, например 100.")
        next_url = request.POST.get("next") or "panel:receipts"
        if isinstance(next_url, str) and next_url.startswith("/"):
            return redirect(next_url)
        return redirect("panel:receipts")
    try:
        approve_receipt(
            receipt,
            comment=comment,
            amount=amount,
            force_duplicate=force_duplicate,
        )
        receipt.refresh_from_db()
        receipt.user.refresh_from_db()
        _notify_user(receipt.user, approved_user_message(receipt))
        messages.success(
            request,
            f"Чек #{pk} принят: {receipt.amount} ₽ → +{receipt.period_label()}.",
        )
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
@require_POST
def receipt_delete(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(PaymentReceipt, pk=pk)
    reason = request.POST.get("reason", "").strip()
    next_url = request.POST.get("next") or "panel:receipts"
    try:
        user = delete_receipt(receipt, reason=reason)
        user.refresh_from_db()
        _notify_user(
            user,
            deleted_user_message(
                reason=reason,
                subscription_until=user.subscription_until,
            ),
        )
        until = (
            timezone.localtime(user.subscription_until).strftime("%d.%m.%Y")
            if user.subscription_until
            else "нет"
        )
        messages.success(
            request,
            f"Чек #{pk} удалён. Подписка {user} пересчитана до: {until}. Пользователь уведомлён.",
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    if isinstance(next_url, str) and next_url.startswith("/"):
        return redirect(next_url)
    return redirect("panel:receipts")


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
        # service_tax_collected is auto-synced from approved receipts — ignore manual POST
        for field, default in (
            ("yandex_llm_rub_per_1k", "0.40"),
            ("yandex_stt_rub_per_request", "0.15"),
            ("yandex_ocr_rub_per_page", "0.10"),
        ):
            raw = request.POST.get(field)
            if raw in (None, ""):
                continue
            try:
                setattr(cfg, field, Decimal(str(raw).replace(",", ".")))
            except (InvalidOperation, ValueError):
                setattr(cfg, field, Decimal(default))
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
    from services.tax import sync_self_employed_tax_collected

    tax_breakdown = sync_self_employed_tax_collected(cfg)
    cfg.refresh_from_db()
    return render(
        request,
        "panel/settings.html",
        {
            "cfg": cfg,
            "model_warning": _model_warning(cfg.yandex_model),
            "tax_warning": cfg.tax_limit_warning(),
            "tax_breakdown": tax_breakdown,
            "tax_year": tax_breakdown["year"],
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
            event_raw = (request.POST.get("event_at") or "").strip()
            event_at = None
            if event_raw:
                # datetime-local: YYYY-MM-DDTHH:MM
                normalized = event_raw.replace(" ", "T")
                if len(normalized) == 16:
                    normalized += ":00"
                dt = parse_datetime(normalized)
                if dt is None:
                    try:
                        dt = datetime.fromisoformat(normalized)
                    except ValueError:
                        dt = None
                if dt is not None:
                    if timezone.is_naive(dt):
                        dt = timezone.make_aware(dt, timezone.get_current_timezone())
                    event_at = dt
            photo_uploads: list[tuple[bytes, str]] = []
            for f in request.FILES.getlist("offer_photos")[:2]:
                photo_uploads.append((f.read(), f.name))
            token_cache: dict = {}

            def send_media(user, text, images, _cache=token_cache):
                _notify_user_with_images(user, text, images, _token_cache=_cache)

            try:
                campaign, sent = launch_campaign_to_group(
                    category=category,
                    title=request.POST.get("title", "").strip()
                    or dict(ServiceCategory.choices).get(category, "Сбор"),
                    description=request.POST.get("description", "").strip(),
                    group=group,
                    total_amount=total,
                    amount_per_user=per_user,
                    event_at=event_at,
                    photo_uploads=photo_uploads,
                    send_fn=_notify_user,
                    send_media_fn=send_media,
                )
                photo_n = campaign.offer_photos.count()
                extra = f", с фото ({photo_n})" if photo_n else ""
                messages.success(
                    request,
                    f"Сбор запущен для группы «{group.name}»: разослано {sent} сообщ.{extra}",
                )
                tax_warn = AppSettings.load().tax_limit_warning()
                if tax_warn:
                    messages.warning(request, tax_warn)
                return redirect("panel:service_campaign_detail", pk=campaign.id)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("panel:services")
        elif action == "delete_campaign":
            campaign = get_object_or_404(ServiceCampaign, pk=request.POST.get("campaign_id"))
            reason = (request.POST.get("reason") or "").strip()
            if not reason:
                messages.error(request, "Укажите причину удаления сбора.")
                return redirect("panel:services")
            label = delete_service_campaign(campaign, reason=reason)
            messages.success(request, f"Сбор удалён: {label}. Причина: {reason}")
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
    groups = list(
        ServiceGroup.objects.prefetch_related("members")
        .annotate(wish_count=Count("wishes"))
        .all()
    )
    from services.service import budgets_by_group_ids
    from services.tax import sync_self_employed_tax_collected
    from services.wishes import aggregate_home_stats, topic_stats

    budget_map = budgets_by_group_ids([g.id for g in groups])
    for g in groups:
        g.budget = budget_map.get(g.id) or Decimal("0")
    recent = (
        ServiceCampaign.objects.select_related("group")
        .prefetch_related("invites")
        .all()[:20]
    )

    tax_stats = sync_self_employed_tax_collected(cfg)
    cfg.refresh_from_db()
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
            "tax_stats": tax_stats,
            "pending_service": ServiceReceipt.objects.filter(
                status=ReceiptStatus.PENDING
            ).count(),
            "wish_overview": aggregate_home_stats(),
            "wish_topics_all": topic_stats(),
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
        group_notices = 0
        campaign_notices = 0
        if new_ids:
            group_notices = notify_members_added_to_group(
                group, new_ids, send_fn=_notify_user
            )
            token_cache: dict = {}

            def send_media(user, text, images, _cache=token_cache):
                _notify_user_with_images(user, text, images, _token_cache=_cache)

            campaign_notices = invite_new_members_to_group_campaigns(
                group,
                new_ids,
                send_fn=_notify_user,
                send_media_fn=send_media,
            )
        parts = ["Группа сохранена"]
        if group_notices:
            parts.append(f"уведомлений о группе: {group_notices}")
        if campaign_notices:
            parts.append(f"отправленных сборов: {campaign_notices}")
        messages.success(request, ". ".join(parts) + ".")
        return redirect("panel:service_group_edit", pk=pk)

    member_ids = set(group.members.values_list("id", flat=True))
    users = BotUser.objects.all().order_by("real_name", "display_name")
    from database.models import NeighborhoodWish
    from services.service import group_accumulated_budget
    from services.wishes import topic_stats

    wish_stats = topic_stats(group)
    recent_wishes = (
        NeighborhoodWish.objects.filter(group=group)
        .select_related("user")
        .order_by("-created_at")[:40]
    )
    return render(
        request,
        "panel/service_group_edit.html",
        {
            "group": group,
            "users": users,
            "member_ids": member_ids,
            "group_budget": group_accumulated_budget(group),
            "wish_stats": wish_stats,
            "wish_total": sum(s["count"] for s in wish_stats),
            "recent_wishes": recent_wishes,
            "wish_chart_labels_json": json.dumps(
                [s["label"] for s in wish_stats], ensure_ascii=False
            ),
            "wish_chart_values_json": json.dumps([s["count"] for s in wish_stats]),
        },
    )


@login_required
def services_category(request: HttpRequest, category: str) -> HttpResponse:
    if category not in ServiceCategory.values:
        messages.error(request, "Неизвестная категория")
        return redirect("panel:services")
    label = dict(ServiceCategory.choices)[category]
    campaigns = (
        ServiceCampaign.objects.filter(category=category)
        .select_related("group")
        .prefetch_related("invites")
    )
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
        if action == "resend":
            if campaign.work_stage != WorkStage.COLLECTING:
                messages.error(request, "Повторная рассылка доступна только на этапе сбора денег")
            elif campaign.status == CampaignStatus.CLOSED:
                messages.error(request, "Сбор денег уже закрыт")
            elif (campaign.amount_per_user or 0) <= 0:
                messages.error(request, "Нет суммы для рассылки")
            else:
                token_cache: dict = {}

                def send_media(user, text, images, _cache=token_cache):
                    _notify_user_with_images(user, text, images, _token_cache=_cache)

                sent = resend_to_unpaid(
                    campaign,
                    send_fn=_notify_user,
                    send_media_fn=send_media,
                )
                if sent:
                    messages.success(
                        request,
                        f"Напоминание отправлено неоплатившим: {sent} сообщ.",
                    )
                else:
                    messages.info(
                        request,
                        "Некому напоминать — все участники уже оплатили или приглашений нет.",
                    )
            return redirect("panel:service_campaign_detail", pk=pk)
        if action == "advance_stage":
            photo_uploads: list[tuple[bytes, str]] = []
            next_stage = WORK_STAGE_NEXT.get(campaign.work_stage or WorkStage.COLLECTING)
            if next_stage == WorkStage.WORK_DONE:
                for f in request.FILES.getlist("result_photos")[:2]:
                    photo_uploads.append((f.read(), f.name))
            token_cache: dict = {}

            def send_media(user, text, images, _cache=token_cache):
                _notify_user_with_images(user, text, images, _token_cache=_cache)

            try:
                new_stage = advance_work_stage(
                    campaign,
                    photo_uploads=photo_uploads,
                    send_fn=_notify_user,
                    send_media_fn=send_media,
                )
                labels = dict(WorkStage.choices)
                messages.success(request, f"Этап: {labels.get(new_stage, new_stage)}")
                if new_stage == WorkStage.WORK_DONE:
                    n_photos = campaign.result_photos.count()
                    if n_photos:
                        messages.info(
                            request,
                            f"Результат разослан участникам с фото ({n_photos}).",
                        )
                    else:
                        messages.info(request, "Результат разослан участникам без фото.")
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("panel:service_campaign_detail", pk=pk)
        if action == "delete_campaign":
            reason = (request.POST.get("reason") or "").strip()
            if not reason:
                messages.error(request, "Укажите причину удаления сбора.")
                return redirect("panel:service_campaign_detail", pk=pk)
            label = delete_service_campaign(campaign, reason=reason)
            messages.success(request, f"Сбор удалён: {label}. Причина: {reason}")
            return redirect("panel:services")

    from django.db.models import Sum

    paid = campaign.invites.aggregate(s=Sum("amount_paid"))["s"] or Decimal("0")
    total = Decimal(campaign.total_amount or 0)
    pct = min(100, int(paid * 100 / total)) if total > 0 else 0
    surplus = paid - total if paid > total else Decimal("0")
    stage = campaign.work_stage or WorkStage.COLLECTING
    next_stage = WORK_STAGE_NEXT.get(stage)
    stage_labels = dict(WorkStage.choices)
    return render(
        request,
        "panel/service_campaign_detail.html",
        {
            "campaign": campaign,
            "invites": campaign.invites.select_related("user").all(),
            "receipts": campaign.receipts.select_related("user", "invite").all()[:200],
            "offer_photos": campaign.offer_photos.all(),
            "result_photos": campaign.result_photos.all(),
            "collected": paid,
            "progress_pct": pct,
            "surplus": surplus,
            "tax_warning": AppSettings.load().tax_limit_warning(),
            "cfg": AppSettings.load(),
            "work_stages": WORK_STAGE_ORDER,
            "work_stage_labels": stage_labels,
            "current_work_stage": stage,
            "next_work_stage": next_stage,
            "next_work_stage_label": stage_labels.get(next_stage or "", ""),
            "needs_result_photos": next_stage == WorkStage.WORK_DONE,
        },
    )


@login_required
@require_POST
def service_receipt_approve(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(ServiceReceipt, pk=pk)
    comment = request.POST.get("comment", "").strip()
    try:
        approve_service_receipt(receipt, comment=comment, send_fn=_notify_user)
        receipt.refresh_from_db()
        receipt.invite.refresh_from_db()
        receipt.campaign.refresh_from_db()
        _notify_user(receipt.user, approved_service_message(receipt))
        messages.success(request, f"Сервис-чек #{pk} принят.")
        if receipt.campaign.status == CampaignStatus.CLOSED:
            messages.info(request, "Цель сбора достигнута — рассылка «Сбор закрыт.»")
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
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "-rating").strip() or "-rating"
    rows = sort_ranking_rows(ranking_list(locality=locality, q=q), sort=sort)
    # Distinct localities already stored on users — no external suggest/Yandex.
    localities = list(
        BotUser.objects.exclude(locality="")
        .exclude(locality__isnull=True)
        .values_list("locality", flat=True)
        .distinct()
        .order_by("locality")
    )
    if locality and locality not in localities:
        # Keep current filter visible even if spelling no longer matches any user.
        localities = [locality, *localities]
    return render(
        request,
        "panel/services_ranking.html",
        {
            "rows": rows,
            "locality": locality,
            "localities": localities,
            "q": q,
            "sort": sort,
        },
    )


@login_required
def clients_map(request: HttpRequest) -> HttpResponse:
    from services.clients_map import build_clients_map

    graphs = build_clients_map()
    return render(
        request,
        "panel/clients_map.html",
        {
            "graphs": graphs,
            "graph_count": len(graphs),
            "household_total": sum(g["household_count"] for g in graphs),
        },
    )


@login_required
def admin_tasks_today(request: HttpRequest) -> HttpResponse:
    from database.models import AdminTaskKind
    from panel.admin_tasks import build_task_sections, sync_admin_tasks

    sync_admin_tasks()
    sections, total, fingerprint = build_task_sections()
    return render(
        request,
        "panel/admin_tasks_today.html",
        {
            "sections": sections,
            "total": total,
            "fingerprint": fingerprint,
            "kind_choices": AdminTaskKind.choices,
            "feed_url": "/panel/tasks/today/feed/",
        },
    )


@login_required
def admin_tasks_feed(request: HttpRequest) -> JsonResponse:
    """Lightweight live feed for «Задачи на сегодня» auto-refresh."""
    from django.template.loader import render_to_string

    from panel.admin_tasks import build_task_sections, sync_admin_tasks

    # Keep inbox in sync with pending receipts/profiles while the page is open.
    sync_admin_tasks()
    sections, total, fingerprint = build_task_sections()
    client_fp = (request.GET.get("fp") or "").strip()
    if client_fp and client_fp == fingerprint:
        return JsonResponse(
            {"changed": False, "fingerprint": fingerprint, "total": total}
        )
    html = render_to_string(
        "panel/partials/admin_tasks_list.html",
        {"sections": sections, "total": total},
        request=request,
    )
    return JsonResponse(
        {
            "changed": True,
            "fingerprint": fingerprint,
            "total": total,
            "html": html,
        }
    )


@login_required
@require_POST
def admin_task_done(request: HttpRequest, pk: int) -> HttpResponse:
    from database.models import AdminTask, AdminTaskKind
    from subscriptions.family import confirm_family_from_task

    task = get_object_or_404(AdminTask, pk=pk)
    family_note = ""
    if task.kind == AdminTaskKind.FAMILY_CLAIM:
        try:
            payer = confirm_family_from_task(task)
            if payer:
                family_note = (
                    f" Семья объединена: подписка через {payer}"
                    + (
                        f" до {timezone.localtime(payer.subscription_until).strftime('%d.%m.%Y')}."
                        if payer.subscription_until
                        else "."
                    )
                )
            elif (task.meta or {}).get("claimed_family"):
                family_note = " Семейная заявка закрыта, но связать некого (нет кандидатов)."
        except Exception:
            logger.exception("Family confirm failed for task #%s", pk)
            messages.error(request, "Не удалось объединить семью — проверьте карточку пользователя.")
    task.mark_done()
    messages.success(request, f"Задача «{task.title}» выполнена.{family_note}")
    return redirect("panel:admin_tasks_today")


@login_required
@require_POST
def admin_task_dismiss(request: HttpRequest, pk: int) -> HttpResponse:
    from database.models import AdminTask

    task = get_object_or_404(AdminTask, pk=pk)
    task.dismiss()
    messages.success(request, f"Задача «{task.title}» скрыта.")
    return redirect("panel:admin_tasks_today")


@login_required
@require_POST
def admin_address_scan(request: HttpRequest) -> HttpResponse:
    from services.address_overlap import scan_all_addresses

    use_ai = request.POST.get("use_ai", "1") != "0"
    try:
        groups = scan_all_addresses(use_ai=use_ai)
        if groups:
            messages.success(
                request,
                f"Сканирование адресов: найдено групп совпадений — {len(groups)}. "
                "Смотрите задачи «Совпадение адреса».",
            )
        else:
            messages.info(request, "Сканирование завершено: явных пересечений адресов не найдено.")
    except Exception as exc:
        messages.error(request, f"Ошибка сканирования: {exc}")
    return redirect("panel:admin_tasks_today")
