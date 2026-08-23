"""Ресурсные stub/thin handlers для KMP. Домен — существующие сервисы."""

from __future__ import annotations

from django.views.decorators.http import require_GET, require_http_methods

from api.http import (
    api_login_required,
    api_public,
    api_subscription_required,
    json_response,
    parse_json,
)
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
def health(request):
    from api.http import client_version_code
    from services.app_version import client_needs_update, mobile_version_payload

    body = {"ok": True, "service": "voitos-api-v1"}
    body.update(mobile_version_payload())
    # ?version_code= и/или X-Voitos-App-Version
    ver = client_version_code(request)
    if ver is not None:
        body["client_version_code"] = ver
        body["update_required"] = client_needs_update(ver)
    return json_response(body)


def _me_payload(user, request=None) -> dict:
    avatar_url = ""
    if request is not None and getattr(user, "avatar", None):
        avatar_url = _absolute_media_url(request, user.avatar)
    elif getattr(user, "avatar", None):
        try:
            avatar_url = user.avatar.url or ""
        except Exception:
            avatar_url = ""
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
        "avatar_url": avatar_url,
    }


def _access_payload(user) -> dict:
    user.ensure_grace_period()
    until = user.effective_subscription_until()
    return {
        "state": user.access_state(),
        "subscription_until": until.isoformat() if until else None,
        "grace_until": user.grace_until.isoformat() if user.grace_until else None,
        "label": user.subscription_label(),
        "needs_payment": user.access_state() == "blocked",
        "is_active": bool(user.is_active),
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
    return json_response(_me_payload(user, request))


@api_login_required
@require_http_methods(["POST"])
def me_avatar(request):
    """Загрузка аватара 500×500."""
    from api.media import decode_base64_payload
    from services.group_chat import save_user_avatar

    data = parse_json(request)
    raw_b64 = (data.get("content_base64") or data.get("image_base64") or "").strip()
    filename = (data.get("filename") or "avatar.jpg").strip()[:120] or "avatar.jpg"
    image_bytes = decode_base64_payload(raw_b64)
    if not image_bytes:
        return json_response(
            {"error": "content_base64_required", "detail": "Пришлите изображение."},
            status=400,
        )
    try:
        save_user_avatar(request.bot_user, image_bytes, filename=filename)
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=400)
    except Exception:
        return json_response({"error": "upload_failed", "detail": "Не удалось сохранить аватар."}, status=500)
    request.bot_user.refresh_from_db()
    return json_response({"ok": True, **_me_payload(request.bot_user, request)})


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
            "family": _family_subscription_payload(user),
        }
    )
    return json_response(payload)


def _family_member_item(u, *, relation: str) -> dict:
    return {
        "id": u.id,
        "name": str(u).strip() or (u.phone or f"#{u.id}"),
        "phone": (u.phone or "").strip(),
        "relation": relation,
    }


def _family_subscription_payload(user) -> dict:
    """Кто на семейной подписке: я плательщик → члены; я на чужой → плательщик и остальные."""
    dependents = list(
        user.family_dependents.all().order_by("real_name", "display_name", "id")
    )
    payer = user.family_payer
    members: list[dict] = []
    if dependents:
        for d in dependents:
            members.append(_family_member_item(d, relation="member"))
        return {
            "is_payer": True,
            "payer_name": "",
            "members": members,
        }
    if payer is not None:
        members.append(_family_member_item(payer, relation="payer"))
        for d in payer.family_dependents.exclude(id=user.id).order_by(
            "real_name", "display_name", "id"
        ):
            members.append(_family_member_item(d, relation="member"))
        return {
            "is_payer": False,
            "payer_name": str(payer).strip(),
            "members": members,
        }
    return {"is_payer": False, "payer_name": "", "members": []}


@api_login_required
@require_http_methods(["GET", "POST"])
def me_receipts(request):
    """Список чеков подписки / загрузка нового (base64)."""
    user = request.bot_user
    if request.method == "GET":
        qs = PaymentReceipt.objects.filter(user=user).order_by("-created_at")[:50]
        items = []
        for r in qs:
            items.append(
                {
                    "id": r.id,
                    "status": r.status,
                    "amount": float(r.amount) if r.amount is not None else None,
                    "period": r.period_label() if hasattr(r, "period_label") else "",
                    "created_at": r.created_at.isoformat(),
                    "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                    "admin_comment": (r.admin_comment or "")[:500],
                }
            )
        return json_response({"items": items})

    from api.media import decode_base64_payload
    from subscriptions.service import submit_receipt

    data = parse_json(request)
    raw_b64 = (data.get("content_base64") or data.get("image_base64") or "").strip()
    filename = (data.get("filename") or "receipt.jpg").strip()[:120] or "receipt.jpg"
    image_bytes = decode_base64_payload(raw_b64)
    if not image_bytes:
        return json_response({"error": "content_base64_required"}, status=400)
    try:
        receipt = submit_receipt(user, image_bytes, filename=filename)
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=400)
    except Exception:
        return json_response({"error": "upload_failed"}, status=500)
    return json_response(
        {
            "ok": True,
            "id": receipt.id,
            "status": receipt.status,
            "created_at": receipt.created_at.isoformat(),
        },
        status=201,
    )


@api_login_required
@require_GET
def executor_roles(request):
    from services.master_booking import role_uses_client_booking, roles_for_client_call

    # for=call — только роли, где в НП есть другой мастер (вызов).
    # Без параметра — полный каталог (регистрация исполнителем).
    purpose = (request.GET.get("for") or "").strip().lower()
    if purpose in {"call", "book", "client"}:
        roles = roles_for_client_call(request.bot_user)
    else:
        roles = list(
            ExecutorRole.objects.filter(is_active=True).order_by("sort_order", "id")
        )
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
                    "accepts_at_home": bool(getattr(r, "accepts_at_home", False)),
                    "is_equipment": bool(getattr(r, "is_equipment", False)),
                    "requires_qualification_docs": bool(
                        getattr(r, "requires_qualification_docs", False)
                    ),
                    "client_books_master": role_uses_client_booking(r),
                }
                for r in roles
            ]
        }
    )


@api_login_required
@api_subscription_required
@require_GET
def role_masters(request, role_id: int):
    """Доступные мастера роли для записи клиента."""
    from services.master_booking import list_masters_for_role, role_uses_client_booking

    role = ExecutorRole.objects.filter(pk=role_id, is_active=True).first()
    if not role:
        return json_response({"error": "role_not_found"}, status=404)
    if not role_uses_client_booking(role):
        return json_response(
            {
                "error": "booking_not_supported",
                "detail": "Для этой роли запись к мастеру недоступна.",
                "items": [],
            },
            status=400,
        )
    items = list_masters_for_role(role, request.bot_user)
    return json_response({"role_id": role.id, "role_name": role.name, "items": items})


