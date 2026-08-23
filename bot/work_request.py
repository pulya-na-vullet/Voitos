"""Заявка жителя: вызвать исполнителя (описание + опционально фото)."""

from __future__ import annotations

from django.core.files.base import ContentFile

from database.models import (
    ActivityKind,
    ActivityLog,
    AdminTaskKind,
    BotUser,
    ExecutorRole,
    PendingAction,
    WorkRequest,
    WorkRequestPhoto,
    WorkRequestStatus,
)
from panel.admin_tasks import upsert_task
from services.executor_roles import (
    extract_role_from_call_phrase,
    format_roles_list,
    match_role_from_text,
)
from services.master_booking import role_has_local_masters, roles_for_client_call

WORK_REQUEST_KIND = "work_request"


def _roles_for_call(user: BotUser):
    return list(roles_for_client_call(user))


def _role_requires_photos(role: ExecutorRole | None) -> bool:
    if role is None:
        return True
    return bool(getattr(role, "requires_work_photos", True))


def start_work_request(
    user: BotUser,
    pending: PendingAction,
    *,
    role: ExecutorRole | None = None,
    text: str = "",
) -> str:
    pending.pending_kind = WORK_REQUEST_KIND
    if role is None and text:
        role = extract_role_from_call_phrase(text) or match_role_from_text(text)
    if role is not None and not role_has_local_masters(role, user):
        role = None
    if role is None:
        roles = _roles_for_call(user)
        if not roles:
            pending.clear_pending()
            return (
                "В вашем населённом пункте пока нет доступных мастеров.\n"
                "Напишите администратору или попробуйте позже."
            )
        pending.pending_payload = {"step": "role"}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        return (
            "Кого вызвать? Напишите номер:\n\n"
            + format_roles_list(roles)
        )
    pending.pending_payload = {"step": "description", "role_id": role.id}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return (
        f"Заявка: {role.name}.\n"
        "Кратко опишите, что нужно сделать."
    )


def handle_work_request_step(user: BotUser, text: str, pending: PendingAction) -> str:
    from services.work_request_cancel import maybe_cancel_work_flow

    cancelled = maybe_cancel_work_flow(user, text, pending)
    if cancelled is not None:
        return cancelled

    payload = dict(pending.pending_payload or {})
    step = payload.get("step") or "role"
    raw = (text or "").strip()

    if step == "role":
        roles = _roles_for_call(user)
        role = match_role_from_text(raw, roles)
        if not role:
            if not roles:
                pending.clear_pending()
                return (
                    "В вашем населённом пункте пока нет доступных мастеров.\n"
                    "Напишите администратору или попробуйте позже."
                )
            return (
                "Не понял номер.\n"
                "Напишите цифру из списка:\n\n"
                + format_roles_list(roles)
                + "\n\nНапример: 1"
            )
        payload = {"step": "description", "role_id": role.id}
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        return f"Заявка: {role.name}.\nОпишите, что нужно сделать."

    if step == "description":
        if len(raw) < 5:
            return "Напишите чуть подробнее, что нужно сделать (хотя бы пару слов)."
        payload["description"] = raw[:4000]
        role = ExecutorRole.objects.filter(pk=payload.get("role_id")).first()
        if not _role_requires_photos(role):
            # Фото для этой роли не нужны — сразу создаём и отправляем заявку.
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            return _finish_if_possible(user, pending, payload)
        payload["step"] = "photo"
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        return (
            "Пришлите фото объекта / места работ (можно несколько).\n"
            "Когда закончите — напишите «готово»."
        )

    if step == "photo":
        low = raw.lower()
        if low in {"готово", "готово.", "всё", "все", "далее", "хватит", "ок", "ok"}:
            return _finish_if_possible(user, pending, payload)
        return (
            "Жду фото. Пришлите изображение, либо напишите «готово», "
            "если фото уже отправили."
        )

    return start_work_request(user, pending)


