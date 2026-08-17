"""Админ: роли исполнителей, заявки на работу, лог менеджеров."""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from database.models import (
    AdminTaskKind,
    ExecutorRole,
    PanelActionLog,
    PanelProfile,
    PanelRole,
    WorkRequest,
    WorkRequestStatus,
)
from panel.admin_tasks import close_task_for_source
from panel.manager_log import log_manager_action
from panel.roles import (
    admin_or_manager_required,
    admin_required,
    can_access_bot_user,
    is_panel_admin,
    is_panel_manager,
    scoped_bot_user_ids,
)
from services.executor_roles import (
    apply_role_form_fields,
    custom_flags_from_role,
    generate_role_code,
)

User = get_user_model()


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def executor_roles(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            name = (request.POST.get("name") or "").strip()
            if not name:
                messages.error(request, "Укажите название роли.")
            else:
                role = ExecutorRole(
                    code=generate_role_code(),
                    name=name[:128],
                    is_active=True,
                    sort_order=0,
                )
                apply_role_form_fields(role, request.POST)
                role.save()
                messages.success(request, f"Роль «{name}» создана.")
            return redirect("panel:executor_roles")
        if action == "save":
            role = get_object_or_404(ExecutorRole, pk=request.POST.get("role_id"))
            role.name = (request.POST.get("name") or role.name).strip()[:128]
            role.is_active = bool(request.POST.get("is_active"))
            apply_role_form_fields(role, request.POST)
            role.save()
            messages.success(request, f"Роль «{role.name}» сохранена.")
            return redirect("panel:executor_roles")
        if action == "delete":
            role = get_object_or_404(ExecutorRole, pk=request.POST.get("role_id"))
            if role.contractors.exists() or role.work_requests.exists():
                role.is_active = False
                role.save(update_fields=["is_active", "updated_at"])
                messages.info(
                    request,
                    f"Роль «{role.name}» скрыта (есть связанные исполнители/заявки).",
                )
            else:
                name = role.name
                role.delete()
                messages.success(request, f"Роль «{name}» удалена.")
            return redirect("panel:executor_roles")
        return redirect("panel:executor_roles")

    roles = list(ExecutorRole.objects.all().order_by("id"))
    roles_payload = [{"id": r.id, "flags": custom_flags_from_role(r)} for r in roles]
    return render(
        request,
        "panel/executor_roles.html",
        {
            "roles": roles,
            "roles_flags_json": json.dumps(roles_payload, ensure_ascii=False),
        },
    )


@login_required
@admin_or_manager_required
@require_http_methods(["GET", "POST"])
def work_requests_list(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        action = request.POST.get("action")
        req = get_object_or_404(
            WorkRequest.objects.select_related("user", "role"),
            pk=request.POST.get("request_id"),
        )
        if not can_access_bot_user(request.user, req.user):
            messages.error(request, "Нет доступа к этой заявке.")
            return redirect("panel:work_requests")
        if action == "delete":
            rid = req.id
            user_id = req.user_id
            label = f"#{rid} {req.role.name}"
            close_task_for_source(AdminTaskKind.WORK_REQUEST, "WorkRequest", rid)
            close_task_for_source(AdminTaskKind.WORK_COMMISSION, "WorkRequest", rid)
            req.delete()
            log_manager_action(
                request.user,
                action="work_request_delete",
                title=f"Удалена заявка {label}",
                detail=f"user_id={user_id}",
                meta={"work_request_id": rid, "user_id": user_id},
            )
            messages.success(request, f"Заявка {label} удалена.")
            return redirect("panel:work_requests")
        if action == "set_status":
            status = (request.POST.get("status") or "").strip()
            if status in WorkRequestStatus.values:
                if status == WorkRequestStatus.DONE and not is_panel_admin(request.user):
                    messages.error(
                        request,
                        "Перевести заявку в «Выполнена» может только администратор.",
                    )
                    return redirect("panel:work_requests")
                req.status = status
                req.admin_note = (request.POST.get("note") or req.admin_note).strip()
                req.save(update_fields=["status", "admin_note", "updated_at"])
                if status in {WorkRequestStatus.DONE, WorkRequestStatus.CANCELLED}:
                    close_task_for_source(
                        AdminTaskKind.WORK_REQUEST, "WorkRequest", req.id
                    )
                messages.success(request, f"Заявка #{req.id}: {req.get_status_display()}.")
        return redirect("panel:work_requests")

    # По умолчанию — активные: без выполненных и отменённых.
    # ?status=all — все; ?status=<code> — один статус.
    raw = request.GET.get("status")
    qs = WorkRequest.objects.select_related("user", "role").prefetch_related("photos")
    scope = scoped_bot_user_ids(request.user)
    if scope is not None:
        qs = qs.filter(user_id__in=scope)
    if raw is None or raw == "":
        status_filter = "active"
        qs = qs.exclude(
            status__in=[
                WorkRequestStatus.DONE,
                WorkRequestStatus.CANCELLED,
                WorkRequestStatus.DRAFT,
            ]
        )
        status = ""
    elif raw == "all":
        status_filter = "all"
        status = "all"
    elif raw in WorkRequestStatus.values:
        status_filter = raw
        status = raw
        qs = qs.filter(status=raw)
    else:
        status_filter = "active"
        status = ""
        qs = qs.exclude(
            status__in=[
                WorkRequestStatus.DONE,
                WorkRequestStatus.CANCELLED,
                WorkRequestStatus.DRAFT,
            ]
        )
    return render(
        request,
        "panel/work_requests.html",
        {
            "items": qs[:200],
            "status": status,
            "status_filter": status_filter,
            "statuses": WorkRequestStatus.choices,
            "can_delete": is_panel_admin(request.user) or is_panel_manager(request.user),
        },
    )



@login_required
@admin_or_manager_required
def work_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    req = get_object_or_404(
        WorkRequest.objects.select_related(
            "user",
            "role",
            "assigned_contractor",
            "assigned_contractor__user",
            "rating",
            "rating__client",
        ).prefetch_related("photos", "offers__contractor__user"),
        pk=pk,
    )
    if not can_access_bot_user(request.user, req.user):
        messages.error(request, "Нет доступа к этой заявке.")
        return redirect("panel:work_requests")
    if request.method == "POST":
        action = (request.POST.get("action") or "save").strip()
        if action == "delete":
            rid = req.id
            user_id = req.user_id
            label = f"#{rid} {req.role.name}"
            close_task_for_source(AdminTaskKind.WORK_REQUEST, "WorkRequest", rid)
            close_task_for_source(AdminTaskKind.WORK_COMMISSION, "WorkRequest", rid)
            req.delete()
            log_manager_action(
                request.user,
                action="work_request_delete",
                title=f"Удалена заявка {label}",
                detail=f"user_id={user_id}",
                meta={"work_request_id": rid, "user_id": user_id},
            )
            messages.success(request, f"Заявка {label} удалена.")
            return redirect("panel:work_requests")
        if action == "dispatch":
            from services.work_request_dispatch import try_dispatch_request

            # Разрешить повторный поиск: сбросить «нет исполнителя»
            if req.no_executor_notified_at and req.status in {
                WorkRequestStatus.PENDING,
                WorkRequestStatus.OFFERING,
            }:
                req.no_executor_notified_at = None
                req.save(update_fields=["no_executor_notified_at", "updated_at"])
            offer = try_dispatch_request(req)
            if offer:
                messages.success(
                    request,
                    f"Предложение отправлено: {offer.contractor}.",
                )
            else:
                messages.info(
                    request,
                    "Подходящего свободного исполнителя не найдено "
                    "(или уже есть активное предложение).",
                )
            return redirect("panel:work_request_detail", pk=pk)
        if action == "approve_commission":
            if not is_panel_admin(request.user):
                messages.error(
                    request,
                    "Принять комиссию может только администратор.",
                )
                return redirect("panel:work_request_detail", pk=pk)
            from services.work_request_completion import approve_commission

            try:
                approve_commission(req, note=(request.POST.get("note") or "").strip())
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("panel:work_request_detail", pk=pk)
            log_manager_action(
                request.user,
                action="commission_approve",
                title=f"Комиссия принята по заявке #{req.id}",
                detail=(request.POST.get("note") or "").strip(),
                meta={"work_request_id": req.id},
            )
            messages.success(request, f"Комиссия по заявке #{req.id} принята.")
            return redirect("panel:work_request_detail", pk=pk)
        if action == "reject_commission":
            if not is_panel_admin(request.user):
                messages.error(
                    request,
                    "Отклонить комиссию может только администратор.",
                )
                return redirect("panel:work_request_detail", pk=pk)
            from services.work_request_completion import reject_commission

            reject_commission(req, note=(request.POST.get("note") or "").strip())
            messages.info(request, f"Комиссия по заявке #{req.id} отклонена.")
            return redirect("panel:work_request_detail", pk=pk)
        status = (request.POST.get("status") or "").strip()
        if status in WorkRequestStatus.values:
            if status == WorkRequestStatus.DONE and not is_panel_admin(request.user):
                messages.error(
                    request,
                    "Перевести заявку в «Выполнена» может только администратор.",
                )
                return redirect("panel:work_request_detail", pk=pk)
            prev_status = req.status
            req.status = status
            req.admin_note = (request.POST.get("note") or "").strip()
            req.client_locality = (
                request.POST.get("client_locality") or req.client_locality
            ).strip()[:255]
            req.save(
                update_fields=[
                    "status",
                    "admin_note",
                    "client_locality",
                    "updated_at",
                ]
            )
            if status in {WorkRequestStatus.DONE, WorkRequestStatus.CANCELLED}:
                close_task_for_source(AdminTaskKind.WORK_REQUEST, "WorkRequest", req.id)
            # Ручной перевод в «ждём клиента» → сразу опрос в MAX: услуга оказана?
            if (
                status == WorkRequestStatus.AWAITING_CLIENT
                and prev_status != WorkRequestStatus.AWAITING_CLIENT
            ):
                from services.work_request_client_survey import start_client_service_survey

                if start_client_service_survey(req):
                    messages.success(
                        request,
                        "Заявка обновлена. Клиенту в MAX отправлен вопрос: "
                        "оказана ли услуга.",
                    )
                else:
                    messages.warning(
                        request,
                        "Статус сохранён, но сообщение клиенту в MAX не ушло "
                        "(проверьте токен бота / chat_id жителя).",
                    )
                log_manager_action(
                    request.user,
                    action="work_request_awaiting_client",
                    title=f"Заявка #{req.id}: ждём подтверждения клиента",
                    detail="Запущен опрос клиента в MAX",
                    meta={"work_request_id": req.id, "prev_status": prev_status},
                )
                return redirect("panel:work_request_detail", pk=pk)
            messages.success(request, "Заявка обновлена.")
            return redirect("panel:work_request_detail", pk=pk)
    from services.contractors import max_profile_link

    assigned = req.assigned_contractor
    assigned_phone = ""
    assigned_max_link = ""
    if assigned:
        assigned_phone = (
            (assigned.phone or getattr(assigned.user, "phone", "") or "")
        ).strip()
        assigned_max_link = max_profile_link(assigned.user)
    client_phone = (req.user.phone or "").strip()
    client_max_link = max_profile_link(req.user)

    return render(
        request,
        "panel/work_request_detail.html",
        {
            "item": req,
            "statuses": WorkRequestStatus.choices,
            "offers": req.offers.select_related("contractor", "contractor__user").all(),
            "active_offer": req.offers.filter(status="offered").first(),
            "client_phone": client_phone,
            "client_max_link": client_max_link,
            "assigned_phone": assigned_phone,
            "assigned_max_link": assigned_max_link,
            "can_delete": is_panel_admin(request.user) or is_panel_manager(request.user),
            "can_manage_commission": is_panel_admin(request.user),
            "can_mark_done": is_panel_admin(request.user),
        },
    )


@login_required
@admin_required
def manager_logs_list(request: HttpRequest) -> HttpResponse:
    managers = (
        PanelProfile.objects.filter(role=PanelRole.MANAGER)
        .select_related("user", "bot_user")
        .order_by("user__username")
    )
    rows = []
    for profile in managers:
        last = (
            PanelActionLog.objects.filter(actor=profile.user)
            .order_by("-created_at")
            .first()
        )
        count = PanelActionLog.objects.filter(actor=profile.user).count()
        rows.append({"profile": profile, "last": last, "count": count})
    return render(request, "panel/manager_logs.html", {"rows": rows})


@login_required
@admin_required
def manager_log_detail(request: HttpRequest, user_id: int) -> HttpResponse:
    manager = get_object_or_404(User, pk=user_id)
    profile = PanelProfile.objects.filter(user=manager).first()
    if not profile or profile.role != PanelRole.MANAGER:
        if not is_panel_admin(request.user):
            messages.error(request, "Это не менеджер.")
            return redirect("panel:manager_logs")
    logs = PanelActionLog.objects.filter(actor=manager).order_by("-created_at")[:500]
    return render(
        request,
        "panel/manager_log_detail.html",
        {"manager": manager, "profile": profile, "logs": logs},
    )