@api_login_required
@api_subscription_required
@require_GET
def contractor_free_slots(request, contractor_id: int):
    """Свободные окна календаря мастера."""
    from database.models import ContractorProfile, ContractorStatus
    from services.master_booking import (
        contractor_blocked_for_commission,
        free_slots_for_contractor,
        COMMISSION_BLOCK_MSG,
    )

    contractor = (
        ContractorProfile.objects.select_related("user", "role")
        .filter(pk=contractor_id, status=ContractorStatus.VERIFIED)
        .first()
    )
    if not contractor:
        return json_response({"error": "not_found"}, status=404)
    if contractor_blocked_for_commission(contractor):
        return json_response(
            {
                "contractor_id": contractor.id,
                "can_accept": False,
                "blocked_reason": "commission",
                "blocked_message": COMMISSION_BLOCK_MSG,
                "items": [],
            }
        )
    days_raw = (request.GET.get("days") or "2").strip()
    try:
        days = max(1, min(int(days_raw), 28))
    except ValueError:
        days = 2
    items = free_slots_for_contractor(contractor, days=days)
    return json_response(
        {
            "contractor_id": contractor.id,
            "name": str(contractor.user),
            "can_accept": True,
            "items": items,
        }
    )


@api_login_required
@require_http_methods(["POST"])
def work_request_confirm_booking(request, pk: int):
    """Мастер подтверждает предварительную запись клиента → в работе."""
    from services.master_booking import confirm_client_booking

    wr = (
        WorkRequest.objects.select_related(
            "role", "user", "assigned_contractor", "assigned_contractor__user"
        )
        .filter(pk=pk, assigned_contractor__user=request.bot_user)
        .first()
    )
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    try:
        msg = confirm_client_booking(wr, request.bot_user)
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=400)
    wr.refresh_from_db()
    return json_response(
        {
            "ok": True,
            "message": msg,
            "status": wr.status,
            "agreed_slot": wr.agreed_slot or "",
        }
    )


@api_login_required
@api_subscription_required
@require_http_methods(["POST"])
def executor_role_propose(request):
    """Житель просит добавить новую роль в каталог (не чаще 1 раза в сутки)."""
    from services.role_proposals import propose_role, proposal_to_dict

    data = parse_json(request)
    try:
        proposal = propose_role(
            request.bot_user,
            str(data.get("name") or data.get("proposed_name") or ""),
        )
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=400)
    return json_response(
        {"ok": True, "proposal": proposal_to_dict(proposal)},
        status=201,
    )


@api_login_required
@require_GET
def me_executor(request):
    """Является ли пользователь исполнителем + его роли."""
    from database.models import ContractorProfile

    profiles = list(
        ContractorProfile.objects.filter(user=request.bot_user)
        .select_related("role")
        .order_by("id")
    )
    items = []
    for p in profiles:
        items.append(
            {
                "id": p.id,
                "role_id": p.role_id or 0,
                "role_name": p.role.name if p.role_id else p.equipment_type,
                "role_code": p.equipment_type or (p.role.code if p.role_id else ""),
                "status": p.status,
                "status_label": p.get_status_display(),
                "locality": p.locality or "",
                "phone": p.phone or "",
            }
        )
    return json_response(
        {
            "is_executor": bool(items),
            "profiles": items,
            "open_offers_count": _open_offers_qs(request.bot_user).count(),
            "work_notices": _work_notices(request.bot_user),
        }
    )


def _work_notices(user) -> list[dict]:
    try:
        from services.work_request_completion import work_screen_notices_for_user

        return work_screen_notices_for_user(user)
    except Exception:
        return []


def _open_offers_qs(user):
    from database.models import WorkRequestOffer, WorkRequestOfferStatus

    return (
        WorkRequestOffer.objects.filter(
            contractor__user=user,
            status=WorkRequestOfferStatus.OFFERED,
        )
        .select_related(
            "work_request",
            "work_request__role",
            "work_request__user",
            "contractor",
        )
        .order_by("-offered_at", "-id")
    )


@api_login_required
@api_subscription_required
@require_http_methods(["POST"])
def executor_register(request):
    from bot.contractor_registration import submit_contractor_registration_api

    data = parse_json(request)
    try:
        profile, message = submit_contractor_registration_api(request.bot_user, data)
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=400)
    return json_response(
        {
            "ok": True,
            "id": profile.id,
            "status": profile.status,
            "message": message,
        },
        status=201,
    )


@api_login_required
@require_GET
def executor_offers(request):
    items = []
    for offer in _open_offers_qs(request.bot_user)[:50]:
        wr = offer.work_request
        items.append(
            {
                "offer_id": offer.id,
                "work_request_id": wr.id,
                "role_id": wr.role_id or 0,
                "role_name": wr.role.name if wr.role_id else "",
                "description": (wr.description or "")[:800],
                "locality": (wr.client_locality or wr.user.locality or "")[:255],
                "address": (wr.user.address or "")[:500],
                "client_phone": (wr.user.phone or "")[:64],
                "client_name": str(wr.user),
                "accepts_at_home": bool(getattr(wr.role, "accepts_at_home", False)),
                "status": offer.status,
                "respond_deadline": (
                    offer.respond_deadline.isoformat() if offer.respond_deadline else None
                ),
            }
        )
    return json_response({"items": items})


@api_login_required
@require_http_methods(["POST"])
def executor_offer_respond(request, pk: int):
    from database.models import WorkRequestOffer, WorkRequestOfferStatus
    from services.work_request_dispatch import accept_offer, decline_offer

    offer = (
        WorkRequestOffer.objects.select_related(
            "work_request", "work_request__role", "contractor", "contractor__user"
        )
        .filter(pk=pk, contractor__user=request.bot_user)
        .first()
    )
    if not offer:
        return json_response({"error": "not_found"}, status=404)
    if offer.status != WorkRequestOfferStatus.OFFERED:
        return json_response({"error": "already_handled"}, status=400)
    data = parse_json(request)
    accept = bool(data.get("accept") or data.get("yes"))
    if accept:
        msg = accept_offer(offer)
    else:
        msg = decline_offer(offer)
    offer.refresh_from_db()
    return json_response(
        {"ok": True, "message": msg, "status": offer.status, "offer_id": offer.id}
    )


