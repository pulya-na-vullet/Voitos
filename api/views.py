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
@require_GET
def work_requests_list(request):
    qs = (
        WorkRequest.objects.filter(user=request.bot_user)
        .select_related("role")
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
        }
    )


@api_login_required
@require_GET
def onboarding(request):
    prog = panel_progress(request.bot_user)
    for step in prog["steps"]:
        step["image_url"] = f"/static/{step['image']}"
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
    """Stub: список инвайтов — полный wiring в следующем PR."""
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
                    "title": getattr(camp, "title", None) or getattr(camp, "name", "") or "Сбор",
                    "category": getattr(camp, "category", "") or "",
                    "amount_due": float(getattr(inv, "amount", 0) or 0),
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