def handle_work_request_photo(
    user: BotUser,
    pending: PendingAction,
    *,
    image_bytes: bytes,
    filename: str = "photo.jpg",
) -> str:
    payload = dict(pending.pending_payload or {})
    if payload.get("step") != "photo":
        return handle_work_request_step(user, "", pending)
    photos = list(payload.get("photos") or [])
    # сохраняем временно в payload как счётчик; байты кладём сразу в черновик заявки
    request_id = payload.get("draft_id")
    if not request_id:
        role = ExecutorRole.objects.filter(pk=payload.get("role_id")).first()
        if not role:
            pending.clear_pending()
            return "Ошибка заявки — начните снова: «вызвать мастера»."
        req = WorkRequest.objects.create(
            user=user,
            role=role,
            description=payload.get("description") or "—",
            status=WorkRequestStatus.DRAFT,
            client_locality=(user.locality or "").strip()[:255],
        )
        payload["draft_id"] = req.id
        request_id = req.id
    else:
        req = WorkRequest.objects.filter(pk=request_id, user=user).first()
        if not req:
            pending.clear_pending()
            return "Заявка не найдена — начните снова."

    safe_name = (filename or "photo.jpg")[:120]
    from api.media import unique_upload_filename

    store_name = unique_upload_filename(safe_name)
    photo = WorkRequestPhoto(request=req)
    photo.image.save(store_name, ContentFile(image_bytes), save=True)
    photos.append(photo.id)
    payload["photos"] = photos
    pending.pending_payload = payload
    pending.save(update_fields=["pending_payload", "updated_at"])
    real_count = WorkRequestPhoto.objects.filter(request=req).count()
    return (
        f"Фото добавлено ({real_count}). "
        "Можете прислать ещё или напишите «готово»."
    )


def _finish_if_possible(user: BotUser, pending: PendingAction, payload: dict) -> str:
    role = ExecutorRole.objects.filter(pk=payload.get("role_id")).first()
    requires_photos = _role_requires_photos(role)
    request_id = payload.get("draft_id")
    photos = payload.get("photos") or []
    if requires_photos and (not request_id or not photos):
        return "Нужно хотя бы одно фото. Пришлите изображение, затем «готово»."

    if not request_id:
        if not role:
            pending.clear_pending()
            return "Ошибка заявки — начните снова: «вызвать мастера»."
        req = WorkRequest.objects.create(
            user=user,
            role=role,
            description=payload.get("description") or "—",
            status=WorkRequestStatus.PENDING,
            client_locality=(user.locality or "").strip()[:255],
        )
    else:
        req = WorkRequest.objects.select_related("role").filter(
            pk=request_id, user=user
        ).first()
        if not req:
            pending.clear_pending()
            return "Заявка не найдена — начните снова."
        if payload.get("description"):
            req.description = payload["description"]
        # Черновик → новая заявка только после «готово»
        req.status = WorkRequestStatus.PENDING
        req.save(
            update_fields=["description", "status", "updated_at"]
            if payload.get("description")
            else ["status", "updated_at"]
        )

    pending.clear_pending()
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.WORK_REQUEST,
        title=f"Заявка на исполнителя: {req.role.name}",
        detail=req.description[:500],
        meta={"work_request_id": req.id, "role": req.role.code},
    )
    try:
        upsert_task(
            kind=AdminTaskKind.WORK_REQUEST,
            title=f"Заявка: {req.role.name} — {user}",
            description=req.description[:500],
            user=user,
            action_url=f"/panel/work-requests/{req.id}/",
            source_model="WorkRequest",
            source_id=req.id,
            priority=25,
        )
    except Exception:
        pass
    photo_n = req.photos.count() if request_id else 0
    if photos:
        photo_n = len(photos)
    photo_line = f"Фото: {photo_n}\n" if photo_n else ""
    # Автоподбор мастера по роли и населённому пункту
    try:
        from services.work_request_dispatch import try_dispatch_request

        if not (req.client_locality or "").strip() and (user.locality or "").strip():
            req.client_locality = user.locality.strip()[:255]
            req.save(update_fields=["client_locality", "updated_at"])
        offer = try_dispatch_request(req)
        if offer:
            return (
                f"Заявка отправлена.\n"
                f"Роль: {req.role.name}\n"
                f"{photo_line}"
                "Ищем мастера рядом — напишем, когда подтвердит."
            )
    except Exception:
        pass
    return (
        f"Заявка отправлена.\n"
        f"Роль: {req.role.name}\n"
        f"{photo_line}"
        "Напишем, когда найдём мастера."
    )