@api_login_required
@require_GET
def executor_jobs(request):
    """Заявки исполнителя, где нужно предложить/ждать согласование времени."""
    from database.models import WorkRequestStatus
    from services.master_booking import contractor_blocked_for_commission
    from services.work_request_schedule import master_visit_address, role_accepts_at_home

    qs = (
        WorkRequest.objects.filter(
            assigned_contractor__user=request.bot_user,
            status=WorkRequestStatus.SCHEDULING,
        )
        .select_related("role", "user", "assigned_contractor")
        .order_by("-updated_at")[:50]
    )
    items = []
    for wr in qs:
        home = role_accepts_at_home(wr)
        prebooked = bool(getattr(wr, "client_prebooked", False)) and bool(
            (wr.agreed_slot or "").strip()
        )
        commission_blocked = False
        if wr.assigned_contractor_id:
            commission_blocked = contractor_blocked_for_commission(wr.assigned_contractor)
        items.append(
            {
                "id": wr.id,
                "status": wr.status,
                "status_label": wr.get_status_display(),
                "role_name": wr.role.name if wr.role_id else "",
                "description": (wr.description or "")[:800],
                "client_name": str(wr.user),
                "client_phone": (wr.user.phone or "")[:64],
                "client_locality": (wr.client_locality or wr.user.locality or "")[:255],
                "client_address": (wr.user.address or "")[:500],
                "accepts_at_home": home,
                "master_address": master_visit_address(wr) if home else "",
                "proposed_slots": _slot_labels(wr),
                "agreed_slot": wr.agreed_slot or "",
                "client_prebooked": prebooked,
                "needs_confirm_booking": prebooked,
                "needs_propose_slots": (not prebooked) and (not bool(_slot_labels(wr))),
                "commission_blocked": commission_blocked,
                "commission_blocked_message": (
                    "Сначала оплатите комиссию по прошлому заказу — "
                    "после этого сможете подтвердить запись."
                    if commission_blocked
                    else ""
                ),
            }
        )
    return json_response({"items": items})


@api_login_required
@require_GET
def executor_schedule(request):
    """Недельный график мастера: согласованные визиты."""
    from datetime import datetime, timedelta

    from django.utils import timezone as dj_tz
    from django.utils.dateparse import parse_date

    from database.models import WorkRequestStatus
    from services.work_request_schedule import parse_slot_datetime_range

    week_raw = (request.GET.get("week_start") or "").strip()
    now = dj_tz.localtime(dj_tz.now())
    if week_raw:
        d0 = parse_date(week_raw)
        if d0 is None:
            return json_response({"error": "bad_week_start"}, status=400)
        week_start = now.replace(
            year=d0.year, month=d0.month, day=d0.day,
            hour=0, minute=0, second=0, microsecond=0,
        )
    else:
        # Понедельник текущей недели
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    week_end = week_start + timedelta(days=7)

    active = {
        WorkRequestStatus.IN_PROGRESS,
        WorkRequestStatus.AWAITING_CLIENT,
        WorkRequestStatus.AWAITING_COMMISSION,
        WorkRequestStatus.SCHEDULING,
    }
    qs = (
        WorkRequest.objects.filter(
            assigned_contractor__user=request.bot_user,
            status__in=active,
        )
        .exclude(agreed_slot="")
        .select_related("role", "user")
        .order_by("id")[:200]
    )
    events = []
    for wr in qs:
        parsed = parse_slot_datetime_range(wr.agreed_slot or "", ref_now=now)
        if not parsed:
            continue
        start, end = parsed
        if end < week_start or start >= week_end:
            continue
        events.append(
            {
                "work_request_id": wr.id,
                "role_name": wr.role.name if wr.role_id else "",
                "client_name": str(wr.user),
                "status": wr.status,
                "status_label": wr.get_status_display(),
                "label": wr.agreed_slot or "",
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "day": start.date().isoformat(),
            }
        )
    events.sort(key=lambda e: e["start_at"])
    return json_response(
        {
            "week_start": week_start.date().isoformat(),
            "week_end": (week_end - timedelta(days=1)).date().isoformat(),
            "items": events,
        }
    )


@api_login_required
@require_http_methods(["POST"])
def work_request_propose_slots(request, pk: int):
    """Мастер предлагает окна клиенту из приложения."""
    from services.work_request_schedule import publish_slots_for_master

    wr = (
        WorkRequest.objects.select_related(
            "role", "user", "assigned_contractor", "assigned_contractor__user"
        )
        .filter(pk=pk, assigned_contractor__user=request.bot_user)
        .first()
    )
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    data = parse_json(request)
    raw_slots = data.get("slots")
    if isinstance(raw_slots, str):
        slots = [raw_slots]
    elif isinstance(raw_slots, list):
        slots = [str(s) for s in raw_slots]
    else:
        slots = []
    # Также принять text с переносами строк.
    text = str(data.get("text") or "").strip()
    if text:
        slots.append(text)
    try:
        msg = publish_slots_for_master(wr, request.bot_user, slots)
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=400)
    wr.refresh_from_db()
    return json_response(
        {
            "ok": True,
            "message": msg,
            "status": wr.status,
            "proposed_slots": _slot_labels(wr),
        }
    )


@api_login_required
@require_http_methods(["GET", "POST"])
def work_requests_list(request):
    if request.method == "POST":
        return work_requests_create(request)
    qs = (
        WorkRequest.objects.filter(user=request.bot_user)
        .select_related("role", "assigned_contractor", "assigned_contractor__user")
        .order_by("-created_at")[:50]
    )
    return json_response(
        {
            "items": [
                _work_request_brief(wr)
                for wr in qs
            ]
        }
    )


def _executor_phone(contractor) -> str:
    if not contractor:
        return ""
    phone = (getattr(contractor, "phone", None) or "").strip()
    if phone:
        return phone
    user = getattr(contractor, "user", None)
    return (getattr(user, "phone", None) or "").strip()


def _executor_contacts_payload(contractor) -> dict:
    if not contractor:
        return {
            "assigned_phone": "",
            "assigned_max_username": "",
            "assigned_max_link": "",
            "assigned_contacts": [],
        }
    from services.contractors import max_profile_link, work_request_executor_contact_lines

    user = getattr(contractor, "user", None)
    uname = (getattr(user, "username", None) or "").strip().lstrip("@")
    return {
        "assigned_phone": _executor_phone(contractor) or None,
        "assigned_max_username": uname or None,
        "assigned_max_link": max_profile_link(user) if user else None,
        "assigned_contacts": work_request_executor_contact_lines(contractor),
    }


def _slot_labels(wr) -> list[str]:
    out = []
    for item in wr.proposed_slots or []:
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip()
        else:
            label = str(item).strip()
        if label:
            out.append(label)
    return out


def _work_request_brief(wr) -> dict:
    from services.work_request_rating import work_request_needs_rating

    slots = _slot_labels(wr)
    contacts = _executor_contacts_payload(wr.assigned_contractor)
    return {
        "id": wr.id,
        "status": wr.status,
        "status_label": wr.get_status_display(),
        "role_name": wr.role.name if wr.role_id else "",
        "role_accepts_at_home": bool(
            getattr(wr.role, "accepts_at_home", False) if wr.role_id else False
        ),
        "description": wr.description or "",
        "created_at": wr.created_at.isoformat(),
        "assigned_executor_name": (
            str(wr.assigned_contractor) if wr.assigned_contractor_id else None
        ),
        "assigned_phone": contacts.get("assigned_phone"),
        "proposed_slots": slots,
        "agreed_slot": wr.agreed_slot or "",
        "can_confirm_slot": wr.status == "scheduling" and bool(slots),
        "needs_confirm_amount": wr.status == "awaiting_client",
        "needs_rating": work_request_needs_rating(wr),
    }


