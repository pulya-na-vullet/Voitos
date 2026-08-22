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
                    "is_equipment": bool(getattr(r, "is_equipment", False)),
                    "requires_qualification_docs": bool(
                        getattr(r, "requires_qualification_docs", False)
                    ),
                }
                for r in roles
            ]
        }
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
        }
    )


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
                _work_request_brief(wr)
                for wr in qs
            ]
        }
    )


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
    slots = _slot_labels(wr)
    return {
        "id": wr.id,
        "status": wr.status,
        "status_label": wr.get_status_display(),
        "role_name": wr.role.name if wr.role_id else "",
        "description": wr.description or "",
        "created_at": wr.created_at.isoformat(),
        "assigned_executor_name": (
            str(wr.assigned_contractor) if wr.assigned_contractor_id else None
        ),
        "proposed_slots": slots,
        "agreed_slot": wr.agreed_slot or "",
        "can_confirm_slot": wr.status == "scheduling",
        "needs_confirm_amount": wr.status == "awaiting_client",
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
        WorkRequest.objects.select_related("role", "assigned_contractor", "user")
        .prefetch_related("photos")
        .filter(pk=pk, user=request.bot_user)
        .first()
    )
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    contractor = wr.assigned_contractor
    requires_photos = bool(
        getattr(wr.role, "requires_work_photos", True) if wr.role_id else True
    )
    photo_urls = _work_request_photo_urls(request, wr)
    payload = _work_request_brief(wr)
    payload.update(
        {
            "assigned_name": str(contractor) if contractor else None,
            "assigned_phone": getattr(contractor, "phone", None) if contractor else None,
            "needs_rating": False,
            "needs_photos": requires_photos and wr.status == "draft",
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
@require_GET
def collections_list(request):
    """Список инвайтов жителя в сборы."""
    try:
        from database.models import InviteStatus, ReceiptStatus, ServiceInvite

        invites = (
            ServiceInvite.objects.filter(user=request.bot_user)
            .exclude(status=InviteStatus.CANCELLED)
            .select_related("campaign")
            .prefetch_related(
                "campaign__offer_photos",
                "campaign__invites",
                "receipts",
            )
            .order_by("-id")[:50]
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
                    **_campaign_progress_payload(camp),
                    **pay,
                }
            )
        return json_response({"items": items})
    except Exception:
        return json_response({"items": []})


@api_login_required
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
def work_request_cancel(request, pk: int):
    """Клиент отменяет свою заявку (поиск мастера / ранняя стадия)."""
    from services.work_request_cancel import cancel_client_work_request

    wr = WorkRequest.objects.filter(pk=pk, user=request.bot_user).first()
    if not wr:
        return json_response({"error": "not_found"}, status=404)
    try:
        cancel_client_work_request(
            request.bot_user,
            wr,
            note="Отменено клиентом в приложении.",
        )
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=400)
    wr.refresh_from_db()
    return json_response({"ok": True, "id": wr.id, "status": wr.status})


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
        after_raw = (request.GET.get("after_id") or "").strip()
        before_raw = (request.GET.get("before_id") or "").strip()
        after_id = int(after_raw) if after_raw.isdigit() else None
        before_id = int(before_raw) if before_raw.isdigit() else None
        try:
            items = list_messages(
                request.bot_user,
                group,
                after_id=after_id,
                before_id=before_id,
                request=request,
            )
        except Exception:
            return json_response({"items": []})
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
        return json_response({"group": {"id": group.id, "name": group.name or ""}, "items": items})

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
