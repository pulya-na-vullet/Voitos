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
from panel.manager_log import log_manager_action
from panel.security import redirect_after_post, safe_redirect_target
from panel.roles import (
    admin_required,
    assign_group_manager,
    can_access_bot_user,
    can_access_group,
    clear_group_manager,
    filter_tasks_for_user,
    is_panel_admin,
    manager_credentials_max_message,
    manager_group_ids,
    manager_groups_qs,
    panel_home_url_name,
    scoped_bot_user_ids,
    scoped_bot_users_qs,
)
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


def _parse_payout_items_from_request(request: HttpRequest, campaign) -> list[dict]:
    """Parse payout amount + receipt file per accepted assignment from POST."""
    from decimal import Decimal, InvalidOperation

    from services.contractors import accepted_assignments_needing_payout

    items: list[dict] = []
    for assignment in accepted_assignments_needing_payout(campaign):
        aid = assignment.id
        amount_raw = (request.POST.get(f"payout_amount_{aid}") or "").strip()
        upload = request.FILES.get(f"payout_receipt_{aid}")
        if not amount_raw and not upload:
            continue
        try:
            amount = Decimal(amount_raw)
        except (InvalidOperation, ValueError):
            raise ValueError(
                f"Укажите сумму перевода для «{assignment.contractor}»"
            )
        if not upload:
            raise ValueError(
                f"Приложите чек перевода для «{assignment.contractor}»"
            )
        items.append(
            {
                "assignment_id": aid,
                "amount": amount,
                "file_bytes": upload.read(),
                "filename": upload.name,
                "comment": (request.POST.get(f"payout_comment_{aid}") or "").strip(),
            }
        )
    return items


def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect(panel_home_url_name(request.user))
    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            log_manager_action(
                user,
                action="login",
                title="Вход в панель",
            )
            return redirect(panel_home_url_name(user))
        error = "Неверный логин или пароль"
    return render(request, "panel/login.html", {"error": error})


def _require_bot_user_access(request: HttpRequest, bot_user: BotUser) -> HttpResponse | None:
    if can_access_bot_user(request.user, bot_user):
        return None
    messages.error(request, "Нет доступа к этому пользователю.")
    return redirect(panel_home_url_name(request.user))


def _require_group_access(request: HttpRequest, group: ServiceGroup) -> HttpResponse | None:
    if can_access_group(request.user, group):
        return None
    messages.error(request, "Нет доступа к этой группе.")
    return redirect("panel:services_groups")


@login_required
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("panel:login")


def _delete_bot_user(user: BotUser) -> str:
    """Полное удаление пользователя бота и связанных данных (CASCADE)."""
    label = str(user)
    # Снять семейные связи, где он плательщик.
    BotUser.objects.filter(family_payer=user).update(family_payer=None)
    user.delete()
    return label