def _work_request_photo_urls(request, wr) -> list[str]:
    urls: list[str] = []
    for photo in wr.photos.all().order_by("id")[:20]:
        u = _absolute_media_url(request, photo.image)
        if u:
            urls.append(u)
    return urls


@api_login_required
@require_GET
def work_request_detail(request, pk: int):
    wr = (
        WorkRequest.objects.select_related(
            "role", "assigned_contractor", "assigned_contractor__user", "user"
        )
        .prefetch_related("photos")
        .filter(pk=pk)
        .first()
    )
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    is_client = wr.user_id == request.bot_user.id
    is_executor = (
        wr.assigned_contractor_id
        and wr.assigned_contractor.user_id == request.bot_user.id
    )
    if not (is_client or is_executor):
        return json_response({"error": "not_found"}, status=404)
    from services.work_request_completion import (
        both_parties_marked_done,
        can_mark_work_done,
    )
    from services.work_request_rating import work_request_needs_rating

    contractor = wr.assigned_contractor
    requires_photos = bool(
        getattr(wr.role, "requires_work_photos", True) if wr.role_id else True
    )
    photo_urls = _work_request_photo_urls(request, wr)
    contacts = _executor_contacts_payload(contractor)
    payload = _work_request_brief(wr)
    slots = _slot_labels(wr)
    can_mark = can_mark_work_done(wr)
    client_done = bool(wr.client_marked_done_at)
    exec_done = bool(wr.executor_marked_done_at)
    same_person = is_client and is_executor
    needs_client_pay = is_client and wr.confirmed_amount is None and (
        wr.status in ("awaiting_client", "awaiting_commission", "done")
        and wr.reported_amount is not None
    )
    needs_exec_pay = (
        is_executor
        and both_parties_marked_done(wr)
        and wr.reported_amount is None
        and wr.status in ("in_progress", "scheduling")
    )
    # Один аккаунт: после dual mark сразу показываем отчёт оплаты.
    if same_person and needs_exec_pay:
        viewer = "executor"
    elif is_executor and not is_client:
        viewer = "executor"
    else:
        viewer = "client"
    can_cancel = wr.status in {
        "draft",
        "pending",
        "offering",
        "scheduling",
        "in_progress",
        "awaiting_client",
        "awaiting_commission",
    }
    payload.update(
        {
            "viewer": viewer,
            "assigned_name": str(contractor) if contractor else None,
            "assigned_phone": contacts.get("assigned_phone"),
            "assigned_max_username": contacts.get("assigned_max_username"),
            "assigned_max_link": contacts.get("assigned_max_link"),
            "assigned_contacts": contacts.get("assigned_contacts") or [],
            "client_name": str(wr.user),
            "client_phone": (wr.user.phone or "").strip() or None,
            "can_confirm_slot": is_client and wr.status == "scheduling" and bool(slots),
            "needs_confirm_amount": needs_client_pay,
            "needs_rating": work_request_needs_rating(wr) if is_client else False,
            "needs_photos": requires_photos and wr.status == "draft" and is_client,
            "photo_count": len(photo_urls),
            "photo_urls": photo_urls,
            "client_locality": (wr.client_locality or "").strip()
            or (getattr(wr.user, "locality", None) or "").strip(),
            "client_address": (getattr(wr.user, "address", None) or "").strip(),
            "master_address": (wr.master_address or "").strip(),
            "reported_amount": float(wr.reported_amount)
            if wr.reported_amount is not None
            else None,
            "confirmed_amount": float(wr.confirmed_amount)
            if wr.confirmed_amount is not None
            else None,
            "pay_method": wr.pay_method or "",
            "updated_at": wr.updated_at.isoformat() if wr.updated_at else "",
            "can_mark_done": can_mark
            and (
                (is_client and not client_done)
                or (is_executor and not exec_done)
            ),
            "can_cancel": can_cancel and (is_client or is_executor),
            "client_marked_done": client_done,
            "executor_marked_done": exec_done,
            "needs_executor_payment_report": needs_exec_pay,
            "same_person": same_person,
            "client_cancel_comment": (wr.client_cancel_comment or "").strip(),
            "executor_cancel_comment": (wr.executor_cancel_comment or "").strip(),
            "amount_mismatch_message": (wr.amount_mismatch_message or "").strip(),
            "amount_mismatch_due": float(wr.amount_mismatch_due)
            if wr.amount_mismatch_due is not None
            else None,
        }
    )
    return json_response(payload)


@api_login_required
@require_http_methods(["POST"])
def work_request_confirm_slot(request, pk: int):
    from services.work_request_schedule import confirm_slot_for_client

    wr = (
        WorkRequest.objects.select_related("role", "assigned_contractor", "user")
        .filter(pk=pk, user=request.bot_user)
        .first()
    )
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    data = parse_json(request)
    try:
        msg = confirm_slot_for_client(wr, slot=str(data.get("slot") or ""))
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=400)
    wr.refresh_from_db()
    return json_response(
        {
            "ok": True,
            "message": msg,
            "status": wr.status,
            "status_label": wr.get_status_display(),
            "agreed_slot": wr.agreed_slot or "",
        }
    )


@api_login_required
@require_GET
def onboarding(request):
    prog = panel_progress(request.bot_user)
    for step in prog["steps"]:
        step["image_url"] = f"/static/{step['image']}"
        # Имя файла в APK assets/onboarding/ (те же 5 комиксов, что в боте)
        step["asset"] = step["image"].rsplit("/", 1)[-1]
    if prog.get("completed_at"):
        prog["completed_at"] = prog["completed_at"].isoformat()
    prog["reward_just_granted"] = False
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
        user.refresh_from_db()

    reward_just_granted = False
    if len(progress_list(user)) >= len(STORIES) and not user.onboarding_reward_granted:
        from bot.onboarding import _grant_reward_if_needed
        from database.models import PendingAction

        pending, _ = PendingAction.objects.get_or_create(user=user)
        _grant_reward_if_needed(user)
        pending.clear_pending()
        user.refresh_from_db()
        reward_just_granted = True
        try:
            from api.emit import emit_app_event
            from django.utils import timezone as dj_tz

            until = user.subscription_until
            until_s = (
                dj_tz.localtime(until).strftime("%d.%m.%Y") if until else "—"
            )
            emit_app_event(
                user,
                ntype="subscription.onboarding_reward",
                title="Месяц подписки начислен",
                body=(
                    "Вы прошли обучение Voitos. "
                    f"Автоматически начислен месяц подписки (до {until_s})."
                ),
                entity_type="subscription",
            )
        except Exception:
            pass

    prog = panel_progress(user)
    for step in prog["steps"]:
        step["image_url"] = f"/static/{step['image']}"
        step["asset"] = step["image"].rsplit("/", 1)[-1]
    if prog.get("completed_at"):
        prog["completed_at"] = prog["completed_at"].isoformat()
    prog["reward_just_granted"] = reward_just_granted
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


