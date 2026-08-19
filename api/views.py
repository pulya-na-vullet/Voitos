"""Ресурсные stub/thin handlers для KMP. Домен — существующие сервисы."""

from __future__ import annotations

from django.views.decorators.http import require_GET, require_http_methods

from api.http import api_login_required, api_public, json_response, parse_json
from api.models import AppNotification
from bot.onboarding import STORIES, panel_progress, progress_list
from database.models import (
    AppSettings,
    ExecutorRole,
    PaymentReceipt,
    WorkRequest,
)


@api_public
@require_GET
def health(_request):
    return json_response({"ok": True, "service": "voitos-api-v1"})


def _me_payload(user) -> dict:
    return {
        "id": user.id,
        "real_name": user.real_name or "",
        "phone": user.phone or "",
        "address": user.address or "",
        "locality": user.locality or "",
        "profile_status": user.profile_status,
        "onboarding_completed": bool(
            user.onboarding_reward_granted or user.onboarding_completed_at
        ),
    }


def _access_payload(user) -> dict:
    until = user.effective_subscription_until()
    return {
        "state": user.access_state(),
        "subscription_until": until.isoformat() if until else None,
        "grace_until": user.grace_until.isoformat() if user.grace_until else None,
        "label": user.subscription_label(),
    }


@api_login_required
@require_http_methods(["GET", "PATCH"])
def me(request):
    user = request.bot_user
    if request.method == "PATCH":
        data = parse_json(request)
        for field in ("real_name", "phone", "address", "locality"):
            if field in data and data[field] is not None:
                setattr(user, field, str(data[field]).strip()[:255 if field != "address" else 2000])
        user.save()
    return json_response(_me_payload(user))


@api_login_required
@require_GET
def me_access(request):
    return json_response(_access_payload(request.bot_user))


@api_login_required
@require_GET
def me_subscription(request):
    user = request.bot_user
    cfg = AppSettings.load()
    payload = _access_payload(user)
    payload.update(
        {
            "price_rub": cfg.subscription_price_rub,
            "payment_phone": (cfg.payment_phone or "").strip(),
            "payment_name": (cfg.payment_name or "").strip(),
            "pending_receipts": PaymentReceipt.objects.filter(
                user=user, status="pending"
            ).count(),
        }
    )
    return json_response(payload)


@api_login_required
@require_GET
def executor_roles(request):
    roles = ExecutorRole.objects.filter(is_active=True).order_by("sort_order", "id")
    return json_response(
        {
            "items": [
                {
                    "id": r.id,
                    "code": r.code,
                    "name": r.name,
                    "requires_work_photos": bool(
                        getattr(r, "requires_work_photos", True)
                    ),
                }
                for r in roles
            ]
        }
    )


@api_login_required
@require_http_methods(["GET", "POST"])
def work_requests_list(request):
    if request.method == "POST":
        return work_requests_create(request)
    qs = (
        WorkRequest.objects.filter(user=request.bot_user)
        .select_related("role", "assigned_contractor")
        .order_by("-created_at")[:50]
    )
    return json_response(
        {
            "items": [
                {
                    "id": wr.id,
                    "status": wr.status,
                    "role_name": wr.role.name if wr.role_id else "",
                    "description": wr.description or "",
                    "created_at": wr.created_at.isoformat(),
                    "assigned_executor_name": (
                        str(wr.assigned_contractor)
                        if wr.assigned_contractor_id
                        else None
                    ),
                }
                for wr in qs
            ]
        }
    )


@api_login_required
@require_GET
def work_request_detail(request, pk: int):
    wr = (
        WorkRequest.objects.select_related("role", "assigned_contractor")
        .filter(pk=pk, user=request.bot_user)
        .first()
    )
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    contractor = wr.assigned_contractor
    requires_photos = bool(
        getattr(wr.role, "requires_work_photos", True) if wr.role_id else True
    )
    return json_response(
        {
            "id": wr.id,
            "status": wr.status,
            "role_name": wr.role.name if wr.role_id else "",
            "description": wr.description or "",
            "created_at": wr.created_at.isoformat(),
            "assigned_name": str(contractor) if contractor else None,
            "assigned_phone": getattr(contractor, "phone", None) if contractor else None,
            "proposed_slots": wr.proposed_slots or [],
            "needs_confirm_amount": wr.status == "awaiting_client",
            "needs_rating": False,
            "needs_photos": requires_photos and wr.status == "draft",
            "photo_count": wr.photos.count(),
        }
    )


@api_login_required
@require_GET
def onboarding(request):
    prog = panel_progress(request.bot_user)
    for step in prog["steps"]:
        step["image_url"] = f"/static/{step['image']}"
    if prog.get("completed_at"):
        prog["completed_at"] = prog["completed_at"].isoformat()
    return json_response(prog)


