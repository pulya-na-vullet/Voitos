"""Админ: роли исполнителей, заявки на работу, лог менеджеров."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from database.models import (
    ExecutorRole,
    PanelActionLog,
    PanelProfile,
    PanelRole,
    WorkRequest,
    WorkRequestStatus,
)
from panel.admin_tasks import close_task_for_source
from database.models import AdminTaskKind
from panel.roles import admin_required, is_panel_admin

User = get_user_model()


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def executor_roles(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            code = (request.POST.get("code") or "").strip().lower().replace(" ", "_")
            name = (request.POST.get("name") or "").strip()
            if not code or not name:
                messages.error(request, "Укажите код и название роли.")
            elif ExecutorRole.objects.filter(code=code).exists():
                messages.error(request, f"Роль с кодом «{code}» уже есть.")
            else:
                ExecutorRole.objects.create(
                    code=code[:64],
                    name=name[:128],
                    requires_qualification_docs=bool(
                        request.POST.get("requires_qualification_docs")
                    ),
                    is_equipment=bool(request.POST.get("is_equipment")),
                    for_snow=bool(request.POST.get("for_snow")),
                    for_road=bool(request.POST.get("for_road")),
                    for_snow_haul=bool(request.POST.get("for_snow_haul")),
                    is_active=True,
                    sort_order=int(request.POST.get("sort_order") or 100),
                )
                messages.success(request, f"Роль «{name}» создана.")
            return redirect("panel:executor_roles")
        if action == "save":
            role = get_object_or_404(ExecutorRole, pk=request.POST.get("role_id"))
            role.name = (request.POST.get("name") or role.name).strip()[:128]
            role.requires_qualification_docs = bool(
                request.POST.get("requires_qualification_docs")
            )
            role.is_equipment = bool(request.POST.get("is_equipment"))
            role.for_snow = bool(request.POST.get("for_snow"))
            role.for_road = bool(request.POST.get("for_road"))
            role.for_snow_haul = bool(request.POST.get("for_snow_haul"))
            role.is_active = bool(request.POST.get("is_active"))
            try:
                role.sort_order = int(request.POST.get("sort_order") or role.sort_order)
            except ValueError:
                pass
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

    roles = ExecutorRole.objects.all().order_by("sort_order", "name")
    return render(request, "panel/executor_roles.html", {"roles": roles})


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def work_requests_list(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        action = request.POST.get("action")
        req = get_object_or_404(WorkRequest, pk=request.POST.get("request_id"))
        if action == "set_status":
            status = (request.POST.get("status") or "").strip()
            if status in WorkRequestStatus.values:
                req.status = status
                req.admin_note = (request.POST.get("note") or req.admin_note).strip()
                req.save(update_fields=["status", "admin_note", "updated_at"])
                if status in {WorkRequestStatus.DONE, WorkRequestStatus.CANCELLED}:
                    close_task_for_source(
                        AdminTaskKind.WORK_REQUEST, "WorkRequest", req.id
                    )
                messages.success(request, f"Заявка #{req.id}: {req.get_status_display()}.")
        return redirect("panel:work_requests")

    status = (request.GET.get("status") or "").strip()
    qs = WorkRequest.objects.select_related("user", "role").prefetch_related("photos")
    if status in WorkRequestStatus.values:
        qs = qs.filter(status=status)
    return render(
        request,
        "panel/work_requests.html",
        {
            "items": qs[:200],
            "status": status,
            "statuses": WorkRequestStatus.choices,
        },
    )


@login_required
@admin_required
def work_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    req = get_object_or_404(
        WorkRequest.objects.select_related("user", "role").prefetch_related("photos"),
        pk=pk,
    )
    if request.method == "POST":
        status = (request.POST.get("status") or "").strip()
        if status in WorkRequestStatus.values:
            req.status = status
            req.admin_note = (request.POST.get("note") or "").strip()
            req.save(update_fields=["status", "admin_note", "updated_at"])
            if status in {WorkRequestStatus.DONE, WorkRequestStatus.CANCELLED}:
                close_task_for_source(AdminTaskKind.WORK_REQUEST, "WorkRequest", req.id)
            messages.success(request, "Заявка обновлена.")
            return redirect("panel:work_request_detail", pk=pk)
    return render(
        request,
        "panel/work_request_detail.html",
        {"item": req, "statuses": WorkRequestStatus.choices},
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