def _absolute_media_url(request, file_field) -> str:
    if not file_field:
        return ""
    try:
        url = file_field.url
    except Exception:
        return ""
    if not url:
        return ""
    if str(url).startswith("http://") or str(url).startswith("https://"):
        return str(url)
    return request.build_absolute_uri(url)


def _campaign_photo_urls(request, campaign) -> list[str]:
    urls: list[str] = []
    if not campaign:
        return urls
    for photo in campaign.offer_photos.all().order_by("id")[:12]:
        u = _absolute_media_url(request, photo.image)
        if u:
            urls.append(u)
    return urls


def _service_payment_payload() -> dict:
    cfg = AppSettings.load()
    return {
        "payment_name": (cfg.service_payee_name or "").strip(),
        "payment_phone": (cfg.service_payee_phone or "").strip(),
        "payment_bank": (getattr(cfg, "service_payee_bank", None) or "").strip(),
        "payment_status": (cfg.service_payee_status or "").strip(),
    }


@api_login_required
@require_http_methods(["GET", "POST"])
def me_feedback(request):
    """Обращения из приложения: баг / ОС / отзыв о менеджере."""
    from services.feedback import (
        create_feedback_ticket,
        list_user_feedback,
        manager_context_dict,
        ticket_to_dict,
    )

    user = request.bot_user
    if request.method == "GET":
        mgr = manager_context_dict(user)
        return json_response(
            {
                "items": [ticket_to_dict(t) for t in list_user_feedback(user)],
                "manager": mgr,
                "managers": mgr.get("managers") or [],
                "notice": (
                    "Баги и обратная связь рассматриваются администратором. "
                    "ОС по менеджеру отвечает автоматически (ИИ), оценка попадает "
                    "к закреплённому менеджеру вашей группы."
                ),
            }
        )
    data = parse_json(request)
    try:
        ticket = create_feedback_ticket(
            user,
            kind=str(data.get("kind") or ""),
            body=str(data.get("body") or ""),
            subject=str(data.get("subject") or ""),
            score=data.get("score"),
            group_id=data.get("group_id"),
        )
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=400)
    return json_response({"ok": True, "ticket": ticket_to_dict(ticket)}, status=201)


@api_login_required
@require_http_methods(["GET", "POST"])
def me_wishes(request):
    """Пожелания двора из приложения — тот же NeighborhoodWish, что и из MAX."""
    from services.wishes import capture_wish, user_groups

    user = request.bot_user
    groups = user_groups(user)
    if request.method == "GET":
        from database.models import NeighborhoodWish

        qs = (
            NeighborhoodWish.objects.filter(user=user)
            .select_related("group")
            .order_by("-created_at")[:50]
        )
        return json_response(
            {
                "items": [
                    {
                        "id": w.id,
                        "text": w.text,
                        "topic": w.topic,
                        "topic_label": w.get_topic_display(),
                        "group_id": w.group_id,
                        "group_name": w.group.name if w.group_id else "",
                        "created_at": w.created_at.isoformat(),
                    }
                    for w in qs
                ],
                "groups": [{"id": g.id, "name": g.name} for g in groups],
            }
        )

    data = parse_json(request)
    text = str(data.get("text") or "").strip()
    if not text:
        return json_response({"error": "empty", "detail": "Введите текст пожелания"}, status=400)
    if not groups:
        return json_response(
            {
                "error": "no_group",
                "detail": "Вас пока нет в группе жителей — попросите администратора добавить.",
            },
            status=400,
        )
    group_id = data.get("group_id")
    group = None
    if group_id is not None:
        group = next((g for g in groups if g.id == int(group_id)), None)
        if group is None:
            return json_response({"error": "bad_group"}, status=400)
    elif len(groups) == 1:
        group = groups[0]
    else:
        return json_response(
            {
                "error": "group_required",
                "detail": "Выберите группу",
                "groups": [{"id": g.id, "name": g.name} for g in groups],
            },
            status=400,
        )
    try:
        wish = capture_wish(
            user,
            text,
            group=group,
            source_message=f"app:{text}",
        )
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=400)
    return json_response(
        {
            "id": wish.id,
            "topic": wish.topic,
            "topic_label": wish.get_topic_display(),
            "text": wish.text,
            "group_id": wish.group_id,
            "group_name": group.name,
        },
        status=201,
    )


def _campaign_progress_payload(campaign) -> dict:
    """Собрано / цель + сколько оплатили из приглашённых."""
    from database.models import InviteStatus
    from services.service import campaign_collected

    if campaign is None:
        return {
            "paid_count": 0,
            "invite_count": 0,
            "collected_amount": 0.0,
            "total_amount": 0.0,
            "progress_percent": 0,
        }
    invites = list(campaign.invites.all())
    paid_count = sum(1 for i in invites if i.status == InviteStatus.PAID)
    invite_count = len(invites)
    collected = float(campaign_collected(campaign) or 0)
    total = float(getattr(campaign, "total_amount", 0) or 0)
    pct = min(100, int(collected * 100 / total)) if total > 0 else 0
    return {
        "paid_count": paid_count,
        "invite_count": invite_count,
        "collected_amount": collected,
        "total_amount": total,
        "progress_percent": pct,
    }


