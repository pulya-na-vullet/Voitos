"""Групповой чат соседских ServiceGroup и аватар жителя."""

from __future__ import annotations

import io
import logging

from django.core.files.base import ContentFile
from django.db.models import Count
from PIL import Image

from api.media import unique_upload_filename
from database.models import (
    BotUser,
    CampaignStatus,
    GroupChatMessage,
    GroupChatReadState,
    InviteStatus,
    ServiceCampaign,
    ServiceGroup,
    ServiceInvite,
)

logger = logging.getLogger(__name__)

AVATAR_SIZE = 500
MAX_CHAT_TEXT = 2000
CHAT_PAGE = 80
# Совпадает с лимитом активных сборов в collections_list.
ACTIVE_COLLECTION_LIMIT = 4


def active_collections_for_group(group: ServiceGroup) -> list[ServiceCampaign]:
    """Активные сборы группы (стабильный порядок: старые слева), макс. 4."""
    return list(
        ServiceCampaign.objects.filter(
            group=group,
            status=CampaignStatus.ACTIVE,
        ).order_by("created_at", "id")[:ACTIVE_COLLECTION_LIMIT]
    )


def author_paid_map(group: ServiceGroup, campaigns: list[ServiceCampaign] | None = None) -> dict[str, list[bool]]:
    """
    author_id → список bool по активным сборам (True = семья/участник оплатил).

    Оплата считается по домохозяйству: если плательщик (родитель) оплатил сбор,
    у иждивенцев в чате тоже зелёные кружки — «семья оплатила».
    """
    if campaigns is None:
        campaigns = active_collections_for_group(group)
    if not campaigns:
        return {}

    campaign_ids = [c.id for c in campaigns]
    paid_user_ids_by_campaign: dict[int, set[int]] = {cid: set() for cid in campaign_ids}
    for uid, cid in ServiceInvite.objects.filter(
        campaign_id__in=campaign_ids,
        status=InviteStatus.PAID,
    ).values_list("user_id", "campaign_id"):
        paid_user_ids_by_campaign.setdefault(cid, set()).add(int(uid))

    members = list(group.members.only("id", "family_payer_id"))
    # Корень семьи → участники группы из этой семьи.
    household_members: dict[int, set[int]] = {}
    member_root: dict[int, int] = {}
    for m in members:
        root = int(m.family_payer_id) if m.family_payer_id else int(m.id)
        member_root[int(m.id)] = root
        household_members.setdefault(root, set()).add(int(m.id))

    # Для каждого сбора: корни семей, у которых кто-то из семьи (в т.ч. родитель) оплатил.
    paid_roots_by_campaign: dict[int, set[int]] = {cid: set() for cid in campaign_ids}
    for cid, paid_uids in paid_user_ids_by_campaign.items():
        roots: set[int] = set()
        for uid in paid_uids:
            if uid in member_root:
                roots.add(member_root[uid])
            else:
                # Плательщик мог оплатить, не будучи в members prefetch — подтянем корень.
                payer = BotUser.objects.filter(pk=uid).only("id", "family_payer_id").first()
                if payer is not None:
                    roots.add(
                        int(payer.family_payer_id)
                        if payer.family_payer_id
                        else int(payer.id)
                    )
                else:
                    roots.add(int(uid))
        paid_roots_by_campaign[cid] = roots

    result: dict[str, list[bool]] = {}
    for m in members:
        uid = int(m.id)
        root = member_root[uid]
        result[str(uid)] = [
            root in paid_roots_by_campaign.get(cid, set())
            or uid in paid_user_ids_by_campaign.get(cid, set())
            for cid in campaign_ids
        ]
    return result


def collection_payment_overlay(group: ServiceGroup) -> dict:
    campaigns = active_collections_for_group(group)
    return {
        "active_collections": [
            {
                "id": c.id,
                "title": (c.title or "").strip() or f"Сбор #{c.id}",
            }
            for c in campaigns
        ],
        "author_paid": author_paid_map(group, campaigns),
    }


def groups_for_user(user: BotUser) -> list[ServiceGroup]:
    return list(
        ServiceGroup.objects.filter(members=user)
        .annotate(_members_total=Count("members"))
        .order_by("name", "id")
    )


def unread_count_for_group(user: BotUser, group: ServiceGroup) -> int:
    last_read = (
        GroupChatReadState.objects.filter(user=user, group=group)
        .values_list("last_read_message_id", flat=True)
        .first()
    ) or 0
    return (
        GroupChatMessage.objects.filter(group=group, id__gt=last_read)
        .exclude(author=user)
        .count()
    )


def group_to_dict(group: ServiceGroup, *, user: BotUser | None = None) -> dict:
    total = getattr(group, "_members_total", None)
    if total is None:
        total = group.members.count()
    from services.service import group_accumulated_budget

    free_balance = float(group_accumulated_budget(group) or 0)
    payload = {
        "id": group.id,
        "name": group.name or "",
        "description": (group.description or "")[:500],
        "member_count": int(total or 0),
        "unread_count": 0,
        "free_balance": free_balance,
    }
    if user is not None:
        payload["unread_count"] = unread_count_for_group(user, group)
    return payload


def groups_payload_for_user(user: BotUser) -> dict:
    items = [group_to_dict(g, user=user) for g in groups_for_user(user)]
    return {
        "items": items,
        "unread_total": sum(int(i.get("unread_count") or 0) for i in items),
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


def mark_group_read(
    user: BotUser,
    group: ServiceGroup,
    *,
    last_read_message_id: int | None = None,
) -> GroupChatReadState:
    if last_read_message_id is None:
        last_id = (
            GroupChatMessage.objects.filter(group=group)
            .order_by("-id")
            .values_list("id", flat=True)
            .first()
        ) or 0
    else:
        try:
            last_id = max(0, int(last_read_message_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректный id сообщения.") from exc
        if last_id > 0 and not GroupChatMessage.objects.filter(
            group=group, id=last_id
        ).exists():
            # Не чужой id: берём максимум не выше запрошенного в этой группе
            last_id = (
                GroupChatMessage.objects.filter(group=group, id__lte=last_id)
                .order_by("-id")
                .values_list("id", flat=True)
                .first()
            ) or 0

    state, _ = GroupChatReadState.objects.get_or_create(
        user=user,
        group=group,
        defaults={"last_read_message_id": last_id},
    )
    if last_id > state.last_read_message_id:
        state.last_read_message_id = last_id
        state.save(update_fields=["last_read_message_id", "updated_at"])
    return state


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
    if author is not None:
        author_name = str(author).strip() or (author.phone or f"#{author.id}")
    else:
        author_name = "Удалённый пользователь"
    return {
        "id": msg.id,
        "group_id": msg.group_id,
        "text": msg.text or "",
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
        "author_id": author.id if author is not None else None,
        "author_name": author_name,
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
    # Свои сообщения сразу считаем прочитанными
    mark_group_read(user, group, last_read_message_id=msg.id)
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