@api_login_required
@require_http_methods(["POST"])
def onboarding_complete_step(request, code: str):
    user = request.bot_user
    code = (code or "").strip()
    if code not in {s.code for s in STORIES}:
        return json_response({"error": "unknown_step"}, status=400)
    done = progress_list(user)
    if code not in done:
        done.append(code)
        user.onboarding_steps = done
        user.save(update_fields=["onboarding_steps", "last_seen_at"])
    if len(progress_list(user)) >= len(STORIES) and not user.onboarding_reward_granted:
        from bot.onboarding import _grant_reward_if_needed
        from database.models import PendingAction

        pending, _ = PendingAction.objects.get_or_create(user=user)
        _grant_reward_if_needed(user)
        pending.clear_pending()
        user.refresh_from_db()
    prog = panel_progress(user)
    for step in prog["steps"]:
        step["image_url"] = f"/static/{step['image']}"
    if prog.get("completed_at"):
        prog["completed_at"] = prog["completed_at"].isoformat()
    return json_response(prog)


@api_login_required
@require_GET
def notifications_list(request):
    qs = AppNotification.objects.filter(bot_user=request.bot_user)
    if request.GET.get("unread") in {"1", "true", "yes"}:
        qs = qs.filter(read_at__isnull=True)
    items = []
    for n in qs[:100]:
        items.append(
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "deep_link": n.deep_link,
                "entity_type": n.entity_type or None,
                "entity_id": n.entity_id,
                "read_at": n.read_at.isoformat() if n.read_at else None,
                "created_at": n.created_at.isoformat(),
            }
        )
    return json_response({"items": items})


@api_login_required
@require_http_methods(["POST"])
def notification_read(request, pk: int):
    n = AppNotification.objects.filter(pk=pk, bot_user=request.bot_user).first()
    if not n:
        return json_response({"error": "not_found"}, status=404)
    n.mark_read()
    return json_response({"ok": True})


@api_login_required
@require_GET
def collections_list(request):
    """Список инвайтов жителя в сборы."""
    try:
        from database.models import ServiceInvite

        invites = (
            ServiceInvite.objects.filter(user=request.bot_user)
            .select_related("campaign")
            .order_by("-id")[:50]
        )
        items = []
        for inv in invites:
            camp = inv.campaign
            items.append(
                {
                    "id": camp.id if camp else inv.id,
                    "title": getattr(camp, "title", None) or "Сбор",
                    "category": getattr(camp, "category", "") or "",
                    "amount_due": float(getattr(inv, "amount_due", 0) or 0),
                    "status": inv.status,
                    "event_at": (
                        camp.event_at.isoformat()
                        if camp and getattr(camp, "event_at", None)
                        else None
                    ),
                }
            )
        return json_response({"items": items})
    except Exception:
        return json_response({"items": []})


@api_login_required
@require_GET
def collection_detail(request, pk: int):
    """Детали сбора по campaign id для текущего пользователя."""
    from database.models import ServiceInvite

    inv = (
        ServiceInvite.objects.filter(user=request.bot_user, campaign_id=pk)
        .select_related("campaign")
        .first()
    )
    if not inv:
        return json_response({"error": "not_found"}, status=404)
    camp = inv.campaign
    paid = int(
        camp.invites.filter(status="paid").count() if camp else 0
    )
    total = int(camp.invites.count() if camp else 0)
    return json_response(
        {
            "id": camp.id,
            "title": camp.title,
            "category": camp.category or "",
            "description": camp.description or "",
            "amount_due": float(inv.amount_due or 0),
            "amount_paid": float(inv.amount_paid or 0),
            "status": inv.status,
            "event_at": camp.event_at.isoformat() if camp.event_at else None,
            "paid_count": paid,
            "invite_count": total,
        }
    )


@api_login_required
@require_http_methods(["POST"])
def work_requests_create(request):
    from database.models import WorkRequestStatus

    data = parse_json(request)
    role_id = data.get("role_id")
    description = (data.get("description") or "").strip()
    if not role_id or len(description) < 5:
        return json_response({"error": "role_and_description_required"}, status=400)
    role = ExecutorRole.objects.filter(pk=role_id, is_active=True).first()
    if not role:
        return json_response({"error": "role_not_found"}, status=404)
    requires_photos = bool(getattr(role, "requires_work_photos", True))
    status = WorkRequestStatus.DRAFT if requires_photos else WorkRequestStatus.PENDING
    wr = WorkRequest.objects.create(
        user=request.bot_user,
        role=role,
        description=description[:4000],
        status=status,
        client_locality=(request.bot_user.locality or "").strip()[:255],
    )
    if status == WorkRequestStatus.PENDING:
        try:
            from services.work_request_dispatch import try_dispatch_request

            try_dispatch_request(wr)
        except Exception:
            pass
    return json_response(
        {
            "id": wr.id,
            "status": wr.status,
            "role_name": role.name,
            "description": wr.description,
            "created_at": wr.created_at.isoformat(),
            "needs_photos": requires_photos,
            "photo_count": 0,
        },
        status=201,
    )