@api_login_required
@api_subscription_required
@require_GET
def collections_list(request):
    """Список инвайтов жителя в сборы (новые сверху)."""
    try:
        from database.models import (
            CampaignStatus,
            InviteStatus,
            ReceiptStatus,
            ServiceCampaign,
            ServiceInvite,
        )

        # Лимит одновременных активных сборов на группу (для баннера в приложении).
        active_limit = 4

        invites = (
            ServiceInvite.objects.filter(user=request.bot_user)
            .exclude(status=InviteStatus.CANCELLED)
            .select_related("campaign")
            .prefetch_related(
                "campaign__offer_photos",
                "campaign__invites",
                "receipts",
            )
            .order_by("-campaign__created_at", "-campaign_id", "-id")[:50]
        )
        pay = _service_payment_payload()
        items = []
        for inv in invites:
            camp = inv.campaign
            photos = _campaign_photo_urls(request, camp)
            receipts = list(inv.receipts.all())
            pending_n = sum(1 for r in receipts if r.status == ReceiptStatus.PENDING)
            rejected_n = sum(1 for r in receipts if r.status == ReceiptStatus.REJECTED)
            items.append(
                {
                    "id": camp.id if camp else inv.id,
                    "title": getattr(camp, "title", None) or "Сбор",
                    "category": getattr(camp, "category", "") or "",
                    "category_label": (
                        camp.get_category_display() if camp else ""
                    ),
                    "amount_due": float(getattr(inv, "amount_due", 0) or 0),
                    "status": inv.status,
                    "campaign_status": getattr(camp, "status", "") or "",
                    "share_policy": "fixed",
                    "share_policy_note": (
                        "Взнос зафиксирован при старте сбора. Новые участники группы "
                        "не уменьшают вашу сумму; кто уже оплатил — без изменений. "
                        "Сверх цели уходит в свободный баланс группы."
                    ),
                    "event_at": (
                        camp.event_at.isoformat()
                        if camp and getattr(camp, "event_at", None)
                        else None
                    ),
                    "cover_photo_url": photos[0] if photos else "",
                    "photo_urls": photos,
                    "description": (getattr(camp, "description", None) or "")[:400],
                    "pending_receipts": pending_n,
                    "rejected_receipts": rejected_n,
                    "created_at": (
                        camp.created_at.isoformat()
                        if camp and getattr(camp, "created_at", None)
                        else None
                    ),
                    **_campaign_progress_payload(camp),
                    **pay,
                }
            )

        # Лимит по группе: если у любой группы пользователя уже 4+ активных сбора.
        from services.wishes import user_groups

        group_ids = {g.id for g in user_groups(request.bot_user)}
        at_active_limit = False
        if group_ids:
            from django.db.models import Count

            at_active_limit = (
                ServiceCampaign.objects.filter(
                    group_id__in=group_ids,
                    status=CampaignStatus.ACTIVE,
                )
                .values("group_id")
                .annotate(n=Count("id"))
                .filter(n__gte=active_limit)
                .exists()
            )
        # Запасной критерий: на экране уже 4+ активных сбора у этого пользователя.
        if not at_active_limit:
            active_on_screen = sum(
                1 for it in items if (it.get("campaign_status") or "") == CampaignStatus.ACTIVE
            )
            at_active_limit = active_on_screen >= active_limit

        return json_response(
            {
                "items": items,
                "active_limit": active_limit,
                "at_active_limit": at_active_limit,
            }
        )
    except Exception:
        return json_response(
            {"items": [], "active_limit": 4, "at_active_limit": False}
        )


@api_login_required
@api_subscription_required
@require_GET
def collection_detail(request, pk: int):
    """Детали сбора по campaign id для текущего пользователя."""
    from database.models import InviteStatus, ReceiptStatus, ServiceInvite

    inv = (
        ServiceInvite.objects.filter(user=request.bot_user, campaign_id=pk)
        .select_related("campaign")
        .prefetch_related("campaign__offer_photos", "campaign__invites")
        .first()
    )
    if not inv:
        return json_response({"error": "not_found"}, status=404)
    camp = inv.campaign
    pending = int(
        inv.receipts.filter(status=ReceiptStatus.PENDING).count() if hasattr(inv, "receipts") else 0
    )
    rejected = int(
        inv.receipts.filter(status=ReceiptStatus.REJECTED).count() if hasattr(inv, "receipts") else 0
    )
    photos = _campaign_photo_urls(request, camp)
    can_pay = inv.status == InviteStatus.OFFERED and (
        camp.status != "closed" if camp else True
    )
    payload = {
        "id": camp.id,
        "title": camp.title,
        "category": camp.category or "",
        "category_label": camp.get_category_display(),
        "description": camp.description or "",
        "amount_due": float(inv.amount_due or 0),
        "amount_paid": float(inv.amount_paid or 0),
        "status": inv.status,
        "campaign_status": camp.status or "",
        "share_policy": "fixed",
        "share_policy_note": (
            "Взнос зафиксирован при старте сбора. Новые участники группы "
            "не уменьшают вашу сумму; кто уже оплатил — без изменений. "
            "Сверх цели уходит в свободный баланс группы."
        ),
        "event_at": camp.event_at.isoformat() if camp.event_at else None,
        "invite_id": inv.id,
        "can_pay": can_pay,
        "pending_receipts": pending,
        "rejected_receipts": rejected,
        "cover_photo_url": photos[0] if photos else "",
        "photo_urls": photos,
        **_campaign_progress_payload(camp),
        **_service_payment_payload(),
    }
    return json_response(payload)


@api_login_required
@api_subscription_required
@require_http_methods(["POST"])
def collection_receipt(request, pk: int):
    """Загрузить чек оплаты сбора (base64)."""
    from database.models import InviteStatus, ServiceInvite
    from services.service import submit_service_receipt

    inv = (
        ServiceInvite.objects.filter(user=request.bot_user, campaign_id=pk)
        .select_related("campaign")
        .first()
    )
    if not inv:
        return json_response({"error": "not_found"}, status=404)
    if inv.status == InviteStatus.PAID:
        return json_response({"error": "already_paid", "message": "Сбор уже оплачен"}, status=400)
    data = parse_json(request)
    from api.media import decode_base64_payload

    image_bytes = decode_base64_payload(data.get("content_base64") or "")
    if not image_bytes:
        return json_response({"error": "bad_image"}, status=400)
    filename = (data.get("filename") or "receipt.jpg").strip()[:120] or "receipt.jpg"
    try:
        receipt = submit_service_receipt(
            request.bot_user,
            image_bytes,
            invite=inv,
            filename=filename,
        )
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=400)
    return json_response(
        {
            "ok": True,
            "id": receipt.id,
            "status": receipt.status,
            "message": "Чек отправлен на проверку",
            "created_at": receipt.created_at.isoformat(),
        },
        status=201,
    )


@api_login_required
@api_subscription_required
@require_http_methods(["POST"])
def work_requests_create(request):
    from database.models import WorkRequestStatus
    from services.master_booking import create_client_booking, role_uses_client_booking

    data = parse_json(request)
    role_id = data.get("role_id")
    description = (data.get("description") or "").strip()
    if not role_id or len(description) < 5:
        return json_response({"error": "role_and_description_required"}, status=400)
    role = ExecutorRole.objects.filter(pk=role_id, is_active=True).first()
    if not role:
        return json_response({"error": "role_not_found"}, status=404)

    contractor_id = data.get("contractor_id") or data.get("master_id")
    slot = (data.get("slot") or data.get("agreed_slot") or "").strip()

    if role_uses_client_booking(role):
        if not contractor_id or not slot:
            return json_response(
                {
                    "error": "master_and_slot_required",
                    "detail": "Выберите мастера и свободное время.",
                },
                status=400,
            )
        try:
            wr = create_client_booking(
                client=request.bot_user,
                role=role,
                description=description,
                contractor_id=int(contractor_id),
                slot_label=slot,
            )
        except ValueError as exc:
            return json_response(
                {"error": str(exc), "detail": str(exc)},
                status=400,
            )
        requires_photos = bool(getattr(role, "requires_work_photos", True))
        return json_response(
            {
                "id": wr.id,
                "status": wr.status,
                "role_name": role.name,
                "description": wr.description,
                "created_at": wr.created_at.isoformat(),
                "needs_photos": requires_photos and wr.status == WorkRequestStatus.DRAFT,
                "photo_count": 0,
                "agreed_slot": wr.agreed_slot or "",
                "client_prebooked": True,
            },
            status=201,
        )

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
            "client_prebooked": False,
        },
        status=201,
    )