@login_required
@require_http_methods(["GET", "POST"])
def users_list(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        if not is_panel_admin(request.user):
            messages.error(request, "Удаление пользователей доступно только администратору.")
            return redirect("panel:users")
        action = request.POST.get("action")
        if action == "delete_user":
            user = get_object_or_404(BotUser, pk=request.POST.get("user_id"))
            label = _delete_bot_user(user)
            messages.success(request, f"Пользователь «{label}» удалён.")
            return redirect("panel:users")
        if action == "delete_users_bulk":
            ids = [int(x) for x in request.POST.getlist("user_ids") if str(x).isdigit()]
            deleted = 0
            for user in BotUser.objects.filter(id__in=ids):
                _delete_bot_user(user)
                deleted += 1
            messages.success(request, f"Удалено пользователей: {deleted}.")
            return redirect("panel:users")
        return redirect("panel:users")

    q = request.GET.get("q", "").strip()
    locality = request.GET.get("locality", "").strip()
    sort = request.GET.get("sort", "-rating").strip() or "-rating"
    scope_ids = scoped_bot_user_ids(request.user)
    rows = ranking_list(locality=locality, q=q, user_ids=scope_ids)
    rows = sort_ranking_rows(rows, sort=sort)[:500]
    users_scope = scoped_bot_users_qs(request.user)
    localities = list(
        users_scope.exclude(locality="")
        .exclude(locality__isnull=True)
        .values_list("locality", flat=True)
        .distinct()
        .order_by("locality")
    )
    if locality and locality not in localities:
        localities = [locality, *localities]
    bot_status = BotRuntimeStatus.load()
    pending_receipts = PaymentReceipt.objects.filter(status=ReceiptStatus.PENDING).count()
    pending_profiles = users_scope.filter(profile_status=ProfileStatus.PENDING_REVIEW).count()
    pending_service = ServiceReceipt.objects.filter(status=ReceiptStatus.PENDING).count()
    if scope_ids is not None:
        pending_service = ServiceReceipt.objects.filter(
            status=ReceiptStatus.PENDING, user_id__in=scope_ids
        ).count()
        pending_receipts = 0
    from database.models import AdminTask, AdminTaskStatus

    open_tasks_qs = AdminTask.objects.filter(status=AdminTaskStatus.OPEN)
    open_admin_tasks = filter_tasks_for_user(open_tasks_qs, request.user).count()
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
            "tax_warning": AppSettings.load().tax_limit_warning() if is_panel_admin(request.user) else "",
            "can_delete_users": is_panel_admin(request.user),
            "stats": {
                "users": users_scope.count(),
                "active": sum(1 for u in users_scope if u.access_state() == "active"),
                "receipts": PaymentReceipt.objects.count() if is_panel_admin(request.user) else 0,
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
    denied = _require_bot_user_access(request, bot_user)
    if denied:
        return denied
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
    log_manager_action(
        request,
        action="profile_" + (action or "edit"),
        title=f"Анкета пользователя #{bot_user.id}",
        detail=f"{action}: {bot_user}",
        meta={"bot_user_id": bot_user.id},
    )
    return redirect("panel:user_dashboard", user_id=user_id)


@login_required
@require_http_methods(["GET", "POST"])
def user_dashboard(request: HttpRequest, user_id: int) -> HttpResponse:
    from bot.registration import missing_profile_fields
    from services.address_overlap import heuristic_candidates

    bot_user = get_object_or_404(
        BotUser.objects.select_related("family_payer"),
        pk=user_id,
    )
    denied = _require_bot_user_access(request, bot_user)
    if denied:
        return denied
    if request.method == "POST" and request.POST.get("action") == "delete_user":
        if not is_panel_admin(request.user):
            messages.error(request, "Удаление пользователей доступно только администратору.")
            return redirect("panel:user_dashboard", user_id=user_id)
        label = _delete_bot_user(bot_user)
        messages.success(request, f"Пользователь «{label}» удалён.")
        return redirect("panel:users")

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
    executor_profiles = list(
        bot_user.contractor_profiles.select_related("role").order_by("id")
    )
    executor_profile = executor_profiles[0] if executor_profiles else None
    executor_ratings = None
    executor_role_stats: list[dict] = []
    if executor_profiles:
        from services.work_request_rating import contractor_rating_stats

        for profile in executor_profiles:
            stats = contractor_rating_stats(profile)
            executor_role_stats.append({"profile": profile, "ratings": stats})
        # Сводный блок для совместимости шаблона
        executor_ratings = contractor_rating_stats(executor_profiles[0])
        if len(executor_profiles) > 1:
            # объединить отзывы всех ролей для таблицы
            all_ratings = []
            total_score = 0
            count = 0
            for item in executor_role_stats:
                st = item["ratings"]
                all_ratings.extend(st.get("ratings") or [])
                if st.get("count"):
                    total_score += (st.get("avg") or 0) * st["count"]
                    count += st["count"]
            all_ratings.sort(key=lambda r: r.created_at, reverse=True)
            executor_ratings = {
                "avg": round(total_score / count, 2) if count else None,
                "count": count,
                "ratings": all_ratings[:20],
            }

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
            "executor_profile": executor_profile,
            "executor_profiles": executor_profiles,
            "executor_role_stats": executor_role_stats,
            "executor_ratings": executor_ratings,
            "stats": {
                "messages": ChatMessage.objects.filter(user=bot_user).count(),
                "memories": MemoryItem.objects.filter(user=bot_user).count(),
                "tasks": TaskItem.objects.filter(user=bot_user).count(),
                "reminders": Reminder.objects.filter(user=bot_user, is_done=False).count(),
                "receipts": PaymentReceipt.objects.filter(user=bot_user).count(),
            },
            "recent_messages": ChatMessage.objects.filter(user=bot_user)[:15],
            "recent_logs": ActivityLog.objects.filter(user=bot_user)[:80],
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
    denied = _require_bot_user_access(request, bot_user)
    if denied:
        return denied
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
    # Менеджер не может привязывать жителей вне своих групп
    scope = scoped_bot_user_ids(request.user)
    if scope is not None:
        bad = [i for i in set(member_ids) if i not in scope]
        if payer_id is not None and payer_id not in scope:
            bad.append(payer_id)
        if bad:
            messages.error(request, "Нельзя связывать пользователей вне ваших групп.")
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
    denied = _require_bot_user_access(request, bot_user)
    if denied:
        return denied
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
    denied = _require_bot_user_access(request, bot_user)
    if denied:
        return denied
    return render(
        request,
        "panel/memories.html",
        {"bot_user": bot_user, "items": MemoryItem.objects.filter(user=bot_user)[:500]},
    )


@login_required
def user_tasks(request: HttpRequest, user_id: int) -> HttpResponse:
    bot_user = get_object_or_404(BotUser, pk=user_id)
    denied = _require_bot_user_access(request, bot_user)
    if denied:
        return denied
    return render(
        request,
        "panel/tasks.html",
        {"bot_user": bot_user, "items": TaskItem.objects.filter(user=bot_user)[:500]},
    )


@login_required
def user_reminders(request: HttpRequest, user_id: int) -> HttpResponse:
    bot_user = get_object_or_404(BotUser, pk=user_id)
    denied = _require_bot_user_access(request, bot_user)
    if denied:
        return denied
    return render(
        request,
        "panel/reminders.html",
        {"bot_user": bot_user, "items": Reminder.objects.filter(user=bot_user)[:500]},
    )


@login_required
def user_logs(request: HttpRequest, user_id: int) -> HttpResponse:
    bot_user = get_object_or_404(BotUser, pk=user_id)
    denied = _require_bot_user_access(request, bot_user)
    if denied:
        return denied
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
    denied = _require_bot_user_access(request, bot_user)
    if denied:
        return denied
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
        return redirect_after_post(request, fallback="panel:receipts")
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
    return redirect_after_post(request, fallback="panel:receipts")


@login_required
@require_POST
def receipt_reject(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(PaymentReceipt, pk=pk)
    comment = request.POST.get("comment", "").strip() or "Реквизиты не подтверждены"
    reject_receipt(receipt, comment=comment)
    _notify_user(receipt.user, rejected_user_message(receipt))
    messages.success(request, f"Чек #{pk} отклонён, пользователь уведомлён.")
    return redirect_after_post(request, fallback="panel:receipts")


@login_required
@require_POST
def receipt_delete(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(PaymentReceipt, pk=pk)
    reason = request.POST.get("reason", "").strip()
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
    return redirect_after_post(request, fallback="panel:receipts")


@login_required
@admin_required
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
            cfg.grace_days = int(request.POST.get("grace_days") or 14)
        except ValueError:
            cfg.grace_days = 14
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
    from database.dump import (
        dump_has_data,
        find_best_nonempty_dump,
        is_working_db_empty,
        list_dump_files,
        sqlite_botuser_count,
    )

    dump_rows = []
    for p in list_dump_files():
        n = sqlite_botuser_count(p)
        dump_rows.append(
            {
                "name": p.name,
                "users": n,
                "has_data": n > 0,
                "mtime": p.stat().st_mtime,
                "size_kb": max(1, p.stat().st_size // 1024),
            }
        )
    best = find_best_nonempty_dump()
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
            "db_is_empty": is_working_db_empty(),
            "dump_rows": dump_rows,
            "best_dump_name": best.name if best else "",
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

    item = get_object_or_404(MemoryItem.objects.select_related("user"), pk=pk)
    denied = _require_bot_user_access(request, item.user)
    if denied:
        return denied
    uid = item.user_id
    MemoryService().delete(item.id)
    messages.success(request, "Память удалена")
    return redirect_after_post(request, fallback=f"/panel/users/{uid}/memories/")


@login_required
@require_POST
def delete_task(request: HttpRequest, pk: int) -> HttpResponse:
    from tasks.service import TaskService

    item = get_object_or_404(TaskItem.objects.select_related("user"), pk=pk)
    denied = _require_bot_user_access(request, item.user)
    if denied:
        return denied
    uid = item.user_id
    TaskService().delete(pk)
    messages.success(request, "Задача удалена")
    return redirect_after_post(request, fallback=f"/panel/users/{uid}/tasks/")


@login_required
@require_POST
def delete_reminder(request: HttpRequest, pk: int) -> HttpResponse:
    from reminders.service import ReminderService

    item = get_object_or_404(Reminder.objects.select_related("user"), pk=pk)
    denied = _require_bot_user_access(request, item.user)
    if denied:
        return denied
    uid = item.user_id
    ReminderService().delete(pk)
    messages.success(request, "Напоминание удалено")
    return redirect_after_post(request, fallback=f"/panel/users/{uid}/reminders/")


@login_required
@require_POST
def delete_message(request: HttpRequest, pk: int) -> HttpResponse:
    item = get_object_or_404(ChatMessage.objects.select_related("user"), pk=pk)
    denied = _require_bot_user_access(request, item.user)
    if denied:
        return denied
    uid = item.user_id
    item.delete()
    messages.success(request, "Сообщение удалено")
    return redirect_after_post(request, fallback=f"/panel/users/{uid}/messages/")


@login_required
@admin_required
@require_POST
def dump_now(request: HttpRequest) -> HttpResponse:
    from database.dump import create_db_dump, is_working_db_empty

    if is_working_db_empty():
        messages.warning(
            request,
            "БД пустая — дамп не создан, чтобы не затереть хороший бэкап в data/dumps/.",
        )
        return redirect("panel:users")
    path = create_db_dump()
    if path:
        messages.success(request, f"Дамп создан: {path.name}")
    else:
        messages.warning(request, "Дамп не создан.")
    return redirect("panel:users")


@login_required
@admin_required
@require_POST
def restore_dump(request: HttpRequest) -> HttpResponse:
    """Восстановить БД из выбранного или лучшего непустого дампа."""
    from database.dump import (
        find_best_nonempty_dump,
        list_dump_files,
        restore_db_from_dump,
        sqlite_botuser_count,
    )

    name = (request.POST.get("dump_name") or "").strip()
    dumps = {p.name: p for p in list_dump_files()}
    if name and name in dumps:
        dump = dumps[name]
    else:
        dump = find_best_nonempty_dump()
    if not dump:
        messages.error(
            request,
            "Нет непустого дампа в data/dumps/. "
            "Если файлы voitos_*.sqlite3 остались на диске — положите их в data/dumps/ и повторите.",
        )
        return redirect("panel:settings")
    try:
        restore_db_from_dump(dump)
    except Exception as exc:
        messages.error(request, f"Не удалось восстановить: {exc}")
        return redirect("panel:settings")
    n = sqlite_botuser_count(dump)
    messages.success(
        request,
        f"БД восстановлена из {dump.name} ({n} жителей). "
        "Перезапустите python app.py, чтобы воркеры подхватили файл.",
    )
    return redirect("panel:settings")


# Backward-compatible aliases
@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    return redirect(panel_home_url_name(request.user))


@login_required
@require_http_methods(["GET", "POST"])
def services_home(request: HttpRequest) -> HttpResponse:
    cfg = AppSettings.load()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "launch":
            group_id = request.POST.get("group_id")
            group = get_object_or_404(ServiceGroup, pk=group_id)
            denied = _require_group_access(request, group)
            if denied:
                return denied
            category = request.POST.get("category", "").strip()
            try:
                total = Decimal(request.POST.get("total_amount") or "0")
                per_user = Decimal(request.POST.get("amount_per_user") or "0")
            except (InvalidOperation, ValueError):
                total, per_user = Decimal("0"), Decimal("0")
            member_count = group.members.count()
            if total > 0 and member_count > 0:
                # Общая сумма ÷ число участников группы (вверх до целых ₽).
                from decimal import ROUND_CEILING

                per_user = (total / Decimal(member_count)).to_integral_value(
                    rounding=ROUND_CEILING
                )
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
                    needs_snow_haul=(
                        category == ServiceCategory.SNOW
                        and bool(request.POST.get("needs_snow_haul"))
                    ),
                    photo_uploads=photo_uploads,
                    send_fn=_notify_user,
                    send_media_fn=send_media,
                )
                ballot_raw = (request.POST.get("wish_ballot_id") or "").strip()
                if ballot_raw.isdigit():
                    from database.models import WishBallot, WishBallotStatus
                    from services.wish_ballot import close_period_after_campaign

                    ballot = WishBallot.objects.filter(
                        pk=int(ballot_raw),
                        group=group,
                        status=WishBallotStatus.WON,
                    ).first()
                    if ballot is not None:
                        close_period_after_campaign(ballot, campaign)
                        messages.info(
                            request,
                            "Период пожеланий закрыт, открыт новый приём идей.",
                        )
                photo_n = campaign.offer_photos.count()
                extra = f", с фото ({photo_n})" if photo_n else ""
                messages.success(
                    request,
                    f"Сбор запущен для группы «{group.name}»: разослано {sent} сообщ.{extra}",
                )
                tax_warn = AppSettings.load().tax_limit_warning()
                if tax_warn and is_panel_admin(request.user):
                    messages.warning(request, tax_warn)
                return redirect("panel:service_campaign_detail", pk=campaign.id)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("panel:services")
        return redirect("panel:services")

    groups = list(manager_groups_qs(request.user).prefetch_related("members"))
    from services.tax import sync_self_employed_tax_collected

    tax_stats = sync_self_employed_tax_collected(cfg) if is_panel_admin(request.user) else {}
    cfg.refresh_from_db()
    pending_qs = ServiceReceipt.objects.filter(status=ReceiptStatus.PENDING)
    scope_ids = scoped_bot_user_ids(request.user)
    if scope_ids is not None:
        pending_qs = pending_qs.filter(user_id__in=scope_ids)

    wish_ballot_id = (request.GET.get("wish_ballot") or "").strip()
    prefill = {
        "wish_ballot_id": "",
        "group_id": (request.GET.get("group_id") or "").strip(),
        "category": (request.GET.get("category") or "").strip(),
        "title": "",
        "description": "",
    }
    if wish_ballot_id.isdigit():
        from database.models import WishBallot, WishBallotStatus

        ballot = (
            WishBallot.objects.select_related("group")
            .filter(pk=int(wish_ballot_id), status=WishBallotStatus.WON)
            .first()
        )
        if ballot is not None:
            from services.wish_ballot import topic_to_category

            prefill["wish_ballot_id"] = str(ballot.id)
            prefill["group_id"] = str(ballot.group_id)
            prefill["category"] = topic_to_category(ballot.winner_topic)
            prefill["title"] = ballot.winner_label or ballot.winner_topic
            prefill["description"] = (ballot.winner_summary or "")[:400]

    return render(
        request,
        "panel/services_home.html",
        {
            "groups": groups,
            "category_choices": ServiceCategory.choices,
            "tax_warning": cfg.tax_limit_warning() if is_panel_admin(request.user) else "",
            "cfg": cfg,
            "tax_stats": tax_stats,
            "pending_service": pending_qs.count(),
            "prefill": prefill,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def services_groups(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        if not is_panel_admin(request.user):
            messages.error(request, "Создание и удаление групп доступно только администратору.")
            return redirect("panel:services_groups")
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
                messages.success(
                    request, f"Группа «{group.name}» создана. Добавьте участников."
                )
                return redirect("panel:service_group_edit", pk=group.id)
        if action == "delete_group":
            group = get_object_or_404(ServiceGroup, pk=request.POST.get("group_id"))
            name = group.name
            group.delete()
            messages.success(request, f"Группа «{name}» удалена.")
            return redirect("panel:services_groups")
        return redirect("panel:services_groups")

    groups = list(
        manager_groups_qs(request.user)
        .select_related("manager", "manager__panel_profile", "manager__panel_profile__bot_user")
        .prefetch_related("members")
        .annotate(wish_count=Count("wishes"))
        .all()
    )
    from services.service import budgets_by_group_ids

    budget_map = budgets_by_group_ids([g.id for g in groups])
    for g in groups:
        g.budget = budget_map.get(g.id) or Decimal("0")
    return render(
        request,
        "panel/services_groups.html",
        {
            "groups": groups,
            "can_manage_groups": is_panel_admin(request.user),
        },
    )


@login_required
def services_wishes(request: HttpRequest) -> HttpResponse:
    from database.models import NeighborhoodWish, WishTopic
    from services.wishes import aggregate_home_stats, topic_stats

    groups = list(
        manager_groups_qs(request.user)
        .prefetch_related("members")
        .annotate(wish_count=Count("wishes"))
        .all()
    )
    allowed_ids = {g.id for g in groups}
    wish_overview = [
        row
        for row in aggregate_home_stats()
        if getattr(row.get("group"), "id", None) in allowed_ids
    ]
    if is_panel_admin(request.user):
        wish_topics_all = topic_stats()
    else:
        topic_rows = (
            NeighborhoodWish.objects.filter(group_id__in=allowed_ids)
            .values("topic")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        labels = dict(WishTopic.choices)
        wish_topics_all = [
            {
                "topic": r["topic"],
                "label": labels.get(r["topic"], r["topic"]),
                "count": r["count"],
            }
            for r in topic_rows
        ]
    return render(
        request,
        "panel/services_wishes.html",
        {
            "groups": groups,
            "wish_overview": wish_overview,
            "wish_topics_all": wish_topics_all,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def services_archive(request: HttpRequest) -> HttpResponse:
    allowed_group_ids = manager_group_ids(request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete_campaign":
            campaign = get_object_or_404(ServiceCampaign, pk=request.POST.get("campaign_id"))
            if campaign.group_id and campaign.group_id not in allowed_group_ids:
                messages.error(request, "Нет доступа к этому сбору.")
                return redirect("panel:services_archive")
            reason = (request.POST.get("reason") or "").strip()
            if not reason:
                messages.error(request, "Укажите причину удаления сбора.")
                return redirect("panel:services_archive")
            label = delete_service_campaign(campaign, reason=reason)
            messages.success(request, f"Сбор удалён: {label}. Причина: {reason}")
            return redirect("panel:services_archive")
        return redirect("panel:services_archive")

    campaigns_base = ServiceCampaign.objects.filter(group_id__in=allowed_group_ids)
    categories = []
    for value, label in ServiceCategory.choices:
        qs = campaigns_base.filter(category=value)
        categories.append(
            {
                "value": value,
                "label": label,
                "count": qs.count(),
                "active": qs.filter(status=CampaignStatus.ACTIVE).count(),
                "pending_receipts": ServiceReceipt.objects.filter(
                    campaign__category=value,
                    campaign__group_id__in=allowed_group_ids,
                    status=ReceiptStatus.PENDING,
                ).count(),
            }
        )
    recent = (
        campaigns_base.select_related("group")
        .prefetch_related("invites")
        .all()[:20]
    )
    return render(
        request,
        "panel/services_archive.html",
        {
            "categories": categories,
            "recent_campaigns": recent,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def service_group_edit(request: HttpRequest, pk: int) -> HttpResponse:
    group = get_object_or_404(
        ServiceGroup.objects.select_related(
            "manager", "manager__panel_profile", "manager__panel_profile__bot_user"
        ),
        pk=pk,
    )
    denied = _require_group_access(request, group)
    if denied:
        return denied
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete":
            if not is_panel_admin(request.user):
                messages.error(request, "Удаление группы доступно только администратору.")
                return redirect("panel:service_group_edit", pk=pk)
            name = group.name
            group.delete()
            messages.success(request, f"Группа «{name}» удалена")
            return redirect("panel:services_groups")
        if action == "assign_manager":
            if not is_panel_admin(request.user):
                messages.error(request, "Назначать менеджера может только администратор.")
                return redirect("panel:service_group_edit", pk=pk)
            raw_id = (request.POST.get("manager_bot_user_id") or "").strip()
            if not raw_id.isdigit():
                messages.error(request, "Выберите участника группы.")
                return redirect("panel:service_group_edit", pk=pk)
            bot_user = get_object_or_404(BotUser, pk=int(raw_id))
            password = (request.POST.get("manager_password") or "").strip() or None
            try:
                account, plain = assign_group_manager(
                    group, bot_user, password=password
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("panel:service_group_edit", pk=pk)
            max_text = manager_credentials_max_message(
                username=account.username,
                password=plain,
                group_name=group.name,
            )
            _notify_user(bot_user, max_text)
            messages.success(
                request,
                f"Менеджер группы: {bot_user}. "
                f"Логин: {account.username}. "
                f"Пароль и данные для входа отправлены пользователю в MAX.",
            )
            return redirect("panel:service_group_edit", pk=pk)
        if action == "clear_manager":
            if not is_panel_admin(request.user):
                messages.error(request, "Снимать менеджера может только администратор.")
                return redirect("panel:service_group_edit", pk=pk)
            clear_group_manager(group)
            messages.success(request, "Менеджер группы снят.")
            return redirect("panel:service_group_edit", pk=pk)

        group.name = request.POST.get("name", group.name).strip() or group.name
        group.description = request.POST.get("description", "").strip()
        group.save()
        old_ids = set(group.members.values_list("id", flat=True))
        ids = [int(x) for x in request.POST.getlist("user_ids") if str(x).isdigit()]
        group.members.set(BotUser.objects.filter(id__in=ids))
        # Если менеджер больше не в группе — снять назначение.
        if group.manager_id:
            profile = getattr(group.manager, "panel_profile", None)
            bot_id = getattr(profile, "bot_user_id", None) if profile else None
            if bot_id and bot_id not in ids:
                clear_group_manager(group)
                messages.warning(
                    request,
                    "Менеджер был исключён из состава — назначение снято.",
                )
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
        log_manager_action(
            request,
            action="group_edit",
            title=f"Группа сохранена: {group.name}",
            meta={"group_id": group.id},
        )
        return redirect("panel:service_group_edit", pk=pk)

    member_ids = set(group.members.values_list("id", flat=True))
    # Админ видит всех для набора состава; менеджер — тоже всех (чтобы добавлять жителей).
    users = scoped_bot_users_qs(request.user) if not is_panel_admin(request.user) else BotUser.objects.all().order_by("real_name", "display_name")
    members = list(group.members.all().order_by("real_name", "display_name"))
    from database.models import NeighborhoodWish
    from services.service import group_accumulated_budget
    from services.wishes import topic_stats

    wish_stats = topic_stats(group, open_period_only=True)
    if not wish_stats:
        wish_stats = topic_stats(group)
    recent_wishes = (
        NeighborhoodWish.objects.filter(group=group)
        .select_related("user")
        .order_by("-created_at")[:40]
    )
    from database.models import WishBallot, WishBallotStatus, WishPeriod, WishPeriodStatus
    from services.wish_ballot import vote_summary_table

    open_period = (
        WishPeriod.objects.filter(group=group, status=WishPeriodStatus.OPEN)
        .order_by("-opened_at")
        .first()
    )
    active_ballot = (
        WishBallot.objects.filter(group=group)
        .exclude(status=WishBallotStatus.CANCELLED)
        .order_by("-started_at")
        .first()
    )
    ballot_vote_rows = vote_summary_table(active_ballot) if active_ballot else []
    current_manager_bot_id = None
    manager_login = ""
    if group.manager_id:
        manager_login = group.manager.username
        profile = getattr(group.manager, "panel_profile", None)
        if profile and profile.bot_user_id:
            current_manager_bot_id = profile.bot_user_id
    from database.models import PanelProfile, PanelRole

    panel_role_bot_ids = set(
        PanelProfile.objects.filter(
            bot_user_id__isnull=False,
            role__in=[PanelRole.ADMIN, PanelRole.MANAGER],
        ).values_list("bot_user_id", flat=True)
    )
    admin_bot_ids = set(
        PanelProfile.objects.filter(
            bot_user_id__isnull=False,
            role=PanelRole.ADMIN,
        ).values_list("bot_user_id", flat=True)
    )
    if current_manager_bot_id:
        panel_role_bot_ids.add(current_manager_bot_id)
    return render(
        request,
        "panel/service_group_edit.html",
        {
            "group": group,
            "users": users,
            "members": members,
            "member_ids": member_ids,
            "group_budget": group_accumulated_budget(group),
            "wish_stats": wish_stats,
            "wish_total": sum(s["count"] for s in wish_stats),
            "recent_wishes": recent_wishes,
            "wish_chart_labels_json": json.dumps(
                [s["label"] for s in wish_stats], ensure_ascii=False
            ),
            "wish_chart_values_json": json.dumps([s["count"] for s in wish_stats]),
            "can_assign_manager": is_panel_admin(request.user),
            "can_delete_group": is_panel_admin(request.user),
            "current_manager_bot_id": current_manager_bot_id,
            "manager_login": manager_login,
            "panel_role_bot_ids": panel_role_bot_ids,
            "admin_bot_ids": admin_bot_ids,
            "open_wish_period": open_period,
            "active_wish_ballot": active_ballot,
            "ballot_vote_rows": ballot_vote_rows,
        },
    )


@login_required
def services_category(request: HttpRequest, category: str) -> HttpResponse:
    if category not in ServiceCategory.values:
        messages.error(request, "Неизвестная категория")
        return redirect("panel:services_archive")
    label = dict(ServiceCategory.choices)[category]
    allowed_group_ids = manager_group_ids(request.user)
    campaigns = (
        ServiceCampaign.objects.filter(category=category, group_id__in=allowed_group_ids)
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
            "tax_warning": AppSettings.load().tax_limit_warning()
            if is_panel_admin(request.user)
            else "",
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
    if campaign.group_id:
        denied = _require_group_access(request, campaign.group)
        if denied:
            return denied
    elif not is_panel_admin(request.user):
        messages.error(request, "Нет доступа к этому сбору.")
        return redirect("panel:services_archive")
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
                if next_stage == WorkStage.WORK_CLOSED:
                    from services.contractors import close_campaign_requiring_payouts

                    payout_items = _parse_payout_items_from_request(request, campaign)
                    new_stage = close_campaign_requiring_payouts(
                        campaign,
                        payout_items,
                        send_fn=_notify_user,
                        send_media_fn=send_media,
                    )
                else:
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
                if new_stage == WorkStage.WORK_CLOSED:
                    messages.info(
                        request,
                        "Работа закрыта. Чеки оплаты исполнителям разосланы участникам сбора.",
                    )
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("panel:service_campaign_detail", pk=pk)
        if action == "backfill_payouts":
            from services.contractors import record_contractor_payouts

            token_cache: dict = {}

            def send_media(user, text, images, _cache=token_cache):
                _notify_user_with_images(user, text, images, _token_cache=_cache)

            try:
                items = _parse_payout_items_from_request(request, campaign)
                created = record_contractor_payouts(
                    campaign,
                    items,
                    send_fn=_notify_user,
                    send_media_fn=send_media,
                )
                messages.success(
                    request,
                    f"Дозаполнено оплат: {len(created)}. Чеки разосланы участникам и исполнителям.",
                )
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
        if action == "assign_contractor":
            from database.models import ContractorProfile
            from services.contractors import assign_contractor

            cid = request.POST.get("contractor_id")
            contractor = get_object_or_404(ContractorProfile, pk=cid)
            try:
                assign_contractor(campaign, contractor, send_fn=_notify_user)
                messages.success(
                    request,
                    f"Исполнителю «{contractor}» отправлено предложение.",
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("panel:service_campaign_detail", pk=pk)
        if action == "assign_resident_helper":
            from services.resident_helpers import assign_resident_helper

            uid = request.POST.get("user_id")
            helper_user = get_object_or_404(BotUser, pk=uid)
            try:
                assign_resident_helper(campaign, helper_user, send_fn=_notify_user)
                messages.success(
                    request,
                    f"Житель «{helper_user}» назначен исполнителем.",
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("panel:service_campaign_detail", pk=pk)
        if action == "cancel_resident_helper":
            from database.models import CampaignResidentHelper
            from services.resident_helpers import cancel_resident_helper

            helper = get_object_or_404(
                CampaignResidentHelper,
                pk=request.POST.get("helper_id"),
                campaign=campaign,
            )
            cancel_resident_helper(helper, send_fn=_notify_user)
            messages.success(request, "Исполнитель из группы снят.")
            return redirect("panel:service_campaign_detail", pk=pk)
        if action == "approve_counter":
            from database.models import CampaignAssignment
            from services.contractors import approve_counter_offer

            assignment = get_object_or_404(
                CampaignAssignment, pk=request.POST.get("assignment_id"), campaign=campaign
            )
            try:
                approve_counter_offer(assignment, send_fn=_notify_user)
                messages.success(request, "Время исполнителя подтверждено, жители уведомлены.")
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("panel:service_campaign_detail", pk=pk)
        if action == "reject_counter":
            from database.models import CampaignAssignment
            from services.contractors import reject_counter_offer

            assignment = get_object_or_404(
                CampaignAssignment, pk=request.POST.get("assignment_id"), campaign=campaign
            )
            reject_counter_offer(assignment, send_fn=_notify_user)
            messages.info(
                request,
                "Время отклонено — назначьте другого исполнителя с такой же техникой.",
            )
            return redirect("panel:service_campaign_detail", pk=pk)
        if action == "cancel_assignment":
            from database.models import CampaignAssignment
            from services.contractors import cancel_assignment

            assignment = get_object_or_404(
                CampaignAssignment, pk=request.POST.get("assignment_id"), campaign=campaign
            )
            cancel_assignment(assignment, send_fn=_notify_user)
            messages.success(request, "Назначение отменено.")
            return redirect("panel:service_campaign_detail", pk=pk)
        if action == "notify_residents_contractors":
            from services.contractors import notify_residents_about_assignments

            n = notify_residents_about_assignments(campaign, send_fn=_notify_user)
            messages.success(request, f"Статус исполнителя разослан: {n} сообщ.")
            return redirect("panel:service_campaign_detail", pk=pk)

    from django.db.models import Sum

    from database.models import EquipmentType, ResidentHelperStatus, VolunteerReplyStatus
    from services.contractors import (
        accepted_assignments_needing_payout,
        suggested_equipment_for_campaign,
        verified_contractors,
    )
    from services.resident_helpers import (
        group_members_for_helper_pick,
        uses_resident_helpers,
    )

    paid = campaign.invites.aggregate(s=Sum("amount_paid"))["s"] or Decimal("0")
    total = Decimal(campaign.total_amount or 0)
    pct = min(100, int(paid * 100 / total)) if total > 0 else 0
    surplus = paid - total if paid > total else Decimal("0")
    stage = campaign.work_stage or WorkStage.COLLECTING
    next_stage = WORK_STAGE_NEXT.get(stage)
    stage_labels = dict(WorkStage.choices)
    suggested_types = suggested_equipment_for_campaign(campaign)
    eq_labels = dict(EquipmentType.choices)
    suggested_labels = [eq_labels.get(t, t) for t in suggested_types]
    assignable = []
    for eq in suggested_types:
        assignable.extend(list(verified_contractors(equipment_type=eq)))
    # Also allow any verified if not in suggested (for snow/road).
    if suggested_types:
        seen_ids = {c.id for c in assignable}
        for c in verified_contractors():
            if c.id not in seen_ids:
                assignable.append(c)
    payout_needed = accepted_assignments_needing_payout(campaign)
    use_residents = uses_resident_helpers(campaign)
    yes_helper_ids = set(
        campaign.volunteer_asks.filter(status=VolunteerReplyStatus.YES).values_list(
            "user_id", flat=True
        )
    )
    return render(
        request,
        "panel/service_campaign_detail.html",
        {
            "campaign": campaign,
            "invites": campaign.invites.select_related("user").all(),
            "receipts": campaign.receipts.select_related("user", "invite").all()[:200],
            "offer_photos": campaign.offer_photos.all(),
            "result_photos": campaign.result_photos.all(),
            "assignments": campaign.assignments.select_related(
                "contractor", "contractor__user"
            ).all(),
            "assignable_contractors": assignable,
            "suggested_equipment_types": suggested_labels,
            "uses_resident_helpers": use_residents,
            "resident_helpers": campaign.resident_helpers.filter(
                status=ResidentHelperStatus.ASSIGNED
            ).select_related("user"),
            "assignable_residents": group_members_for_helper_pick(campaign),
            "volunteer_yes_ids": yes_helper_ids,
            "contractor_payouts": campaign.contractor_payouts.select_related(
                "contractor", "contractor__user", "assignment"
            ).all(),
            "payout_needed": payout_needed,
            "needs_contractor_payouts": bool(payout_needed)
            and next_stage == WorkStage.WORK_CLOSED,
            "can_backfill_payouts": bool(payout_needed)
            and stage == WorkStage.WORK_CLOSED,
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
@admin_required
@require_http_methods(["GET", "POST"])
def contractors_list(request: HttpRequest) -> HttpResponse:
    from database.models import ContractorProfile, ContractorStatus, ExecutorRole
    from panel.admin_tasks import close_task_for_source
    from database.models import AdminTaskKind

    if not is_panel_admin(request.user):
        messages.error(request, "Раздел исполнителей доступен только администратору.")
        return redirect(panel_home_url_name(request.user))

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete":
            profile = get_object_or_404(
                ContractorProfile, pk=request.POST.get("contractor_id")
            )
            label = str(profile)
            close_task_for_source(
                AdminTaskKind.CONTRACTOR_REVIEW, "ContractorProfile", profile.id
            )
            profile.delete()
            messages.success(request, f"Исполнитель «{label}» удалён.")
            return redirect("panel:contractors")
        profile = get_object_or_404(ContractorProfile, pk=request.POST.get("contractor_id"))
        if action == "verify":
            profile.status = ContractorStatus.VERIFIED
            profile.verified_at = timezone.now()
            profile.admin_note = (request.POST.get("note") or "").strip()
            profile.save()
            close_task_for_source(
                AdminTaskKind.CONTRACTOR_REVIEW, "ContractorProfile", profile.id
            )
            _notify_user(
                profile.user,
                "Анкета исполнителя проверена. Теперь вы можете получать заказы.",
            )
            try:
                from services.work_request_dispatch import dispatch_for_new_contractor

                n = dispatch_for_new_contractor(profile, send_fn=_notify_user)
                if n:
                    messages.info(
                        request,
                        f"Исполнителю предложено открытых заявок: {n}.",
                    )
            except Exception:
                pass
            messages.success(request, f"Исполнитель «{profile}» подтверждён.")
        elif action == "reject":
            profile.status = ContractorStatus.REJECTED
            profile.admin_note = (request.POST.get("note") or "").strip()
            profile.save()
            close_task_for_source(
                AdminTaskKind.CONTRACTOR_REVIEW, "ContractorProfile", profile.id
            )
            _notify_user(
                profile.user,
                "Анкета исполнителя отклонена."
                + (f"\n{profile.admin_note}" if profile.admin_note else ""),
            )
            messages.info(request, f"Исполнитель «{profile}» отклонён.")
        elif action == "disable":
            profile.status = ContractorStatus.DISABLED
            profile.save(update_fields=["status", "updated_at"])
            messages.info(request, f"Исполнитель «{profile}» отключён.")
        elif action == "update_payout_details":
            from subscriptions.receipts import normalize_phone

            profile.bank_name = (request.POST.get("bank_name") or "").strip()[:255]
            raw_phone = (request.POST.get("payout_phone") or "").strip()
            if raw_phone:
                phone = normalize_phone(raw_phone)
                profile.payout_phone = phone[:32] if len(phone) >= 10 else raw_phone[:32]
            else:
                profile.payout_phone = ""
            profile.save(update_fields=["bank_name", "payout_phone", "updated_at"])
            messages.success(request, f"Реквизиты «{profile}» сохранены.")
        return redirect("panel:contractors")

    from django.db.models import Avg, Count
    from services.contractors import annotate_contractor_total_earned

    eq_filter = (request.GET.get("type") or "").strip()
    qs = annotate_contractor_total_earned(
        ContractorProfile.objects.select_related("user", "role")
    ).annotate(
        rating_avg=Avg("work_ratings__score"),
        rating_count=Count("work_ratings", distinct=True),
    ).order_by("status", "equipment_type", "-submitted_at")
    if eq_filter:
        qs = qs.filter(equipment_type=eq_filter)
    role_choices = list(
        ExecutorRole.objects.filter(is_active=True)
        .order_by("id")
        .values_list("code", "name")
    )
    return render(
        request,
        "panel/contractors.html",
        {
            "contractors": qs,
            "equipment_choices": role_choices,
            "type_filter": eq_filter,
            "status_verified": ContractorStatus.VERIFIED,
            "status_pending": ContractorStatus.PENDING_REVIEW,
        },
    )


@login_required
@require_POST
def service_receipt_approve(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(
        ServiceReceipt.objects.select_related("campaign", "campaign__group", "user"),
        pk=pk,
    )
    if receipt.campaign.group_id:
        denied = _require_group_access(request, receipt.campaign.group)
        if denied:
            return denied
    elif not is_panel_admin(request.user):
        messages.error(request, "Нет доступа.")
        return redirect(panel_home_url_name(request.user))
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
        if tax_warn and is_panel_admin(request.user):
            messages.warning(request, tax_warn)
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect_after_post(request, fallback=f"/panel/services/campaigns/{receipt.campaign_id}/")


@login_required
@require_POST
def service_receipt_reject(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(
        ServiceReceipt.objects.select_related("campaign", "campaign__group", "user"),
        pk=pk,
    )
    if receipt.campaign.group_id:
        denied = _require_group_access(request, receipt.campaign.group)
        if denied:
            return denied
    elif not is_panel_admin(request.user):
        messages.error(request, "Нет доступа.")
        return redirect(panel_home_url_name(request.user))
    comment = request.POST.get("comment", "").strip() or "Реквизиты не подтверждены"
    reject_service_receipt(receipt, comment=comment)
    _notify_user(receipt.user, rejected_service_message(receipt))
    messages.success(request, f"Сервис-чек #{pk} отклонён.")
    return redirect_after_post(request, fallback=f"/panel/services/campaigns/{receipt.campaign_id}/")


@login_required
def services_ranking(request: HttpRequest) -> HttpResponse:
    locality = request.GET.get("locality", "").strip()
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "-rating").strip() or "-rating"
    scope_ids = scoped_bot_user_ids(request.user)
    rows = sort_ranking_rows(
        ranking_list(locality=locality, q=q, user_ids=scope_ids),
        sort=sort,
    )
    users_scope = scoped_bot_users_qs(request.user)
    localities = list(
        users_scope.exclude(locality="")
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

    group_ids = None if is_panel_admin(request.user) else manager_group_ids(request.user)
    graphs = build_clients_map(group_ids=group_ids)
    graphs_payload = [
        {
            "group_id": g["group_id"],
            "group_name": g["group_name"],
            "vertex_count": g["vertex_count"],
            "payer_count": g["payer_count"],
            "user_count": g["user_count"],
            "elements": g["elements"],
        }
        for g in graphs
    ]
    return render(
        request,
        "panel/clients_map.html",
        {
            "graphs": graphs,
            "graphs_payload": graphs_payload,
            "graph_count": len(graphs),
            "vertex_total": sum(g["vertex_count"] for g in graphs),
            "payer_total": sum(g["payer_count"] for g in graphs),
        },
    )


@login_required
@admin_required
def earnings_forecast(request: HttpRequest) -> HttpResponse:
    from subscriptions.forecast import build_earnings_forecast

    forecast = build_earnings_forecast()
    return render(
        request,
        "panel/earnings_forecast.html",
        {"forecast": forecast},
    )


@login_required
def admin_tasks_today(request: HttpRequest) -> HttpResponse:
    from database.models import AdminTaskKind
    from panel.admin_tasks import build_task_sections, sync_admin_tasks

    sync_admin_tasks()
    scope_ids = scoped_bot_user_ids(request.user)
    exclude = None
    if not is_panel_admin(request.user):
        from database.models import AdminTaskKind

        # Оплата подписок — только администратор.
        exclude = {AdminTaskKind.PAYMENT_RECEIPT}
    sections, total, fingerprint = build_task_sections(
        user_ids=scope_ids, exclude_kinds=exclude
    )
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
    scope_ids = scoped_bot_user_ids(request.user)
    exclude = None
    if not is_panel_admin(request.user):
        from database.models import AdminTaskKind

        # Оплата подписок — только администратор.
        exclude = {AdminTaskKind.PAYMENT_RECEIPT}
    sections, total, fingerprint = build_task_sections(
        user_ids=scope_ids, exclude_kinds=exclude
    )
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
    if not is_panel_admin(request.user):
        if not task.user_id or not can_access_bot_user(request.user, task.user):
            messages.error(request, "Нет доступа к этой задаче.")
            return redirect("panel:admin_tasks_today")
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
    log_manager_action(
        request,
        action="task_done",
        title=f"Задача выполнена: {task.title}",
        detail=family_note.strip(),
        meta={"task_id": task.id, "kind": task.kind},
    )
    messages.success(request, f"Задача «{task.title}» выполнена.{family_note}")
    return redirect("panel:admin_tasks_today")


@login_required
@require_POST
def admin_task_dismiss(request: HttpRequest, pk: int) -> HttpResponse:
    from database.models import AdminTask

    task = get_object_or_404(AdminTask, pk=pk)
    if task.user_id and not can_access_bot_user(request.user, task.user):
        messages.error(request, "Нет доступа к этой задаче.")
        return redirect("panel:admin_tasks_today")
    task.dismiss()
    messages.success(request, f"Задача «{task.title}» скрыта.")
    return redirect("panel:admin_tasks_today")


@login_required
@require_POST
def admin_address_scan(request: HttpRequest) -> HttpResponse:
    if not is_panel_admin(request.user):
        messages.error(request, "Сканирование адресов доступно только администратору.")
        return redirect("panel:admin_tasks_today")
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
