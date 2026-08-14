"""Заявка жителя: вызвать исполнителя (фото + описание)."""

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
    active_roles,
    extract_role_from_call_phrase,
    format_roles_list,
    match_role_from_text,
)

WORK_REQUEST_KIND = "work_request"


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
    if role is None:
        pending.pending_payload = {"step": "role"}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        return (
            "Кого вызвать?\n"
            + format_roles_list()
            + "\n\nНапишите номер или название роли."
        )
    pending.pending_payload = {"step": "description", "role_id": role.id}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return (
        f"Заявка: {role.name}.\n"
        "Опишите, что нужно сделать (текстом)."
    )


def handle_work_request_step(user: BotUser, text: str, pending: PendingAction) -> str:
    payload = dict(pending.pending_payload or {})
    step = payload.get("step") or "role"
    raw = (text or "").strip()

    if step == "role":
        role = match_role_from_text(raw)
        if not role:
            return "Не понял роль.\n\n" + format_roles_list()
        payload = {"step": "description", "role_id": role.id}
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        return f"Заявка: {role.name}.\nОпишите, что нужно сделать."

    if step == "description":
        if len(raw) < 5:
            return "Напишите чуть подробнее, что нужно сделать (хотя бы пару слов)."
        payload["description"] = raw[:4000]
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
            return "Ошибка заявки — начните снова: «вызвать исполнителя»."
        req = WorkRequest.objects.create(
            user=user,
            role=role,
            description=payload.get("description") or "—",
            status=WorkRequestStatus.PENDING,
        )
        payload["draft_id"] = req.id
        request_id = req.id
    else:
        req = WorkRequest.objects.filter(pk=request_id, user=user).first()
        if not req:
            pending.clear_pending()
            return "Заявка не найдена — начните снова."

    safe_name = (filename or "photo.jpg")[:120]
    photo = WorkRequestPhoto(request=req)
    photo.image.save(safe_name, ContentFile(image_bytes), save=True)
    photos.append(photo.id)
    payload["photos"] = photos
    pending.pending_payload = payload
    pending.save(update_fields=["pending_payload", "updated_at"])
    return (
        f"Фото добавлено ({len(photos)}). "
        "Можете прислать ещё или напишите «готово»."
    )


def _finish_if_possible(user: BotUser, pending: PendingAction, payload: dict) -> str:
    request_id = payload.get("draft_id")
    photos = payload.get("photos") or []
    if not request_id or not photos:
        return "Нужно хотя бы одно фото. Пришлите изображение, затем «готово»."
    req = WorkRequest.objects.select_related("role").filter(pk=request_id, user=user).first()
    if not req:
        pending.clear_pending()
        return "Заявка не найдена — начните снова."
    if payload.get("description"):
        req.description = payload["description"]
        req.save(update_fields=["description", "updated_at"])
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
    return (
        f"Заявка отправлена администратору.\n"
        f"Роль: {req.role.name}\n"
        f"Фото: {len(photos)}\n"
        "Мы свяжемся, когда подберём исполнителя."
    )