@api_login_required
@api_subscription_required
@require_http_methods(["POST"])
def work_request_add_photo(request, pk: int):
    """Добавить фото к черновику заявки (base64 JSON — проще для KMP)."""
    from django.core.files.base import ContentFile

    from api.media import decode_base64_payload, unique_upload_filename
    from database.models import WorkRequestPhoto, WorkRequestStatus

    wr = WorkRequest.objects.filter(pk=pk, user=request.bot_user).first()
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    if wr.status not in {WorkRequestStatus.DRAFT, WorkRequestStatus.PENDING}:
        return json_response({"error": "not_editable"}, status=400)

    data = parse_json(request)
    raw_b64 = (data.get("content_base64") or data.get("image_base64") or "").strip()
    raw_name = (data.get("filename") or "photo.jpg").strip()[:120] or "photo.jpg"
    image_bytes = decode_base64_payload(raw_b64)
    if not image_bytes:
        return json_response({"error": "content_base64_required"}, status=400)

    filename = unique_upload_filename(raw_name)
    photo = WorkRequestPhoto(request=wr)
    photo.image.save(filename, ContentFile(image_bytes), save=True)
    photo_count = WorkRequestPhoto.objects.filter(request=wr).count()
    return json_response(
        {
            "ok": True,
            "photo_id": photo.id,
            "photo_count": photo_count,
            "filename": photo.image.name.split("/")[-1] if photo.image.name else filename,
            "status": wr.status,
        },
        status=201,
    )


@api_login_required
@api_subscription_required
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
        # Предзапись к мастеру — сразу в scheduling, без автоподбора.
        if getattr(wr, "client_prebooked", False) and wr.assigned_contractor_id:
            wr.status = WorkRequestStatus.SCHEDULING
        else:
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
    if getattr(wr, "client_prebooked", False) and wr.assigned_contractor_id:
        try:
            from services.master_booking import activate_prebooking_after_photos

            # Уже SCHEDULING — только уведомить, если ещё не уведомляли при create
            if wr.status == WorkRequestStatus.SCHEDULING:
                activate_prebooking_after_photos(wr)
            dispatched = True
        except Exception:
            pass
    else:
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
def work_request_cancel(request, pk: int):
    """Клиент или исполнитель отменяет заявку (с комментарием)."""
    from services.work_request_cancel import (
        cancel_client_work_request,
        cancel_executor_work_request,
    )

    wr = (
        WorkRequest.objects.select_related("assigned_contractor", "assigned_contractor__user")
        .filter(pk=pk)
        .first()
    )
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    data = parse_json(request)
    comment = (data.get("comment") or data.get("note") or "").strip()
    is_client = wr.user_id == request.bot_user.id
    is_executor = bool(
        wr.assigned_contractor_id
        and wr.assigned_contractor.user_id == request.bot_user.id
    )
    try:
        if is_client:
            if wr.status in {
                "scheduling",
                "in_progress",
                "awaiting_client",
                "awaiting_commission",
            } and not comment:
                return json_response(
                    {"error": "comment_required", "detail": "Укажите комментарий к отмене."},
                    status=400,
                )
            cancel_client_work_request(
                request.bot_user,
                wr,
                note="Отменено клиентом в приложении.",
                comment=comment,
            )
        elif is_executor:
            cancel_executor_work_request(request.bot_user, wr, comment=comment)
        else:
            return json_response({"error": "not_found"}, status=404)
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=400)
    wr.refresh_from_db()
    return json_response(
        {
            "ok": True,
            "id": wr.id,
            "status": wr.status,
            "client_cancel_comment": wr.client_cancel_comment or "",
            "executor_cancel_comment": wr.executor_cancel_comment or "",
        }
    )


@api_login_required
@require_http_methods(["POST"])
def work_request_mark_done(request, pk: int):
    """Клиент или мастер жмёт «Работа выполнена»."""
    from services.work_request_completion import mark_work_done

    wr = (
        WorkRequest.objects.select_related(
            "role", "assigned_contractor", "assigned_contractor__user", "user"
        )
        .filter(pk=pk)
        .first()
    )
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    try:
        result = mark_work_done(request.bot_user, wr)
    except ValueError as exc:
        code = str(exc)
        detail = {
            "forbidden": "Нет доступа к этой заявке.",
            "not_in_progress": "Заявка ещё не в работе (нужно согласовать время).",
            "already_marked": "Вы уже отметили заявку выполненной.",
        }.get(code, code)
        return json_response({"error": code, "detail": detail}, status=400)
    wr.refresh_from_db()
    result["status"] = wr.status
    result["id"] = wr.id
    return json_response(result)


@api_login_required
@require_http_methods(["POST"])
def work_request_report_payment(request, pk: int):
    """Мастер указывает способ и сумму оплаты после того, как оба нажали «выполнено»."""
    from database.models import PendingAction, WorkRequestPayMethod
    from services.work_request_completion import (
        COMPLETE_PENDING,
        _finalize_executor_report,
        both_parties_marked_done,
        parse_money,
    )

    wr = (
        WorkRequest.objects.select_related(
            "assigned_contractor", "assigned_contractor__user", "role", "user"
        )
        .filter(pk=pk)
        .first()
    )
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    if not wr.assigned_contractor or wr.assigned_contractor.user_id != request.bot_user.id:
        return json_response({"error": "forbidden"}, status=403)
    if not both_parties_marked_done(wr):
        return json_response({"error": "both_must_mark_done"}, status=400)
    if wr.reported_amount is not None:
        return json_response({"error": "already_reported"}, status=400)

    data = parse_json(request)
    method_raw = (data.get("pay_method") or "").strip().lower()
    if method_raw in {"transfer", "перевод", "1"}:
        method = WorkRequestPayMethod.TRANSFER
    elif method_raw in {"cash", "наличные", "наличка", "2"}:
        method = WorkRequestPayMethod.CASH
    else:
        return json_response({"error": "pay_method_required"}, status=400)
    amount = parse_money(str(data.get("amount") or ""))
    if amount is None:
        return json_response({"error": "amount_required"}, status=400)

    pending, _ = PendingAction.objects.get_or_create(user=request.bot_user)
    pending.pending_kind = COMPLETE_PENDING
    payload = {
        "step": "amount",
        "work_request_id": wr.id,
        "pay_method": method,
        "amount": str(amount),
    }
    pending.pending_payload = payload
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    reply = _finalize_executor_report(
        request.bot_user,
        pending,
        payload,
        wr,
        receipt_bytes=None,
    )
    wr.refresh_from_db()
    return json_response(
        {
            "ok": True,
            "message": reply,
            "status": wr.status,
            "reported_amount": float(wr.reported_amount) if wr.reported_amount else None,
            "commission_amount": float(wr.commission_amount)
            if wr.commission_amount
            else None,
        }
    )


