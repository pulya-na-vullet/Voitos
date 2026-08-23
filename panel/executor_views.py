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
    ExecutorRoleProposal,
    ExecutorRoleProposalStatus,
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
from services.role_proposals import approve_proposal, reject_proposal

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
    open_proposals = list(
        ExecutorRoleProposal.objects.filter(status=ExecutorRoleProposalStatus.OPEN)
        .select_related("user")
        .order_by("-created_at")[:30]
    )
    return render(
        request,
        "panel/executor_roles.html",
        {
            "roles": roles,
            "roles_flags_json": json.dumps(roles_payload, ensure_ascii=False),
            "open_proposals": open_proposals,
        },
    )


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def role_proposal_detail(request: HttpRequest, pk: int) -> HttpResponse:
    proposal = get_object_or_404(
        ExecutorRoleProposal.objects.select_related("user", "created_role", "reviewed_by"),
        pk=pk,
    )
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        note = (request.POST.get("admin_note") or "").strip()
        try:
            if action == "approve":
                # Позволить админу поправить название перед созданием.
                new_name = (request.POST.get("role_name") or "").strip()
                if new_name:
                    proposal.proposed_name = new_name[:128]
                    proposal.save(update_fields=["proposed_name", "updated_at"])
                role = approve_proposal(
                    proposal,
                    admin_user=request.user,
                    admin_note=note,
                    accepts_at_home=bool(request.POST.get("accepts_at_home")),
                    requires_work_photos=bool(request.POST.get("requires_work_photos")),
                )
                messages.success(
                    request,
                    f"Роль «{role.name}» добавлена в каталог.",
                )
            elif action == "reject":
                reject_proposal(proposal, admin_user=request.user, admin_note=note)
                messages.info(request, "Заявка отклонена.")
            else:
                messages.error(request, "Неизвестное действие.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("panel:role_proposal_detail", pk=proposal.id)

    return render(
        request,
        "panel/role_proposal_detail.html",
        {"proposal": proposal},
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
        if action == "reassign":
            from services.work_request_dispatch import admin_reassign_executor

            raw_cid = (request.POST.get("contractor_id") or "").strip()
            contractor_id = int(raw_cid) if raw_cid.isdigit() else None
            try:
                offer = admin_reassign_executor(req, contractor_id=contractor_id)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("panel:work_request_detail", pk=pk)
            log_manager_action(
                request.user,
                action="work_request_reassign",
                title=f"Заявка #{req.id}: повторное назначение",
                detail=(
                    f"contractor_id={contractor_id}"
                    if contractor_id
                    else "автоподбор"
                ),
                meta={
                    "work_request_id": req.id,
                    "contractor_id": contractor_id,
                    "offer_id": offer.id if offer else None,
                },
            )
            if offer:
                messages.success(
                    request,
                    f"Назначение обновлено. Предложение: {offer.contractor}.",
                )
            else:
                messages.info(
                    request,
                    "Назначение сброшено, свободный мастер по автоподбору не найден.",
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
        if action == "send_client_survey":
            from services.work_request_client_survey import start_client_service_survey

            if req.status != WorkRequestStatus.AWAITING_CLIENT:
                req.status = WorkRequestStatus.AWAITING_CLIENT
                req.save(update_fields=["status", "updated_at"])
            ok, detail = start_client_service_survey(req)
            if ok:
                messages.success(request, detail)
            else:
                messages.warning(request, detail)
            log_manager_action(
                request.user,
                action="work_request_client_survey",
                title=f"Заявка #{req.id}: опрос клиента в MAX",
                detail=detail,
                meta={"work_request_id": req.id, "ok": ok},
            )
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
            # Ручной перевод / повторное сохранение в «ждём клиента» → опрос в MAX
            if status == WorkRequestStatus.AWAITING_CLIENT:
                from services.work_request_client_survey import start_client_service_survey

                ok, detail = start_client_service_survey(req)
                if ok:
                    messages.success(request, f"Заявка обновлена. {detail}")
                else:
                    messages.warning(
                        request,
                        f"Статус сохранён. {detail}",
                    )
                log_manager_action(
                    request.user,
                    action="work_request_awaiting_client",
                    title=f"Заявка #{req.id}: ждём подтверждения клиента",
                    detail=detail,
                    meta={
                        "work_request_id": req.id,
                        "prev_status": prev_status,
                        "ok": ok,
                    },
                )
                return redirect("panel:work_request_detail", pk=pk)
            messages.success(request, "Заявка обновлена.")
            return redirect("panel:work_request_detail", pk=pk)
    from services.contractors import max_profile_link
    from services.work_request_dispatch import (
        declined_contractor_ids,
        verified_contractors_for_role,
    )

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

    declined_ids = declined_contractor_ids(req)
    reassign_candidates = []
    for c in verified_contractors_for_role(req.role):
        reassign_candidates.append(
            {
                "id": c.id,
                "label": f"{c} · {(c.locality or getattr(c.user, 'locality', '') or '—')}",
                "declined": c.id in declined_ids,
            }
        )

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
            "reassign_candidates": reassign_candidates,
            "can_reassign": req.status
            not in {WorkRequestStatus.DONE, WorkRequestStatus.CANCELLED},
        },
    )


@login_required
@admin_required
def managers_quality_list(request: HttpRequest) -> HttpResponse:
    from database.models import FeedbackKind, FeedbackTicket, ManagerSurveyPeriod, ManagerSurveyResponse
    from django.db.models import Avg, Count

    managers = (
        PanelProfile.objects.filter(role=PanelRole.MANAGER)
        .select_related("user", "bot_user")
        .order_by("user__username")
    )
    rows = []
    for profile in managers:
        groups = list(profile.user.managed_service_groups.order_by("name"))
        periods = ManagerSurveyPeriod.objects.filter(manager=profile.user)
        latest = periods.order_by("-started_at").first()
        agg = ManagerSurveyResponse.objects.filter(period__manager=profile.user).aggregate(
            avg=Avg("score"), cnt=Count("id")
        )
        low_cnt = ManagerSurveyResponse.objects.filter(
            period__manager=profile.user, score__lte=4
        ).count()
        app_agg = FeedbackTicket.objects.filter(
            manager=profile.user, kind=FeedbackKind.MANAGER
        ).aggregate(avg=Avg("score"), cnt=Count("id"))
        rows.append(
            {
                "profile": profile,
                "groups": groups,
                "latest": latest,
                "avg": round(float(agg["avg"]), 2) if agg["avg"] is not None else None,
                "count": int(agg["cnt"] or 0),
                "low_count": low_cnt,
                "app_avg": (
                    round(float(app_agg["avg"]), 2) if app_agg["avg"] is not None else None
                ),
                "app_count": int(app_agg["cnt"] or 0),
            }
        )
    return render(request, "panel/managers.html", {"rows": rows})


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def managers_quality_detail(request: HttpRequest, user_id: int) -> HttpResponse:
    from database.models import (
        FeedbackKind,
        FeedbackTicket,
        ManagerSurveyAILog,
        ManagerSurveyPeriod,
        ManagerSurveyResponse,
    )
    from services.manager_survey import period_stats, summarize_period_with_ai

    manager = get_object_or_404(User, pk=user_id)
    profile = PanelProfile.objects.filter(user=manager).select_related("bot_user").first()
    if not profile or profile.role != PanelRole.MANAGER:
        messages.error(request, "Это не менеджер.")
        return redirect("panel:managers")

    periods = list(
        ManagerSurveyPeriod.objects.filter(manager=manager)
        .select_related("group")
        .order_by("-started_at")[:24]
    )
    period_id = (request.GET.get("period") or request.POST.get("period_id") or "").strip()
    selected = None
    if period_id.isdigit():
        selected = next((p for p in periods if p.id == int(period_id)), None)
    if selected is None and periods:
        selected = periods[0]

    if request.method == "POST" and selected is not None:
        action = request.POST.get("action")
        if action == "resummarize":
            summarize_period_with_ai(selected)
            messages.success(request, "Саммари ИИ обновлено.")
            return redirect(f"{request.path}?period={selected.id}")

    feedback = []
    stats = {"avg": None, "count": 0, "low_count": 0}
    ai_logs = []
    if selected is not None:
        stats = period_stats(selected)
        feedback = list(
            ManagerSurveyResponse.objects.filter(period=selected, score__lte=4)
            .select_related("user")
            .order_by("score", "-created_at")
        )
        # Также все ответы периода для полной картины
        all_responses = list(
            ManagerSurveyResponse.objects.filter(period=selected)
            .select_related("user")
            .order_by("score", "-created_at")
        )
        ai_logs = list(
            ManagerSurveyAILog.objects.filter(manager=manager, period=selected).order_by(
                "created_at"
            )[:100]
        )
    else:
        all_responses = []

    app_tickets = list(
        FeedbackTicket.objects.filter(manager=manager, kind=FeedbackKind.MANAGER)
        .select_related("user", "group")
        .order_by("-created_at")[:100]
    )
    app_ai_logs = list(
        ManagerSurveyAILog.objects.filter(manager=manager, period__isnull=True)
        .order_by("-created_at")[:50]
    )

    groups = list(manager.managed_service_groups.order_by("name"))
    return render(
        request,
        "panel/managers_detail.html",
        {
            "manager": manager,
            "profile": profile,
            "groups": groups,
            "periods": periods,
            "selected": selected,
            "stats": stats,
            "feedback": feedback,
            "all_responses": all_responses,
            "ai_logs": ai_logs,
            "app_tickets": app_tickets,
            "app_ai_logs": app_ai_logs,
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
