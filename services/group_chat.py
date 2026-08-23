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


def _household_root(uid: int, family_payer_by_id: dict[int, int | None]) -> int:
    """Корень домохозяйства: family_payer, иначе сам пользователь."""
    payer = family_payer_by_id.get(int(uid))
    return int(payer) if payer else int(uid)


def _family_payer_map_for_group(group: ServiceGroup) -> dict[int, int | None]:
    """
    id → family_payer_id для участников группы и связанных членов семьи.

    values() вместо only() — стабильно тянет FK даже через M2M.
    """
    member_rows = list(group.members.values("id", "family_payer_id"))
    member_ids = {int(r["id"]) for r in member_rows}
    payer_ids = {
        int(r["family_payer_id"]) for r in member_rows if r.get("family_payer_id")
    }
    # Иждивенцы участников (на случай, если кто-то из семьи ещё не в members,
    # но уже оплатил / нужен для корня).
    dep_rows = list(
        BotUser.objects.filter(family_payer_id__in=member_ids).values(
            "id", "family_payer_id"
        )
    ) if member_ids else []
    payer_rows = list(
        BotUser.objects.filter(id__in=payer_ids).values("id", "family_payer_id")
    ) if payer_ids else []

    family_payer_by_id: dict[int, int | None] = {}
    for row in (*member_rows, *dep_rows, *payer_rows):
        uid = int(row["id"])
        fp = row.get("family_payer_id")
        family_payer_by_id[uid] = int(fp) if fp else None
    return family_payer_by_id


def author_paid_map(group: ServiceGroup, campaigns: list[ServiceCampaign] | None = None) -> dict[str, list[bool]]:
    """
    author_id → список bool по активным сборам (True = семья/участник оплатил).

    Оплата по домохозяйству: если родитель (family_payer) или любой член семьи
    оплатил сбор — у всех из этой семьи в чате зелёный кружок.
    """
    if campaigns is None:
        campaigns = active_collections_for_group(group)
    if not campaigns:
        return {}

    campaign_ids = [int(c.id) for c in campaigns]
    paid_user_ids_by_campaign: dict[int, set[int]] = {cid: set() for cid in campaign_ids}
    for uid, cid in ServiceInvite.objects.filter(
        campaign_id__in=campaign_ids,
        status=InviteStatus.PAID,
    ).values_list("user_id", "campaign_id"):
        paid_user_ids_by_campaign.setdefault(int(cid), set()).add(int(uid))

    family_payer_by_id = _family_payer_map_for_group(group)
    member_ids = set(group.members.values_list("id", flat=True))
    # Догрузим family_payer для тех, кто оплатил, но ещё не в карте.
    missing_paid = {
        uid
        for paid in paid_user_ids_by_campaign.values()
        for uid in paid
        if uid not in family_payer_by_id
    }
    if missing_paid:
        for row in BotUser.objects.filter(id__in=missing_paid).values(
            "id", "family_payer_id"
        ):
            fp = row.get("family_payer_id")
            family_payer_by_id[int(row["id"])] = int(fp) if fp else None

    paid_roots_by_campaign: dict[int, set[int]] = {cid: set() for cid in campaign_ids}
    for cid, paid_uids in paid_user_ids_by_campaign.items():
        paid_roots_by_campaign[cid] = {
            _household_root(uid, family_payer_by_id) for uid in paid_uids
        }

    result: dict[str, list[bool]] = {}
    for uid in member_ids:
        uid = int(uid)
        root = _household_root(uid, family_payer_by_id)
        result[str(uid)] = [
            root in paid_roots_by_campaign.get(cid, set())
            or uid in paid_user_ids_by_campaign.get(cid, set())
            for cid in campaign_ids
        ]
    return result


def attach_payment_dots(items: list[dict], author_paid: dict[str, list[bool]]) -> list[dict]:
    """Проставить payment_dots на каждое сообщение (дубль author_paid для клиента)."""
    for item in items:
        aid = item.get("author_id")
        if aid is None:
            item["payment_dots"] = []
        else:
            item["payment_dots"] = list(author_paid.get(str(aid), []))
    return items


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