@api_login_required
@require_http_methods(["POST"])
def work_request_confirm_amount(request, pk: int):
    """Клиент указывает способ/сумму оплаты (после опроса или из приложения)."""
    from decimal import Decimal

    from database.models import PendingAction, WorkRequestPayMethod, WorkRequestStatus
    from services.work_request_completion import (
        CLIENT_CONFIRM_PENDING,
        _apply_client_confirmation,
        parse_money,
    )

    user = request.bot_user
    wr = WorkRequest.objects.filter(pk=pk, user=user).first()
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    if wr.confirmed_amount is not None:
        return json_response({"error": "already_confirmed"}, status=400)
    if wr.reported_amount is None:
        return json_response({"error": "executor_not_reported"}, status=400)
    if wr.status not in {
        WorkRequestStatus.AWAITING_CLIENT,
        WorkRequestStatus.AWAITING_COMMISSION,
        WorkRequestStatus.DONE,
    }:
        return json_response({"error": "not_awaiting_confirm"}, status=400)

    data = parse_json(request)
    confirmed = bool(data.get("confirmed"))
    amount = data.get("amount")
    method_raw = (data.get("pay_method") or "").strip().lower()
    pay_method = ""
    if method_raw in {"transfer", "перевод", "1"}:
        pay_method = WorkRequestPayMethod.TRANSFER
    elif method_raw in {"cash", "наличные", "наличка", "2"}:
        pay_method = WorkRequestPayMethod.CASH
    if confirmed:
        money = wr.reported_amount
        if money is None:
            return json_response({"error": "no_reported_amount"}, status=400)
        pay_method = pay_method or wr.pay_method or WorkRequestPayMethod.CASH
    else:
        money = parse_money(str(amount)) if amount is not None else None
        if money is None:
            return json_response({"error": "amount_required"}, status=400)
        if not pay_method:
            return json_response({"error": "pay_method_required"}, status=400)

    pending, _ = PendingAction.objects.get_or_create(user=user)
    pending.pending_kind = CLIENT_CONFIRM_PENDING
    pending.pending_payload = {"work_request_id": wr.id, "step": "amount"}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    reply = _apply_client_confirmation(
        wr, Decimal(money), pending, pay_method=pay_method
    )
    wr.refresh_from_db()
    from services.work_request_rating import work_request_needs_rating

    return json_response(
        {
            "ok": True,
            "message": reply,
            "status": wr.status,
            "needs_rating": work_request_needs_rating(wr),
        }
    )


@api_login_required
@require_http_methods(["POST"])
def work_request_rate(request, pk: int):
    """Клиент оценивает исполнителя (1–5) после выполнения заявки."""
    from services.work_request_rating import submit_work_request_rating

    user = request.bot_user
    wr = (
        WorkRequest.objects.select_related("assigned_contractor", "assigned_contractor__user")
        .filter(pk=pk, user=user)
        .first()
    )
    if not wr:
        return json_response({"error": "not_found"}, status=404)

    data = parse_json(request)
    raw_score = data.get("score")
    comment = str(data.get("comment") or "")
    try:
        rating = submit_work_request_rating(
            user,
            wr,
            score=raw_score,
            comment=comment,
        )
    except ValueError as exc:
        code = str(exc)
        status = {
            "forbidden": 403,
            "already_rated": 409,
            "not_ready_for_rating": 400,
            "no_executor": 400,
            "score_out_of_range": 400,
            "score_invalid": 400,
        }.get(code, 400)
        return json_response({"error": code, "detail": code}, status=status)

    return json_response(
        {
            "ok": True,
            "score": rating.score,
            "comment": rating.comment or "",
            "message": (
                f"Спасибо! Сохранили оценку {rating.score}/5"
                + (" и комментарий." if rating.comment else ".")
            ),
        }
    )


@api_login_required
@require_GET
def groups_list(request):
    """Группы, в которых состоит житель (для чата) + непрочитанные."""
    from services.group_chat import groups_payload_for_user

    return json_response(groups_payload_for_user(request.bot_user))


@api_login_required
@require_http_methods(["GET", "POST"])
def group_messages(request, group_id: int):
    """История и отправка сообщений в чат группы."""
    from services.group_chat import list_messages, mark_group_read, post_message, require_group_member

    try:
        group = require_group_member(request.bot_user, group_id)
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=403)

    if request.method == "GET":
        from services.group_chat import attach_payment_dots, collection_payment_overlay

        after_raw = (request.GET.get("after_id") or "").strip()
        before_raw = (request.GET.get("before_id") or "").strip()
        after_id = int(after_raw) if after_raw.isdigit() else None
        before_id = int(before_raw) if before_raw.isdigit() else None
        overlay = collection_payment_overlay(group)
        try:
            items = list_messages(
                request.bot_user,
                group,
                after_id=after_id,
                before_id=before_id,
                request=request,
            )
        except Exception:
            return json_response(
                {
                    "group": {"id": group.id, "name": group.name or ""},
                    "items": [],
                    **overlay,
                }
            )
        attach_payment_dots(items, overlay.get("author_paid") or {})
        # Открытие/опрос ленты без after_id — помечаем прочитанным
        if after_id is None and before_id is None and items:
            mark_group_read(
                request.bot_user,
                group,
                last_read_message_id=items[-1]["id"],
            )
        elif after_id is not None and items:
            mark_group_read(
                request.bot_user,
                group,
                last_read_message_id=items[-1]["id"],
            )
        return json_response(
            {
                "group": {"id": group.id, "name": group.name or ""},
                "items": items,
                **overlay,
            }
        )

    data = parse_json(request)
    try:
        item = post_message(
            request.bot_user,
            group,
            str(data.get("text") or ""),
            request=request,
        )
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=400)
    return json_response({"ok": True, "message": item}, status=201)


@api_login_required
@require_http_methods(["POST"])
def group_mark_read(request, group_id: int):
    """Пометить чат группы прочитанным (до last_read_message_id или до конца)."""
    from services.group_chat import mark_group_read, require_group_member, unread_count_for_group

    try:
        group = require_group_member(request.bot_user, group_id)
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=403)
    data = parse_json(request)
    raw = data.get("last_read_message_id")
    last_id = None
    if raw is not None and str(raw).strip() != "":
        try:
            last_id = int(raw)
        except (TypeError, ValueError):
            return json_response(
                {"error": "bad_last_read", "detail": "Некорректный id сообщения."},
                status=400,
            )
    try:
        state = mark_group_read(
            request.bot_user, group, last_read_message_id=last_id
        )
    except ValueError as exc:
        return json_response({"error": str(exc), "detail": str(exc)}, status=400)
    return json_response(
        {
            "ok": True,
            "group_id": group.id,
            "last_read_message_id": state.last_read_message_id,
            "unread_count": unread_count_for_group(request.bot_user, group),
        }
    )