@api_login_required
@require_http_methods(["POST"])
def work_request_add_photo(request, pk: int):
    """Добавить фото к черновику заявки (base64 JSON — проще для KMP)."""
    import base64

    from django.core.files.base import ContentFile

    from database.models import WorkRequestPhoto, WorkRequestStatus

    wr = WorkRequest.objects.filter(pk=pk, user=request.bot_user).first()
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    if wr.status not in {WorkRequestStatus.DRAFT, WorkRequestStatus.PENDING}:
        return json_response({"error": "not_editable"}, status=400)

    data = parse_json(request)
    raw_b64 = (data.get("content_base64") or data.get("image_base64") or "").strip()
    filename = (data.get("filename") or "photo.jpg").strip()[:120] or "photo.jpg"
    if not raw_b64:
        return json_response({"error": "content_base64_required"}, status=400)
    # data:image/jpeg;base64,... 
    if "," in raw_b64 and raw_b64.lower().startswith("data:"):
        raw_b64 = raw_b64.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(raw_b64, validate=False)
    except Exception:
        return json_response({"error": "invalid_base64"}, status=400)
    if not image_bytes or len(image_bytes) > 12 * 1024 * 1024:
        return json_response({"error": "invalid_image"}, status=400)

    photo = WorkRequestPhoto(request=wr)
    photo.image.save(filename, ContentFile(image_bytes), save=True)
    return json_response(
        {
            "ok": True,
            "photo_id": photo.id,
            "photo_count": wr.photos.count(),
            "status": wr.status,
        },
        status=201,
    )


@api_login_required
@require_http_methods(["POST"])
def work_request_submit(request, pk: int):
    """Черновик → pending + автоподбор (как «готово» в боте)."""
    from database.models import ActivityKind, ActivityLog, WorkRequestStatus

    wr = (
        WorkRequest.objects.select_related("role")
        .filter(pk=pk, user=request.bot_user)
        .first()
    )
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    if wr.status not in {WorkRequestStatus.DRAFT, WorkRequestStatus.PENDING}:
        return json_response({"error": "already_submitted"}, status=400)

    requires_photos = bool(getattr(wr.role, "requires_work_photos", True))
    photo_n = wr.photos.count()
    if requires_photos and photo_n < 1:
        return json_response({"error": "photos_required"}, status=400)

    if wr.status == WorkRequestStatus.DRAFT:
        wr.status = WorkRequestStatus.PENDING
        wr.save(update_fields=["status", "updated_at"])

    ActivityLog.objects.create(
        user=request.bot_user,
        kind=ActivityKind.WORK_REQUEST,
        title=f"Заявка на исполнителя: {wr.role.name}",
        detail=wr.description[:500],
        meta={"work_request_id": wr.id, "role": wr.role.code, "via": "api"},
    )
    try:
        from panel.admin_tasks import upsert_task
        from database.models import AdminTaskKind

        upsert_task(
            kind=AdminTaskKind.WORK_REQUEST,
            title=f"Заявка: {wr.role.name} — {request.bot_user}",
            description=wr.description[:500],
            user=request.bot_user,
            action_url=f"/panel/work-requests/{wr.id}/",
            source_model="WorkRequest",
            source_id=wr.id,
            priority=25,
        )
    except Exception:
        pass

    dispatched = False
    try:
        from services.work_request_dispatch import try_dispatch_request

        if not (wr.client_locality or "").strip():
            loc = (request.bot_user.locality or "").strip()
            if loc:
                wr.client_locality = loc[:255]
                wr.save(update_fields=["client_locality", "updated_at"])
        dispatched = bool(try_dispatch_request(wr))
    except Exception:
        pass

    return json_response(
        {
            "ok": True,
            "id": wr.id,
            "status": wr.status,
            "photo_count": photo_n,
            "dispatched": dispatched,
        }
    )


@api_login_required
@require_http_methods(["POST"])
def work_request_confirm_amount(request, pk: int):
    """Клиент подтверждает сумму (R24) — та же логика, что ответ в боте."""
    from decimal import Decimal

    from database.models import PendingAction, WorkRequestStatus
    from services.work_request_completion import (
        CLIENT_CONFIRM_PENDING,
        _apply_client_confirmation,
        parse_money,
    )

    user = request.bot_user
    wr = WorkRequest.objects.filter(pk=pk, user=user).first()
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    if wr.status != WorkRequestStatus.AWAITING_CLIENT:
        return json_response({"error": "not_awaiting_confirm"}, status=400)

    data = parse_json(request)
    confirmed = bool(data.get("confirmed"))
    amount = data.get("amount")
    if confirmed:
        money = wr.reported_amount
        if money is None:
            return json_response({"error": "no_reported_amount"}, status=400)
    else:
        money = parse_money(str(amount)) if amount is not None else None
        if money is None:
            return json_response({"error": "amount_required"}, status=400)

    pending, _ = PendingAction.objects.get_or_create(user=user)
    pending.pending_kind = CLIENT_CONFIRM_PENDING
    pending.pending_payload = {"work_request_id": wr.id}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    reply = _apply_client_confirmation(wr, Decimal(money), pending)
    wr.refresh_from_db()
    return json_response({"ok": True, "message": reply, "status": wr.status})
