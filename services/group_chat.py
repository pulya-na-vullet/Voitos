"""Групповой чат соседских ServiceGroup и аватар жителя."""

from __future__ import annotations

import io
import logging

from django.core.files.base import ContentFile
from django.db.models import Count
from PIL import Image

from api.media import unique_upload_filename
from database.models import BotUser, GroupChatMessage, ServiceGroup

logger = logging.getLogger(__name__)

AVATAR_SIZE = 500
MAX_CHAT_TEXT = 2000
CHAT_PAGE = 80


def groups_for_user(user: BotUser) -> list[ServiceGroup]:
    return list(
        ServiceGroup.objects.filter(members=user)
        .annotate(_members_total=Count("members"))
        .order_by("name", "id")
    )


def group_to_dict(group: ServiceGroup) -> dict:
    total = getattr(group, "_members_total", None)
    if total is None:
        total = group.members.count()
    return {
        "id": group.id,
        "name": group.name or "",
        "description": (group.description or "")[:500],
        "member_count": int(total or 0),
    }


def require_group_member(user: BotUser, group_id: int) -> ServiceGroup:
    try:
        gid = int(group_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректная группа.") from exc
    group = (
        ServiceGroup.objects.filter(pk=gid, members=user)
        .annotate(_members_total=Count("members"))
        .first()
    )
    if group is None:
        raise ValueError("Вы не состоите в этой группе.")
    return group


def message_to_dict(msg: GroupChatMessage, *, request=None, viewer: BotUser | None = None) -> dict:
    author = msg.author
    avatar_url = ""
    if request is not None and author is not None and getattr(author, "avatar", None):
        try:
            url = author.avatar.url
            if url:
                if str(url).startswith("http://") or str(url).startswith("https://"):
                    avatar_url = str(url)
                else:
                    avatar_url = request.build_absolute_uri(url)
        except Exception:
            avatar_url = ""
    return {
        "id": msg.id,
        "group_id": msg.group_id,
        "text": msg.text or "",
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
        "author_id": author.id if author is not None else None,
        "author_name": str(author) if author else "",
        "author_avatar_url": avatar_url,
        "is_mine": bool(viewer and author and author.id == viewer.id),
    }


def list_messages(
    user: BotUser,
    group: ServiceGroup,
    *,
    after_id: int | None = None,
    before_id: int | None = None,
    limit: int = CHAT_PAGE,
    request=None,
) -> list[dict]:
    limit = max(1, min(int(limit or CHAT_PAGE), 100))
    qs = GroupChatMessage.objects.filter(group=group).select_related("author")
    if after_id is not None:
        qs = qs.filter(id__gt=int(after_id)).order_by("id")
        rows = list(qs[:limit])
    elif before_id is not None:
        qs = qs.filter(id__lt=int(before_id)).order_by("-id")
        rows = list(reversed(list(qs[:limit])))
    else:
        qs = qs.order_by("-id")
        rows = list(reversed(list(qs[:limit])))
    items = []
    for msg in rows:
        items.append(message_to_dict(msg, request=request, viewer=user))
    return items


def post_message(user: BotUser, group: ServiceGroup, text: str, *, request=None) -> dict:
    text = (text or "").strip()
    if len(text) < 1:
        raise ValueError("Введите сообщение.")
    if len(text) > MAX_CHAT_TEXT:
        raise ValueError(f"Сообщение слишком длинное (макс. {MAX_CHAT_TEXT}).")
    msg = GroupChatMessage.objects.create(group=group, author=user, text=text)
    return message_to_dict(msg, request=request, viewer=user)


def process_avatar_bytes(raw: bytes) -> bytes:
    """Центральный кроп в квадрат и ресайз до 500×500 JPEG."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        raise ValueError("Не удалось прочитать изображение. Выберите JPG или PNG.") from exc
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGBA")
    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if w < 1 or h < 1:
        raise ValueError("Некорректное изображение.")
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue()


def save_user_avatar(user: BotUser, raw: bytes, *, filename: str = "avatar.jpg") -> BotUser:
    processed = process_avatar_bytes(raw)
    name = unique_upload_filename(filename, default_ext="jpg")
    if not name.lower().endswith((".jpg", ".jpeg")):
        name = f"{name.rsplit('.', 1)[0]}.jpg"
    if user.avatar:
        try:
            user.avatar.delete(save=False)
        except Exception:
            logger.exception("Failed to delete old avatar for user %s", user.id)
    user.avatar.save(name, ContentFile(processed), save=True)
    return user
