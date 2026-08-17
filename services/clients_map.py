"""Клиентская карта: графы групп.

Вершина = житель. Члены семьи, привязанные по платежке, — рёбрами к плательщику.
Корневые вершины группы (не иждивенцы) связаны между собой кольцом.
Название группы — только в заголовке.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone

from database.models import BotUser, ServiceGroup


@dataclass(frozen=True)
class MapGroup:
    id: int | None
    name: str


def household_root_id(user: BotUser) -> int:
    if user.family_payer_id:
        return int(user.family_payer_id)
    return int(user.id)


def _display_name(user: BotUser) -> str:
    name = (user.real_name or user.display_name or user.username or "").strip()
    return name or f"id {user.id}"


def is_paying_user(user: BotUser) -> bool:
    """Плательщик: сам оплачивает подписку и срок ещё активен."""
    if user.family_payer_id:
        return False
    until = user.subscription_until
    return bool(until and until > timezone.now())


def build_households(users: list[BotUser]) -> dict[int, dict[str, Any]]:
    """root_id -> household dict with members (related users merged).

    Используется прогнозом и агрегациями; граф карты рисует людей отдельно.
    """
    by_id = {int(u.id): u for u in users}
    members_by_root: dict[int, list[BotUser]] = defaultdict(list)

    for user in users:
        root_id = household_root_id(user)
        if root_id not in by_id:
            root_id = int(user.id)
        members_by_root[root_id].append(user)

    households: dict[int, dict[str, Any]] = {}
    for root_id, members in members_by_root.items():
        uniq: dict[int, BotUser] = {}
        for m in members:
            uniq[int(m.id)] = m
        ordered = sorted(
            uniq.values(),
            key=lambda u: (0 if int(u.id) == root_id else 1, _display_name(u).lower(), int(u.id)),
        )
        root = by_id.get(root_id) or ordered[0]
        labels = [_display_name(u) for u in ordered]
        households[root_id] = {
            "id": f"h{root_id}",
            "root_id": root_id,
            "members": ordered,
            "label": " · ".join(labels),
            "labels": labels,
            "member_count": len(ordered),
            "has_family": len(ordered) > 1,
            "is_paying": is_paying_user(root),
            "active_subscription": any(u.has_feature_access() for u in ordered),
        }
    return households


def _person_node_id(user_id: int) -> str:
    return f"u{int(user_id)}"


def build_group_graph(
    group: MapGroup,
    users: list[BotUser],
    *,
    panel_role_bot_ids: set[int] | None = None,
) -> dict[str, Any] | None:
    """Данные одного графа группы в формате элементов Cytoscape.js."""
    panel_role_bot_ids = panel_role_bot_ids or set()
    by_id: dict[int, BotUser] = {}
    for u in users:
        by_id[int(u.id)] = u
    if not by_id:
        return None

    # Убедимся, что плательщики привязанных есть в графе (на случай неполного списка).
    for u in list(by_id.values()):
        payer_id = u.family_payer_id
        if payer_id and int(payer_id) not in by_id:
            payer = getattr(u, "family_payer", None)
            if payer is not None:
                by_id[int(payer.id)] = payer

    ordered_users = sorted(
        by_id.values(),
        key=lambda u: (_display_name(u).lower(), int(u.id)),
    )

    elements: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    root_ids: list[int] = []
    paying_count = 0

    for user in ordered_users:
        uid = int(user.id)
        is_dependent = bool(user.family_payer_id)
        is_paying = is_paying_user(user)
        has_dependents = any(
            int(o.family_payer_id or 0) == uid for o in ordered_users if int(o.id) != uid
        )
        label = _display_name(user)
        href = reverse("panel:user_dashboard", args=[uid])
        classes = ["person"]
        if is_dependent:
            classes.append("dependent")
        else:
            classes.append("root")
            root_ids.append(uid)
            if has_dependents:
                classes.append("family-parent")
        if is_paying:
            classes.append("paying")
            paying_count += 1
        if user.has_feature_access():
            classes.append("active-sub")
        is_panel_role = uid in panel_role_bot_ids
        if is_panel_role:
            classes.append("panel-role")

        node_id = _person_node_id(uid)
        elements.append(
            {
                "data": {
                    "id": node_id,
                    "label": label,
                    "kind": "person",
                    "href": href,
                    "user_id": uid,
                    "is_paying": is_paying,
                    "is_dependent": is_dependent,
                    "is_panel_role": is_panel_role,
                    "payer_id": int(user.family_payer_id) if user.family_payer_id else None,
                },
                "classes": " ".join(classes),
            }
        )
        nodes.append(
            {
                "id": node_id,
                "kind": "person",
                "label": label,
                "user_id": uid,
                "is_paying": is_paying,
                "is_dependent": is_dependent,
                "is_panel_role": is_panel_role,
            }
        )

    # Спицы семьи: зависимый → плательщик по платежке.
    for user in ordered_users:
        payer_id = user.family_payer_id
        if not payer_id:
            continue
        pid = int(payer_id)
        if pid not in by_id:
            continue
        uid = int(user.id)
        elements.append(
            {
                "data": {
                    "id": f"e-fam-{uid}-{pid}",
                    "source": _person_node_id(uid),
                    "target": _person_node_id(pid),
                    "kind": "family",
                },
                "classes": "family",
            }
        )

    # Кольцо между корневыми вершинами группы (не иждивенцы):
    # так грани видны даже без активной подписки.
    root_ids = sorted(set(root_ids))
    n_roots = len(root_ids)
    if n_roots >= 2:
        ring_steps = n_roots if n_roots > 2 else 1
        for i in range(ring_steps):
            a = root_ids[i]
            b = root_ids[(i + 1) % n_roots]
            elements.append(
                {
                    "data": {
                        "id": f"e-ring-{a}-{b}",
                        "source": _person_node_id(a),
                        "target": _person_node_id(b),
                        "kind": "ring",
                    },
                    "classes": "ring",
                }
            )

    households = build_households(list(by_id.values()))
    panel_role_count = sum(1 for n in nodes if n.get("is_panel_role"))
    return {
        "group_id": group.id,
        "group_name": group.name,
        "vertex_count": len(nodes),
        "payer_count": paying_count,
        "panel_role_count": panel_role_count,
        "household_count": len(households),
        "user_count": len(nodes),
        "nodes": nodes,
        "elements": elements,
    }


def _expand_with_family(users: list[BotUser]) -> list[BotUser]:
    """Include family payers and dependents so relatives appear together."""
    by_id: dict[int, BotUser] = {int(u.id): u for u in users}
    payer_ids = [u.family_payer_id for u in users if u.family_payer_id]
    if payer_ids:
        for payer in BotUser.objects.filter(id__in=payer_ids).select_related("family_payer"):
            by_id[int(payer.id)] = payer
    root_ids = list(by_id.keys())
    if root_ids:
        for dep in BotUser.objects.filter(family_payer_id__in=root_ids).select_related(
            "family_payer"
        ):
            by_id[int(dep.id)] = dep
    return list(by_id.values())


def _panel_role_bot_user_ids() -> set[int]:
    """BotUser id менеджеров и администраторов панели (связанных с жителем)."""
    from database.models import PanelProfile, PanelRole

    return set(
        PanelProfile.objects.filter(
            bot_user_id__isnull=False,
            role__in=[PanelRole.ADMIN, PanelRole.MANAGER],
        ).values_list("bot_user_id", flat=True)
    )


def build_clients_map(*, group_ids: list[int] | set[int] | None = None) -> list[dict[str, Any]]:
    """Все группы сервисов как отдельные графы + блок без группы."""
    panel_role_ids = _panel_role_bot_user_ids()
    groups_qs = ServiceGroup.objects.select_related(
        "manager", "manager__panel_profile"
    ).prefetch_related(
        Prefetch(
            "members",
            queryset=BotUser.objects.select_related("family_payer").order_by("id"),
        )
    ).order_by("name", "id")
    if group_ids is not None:
        groups_qs = groups_qs.filter(id__in=group_ids)
    groups = list(groups_qs)

    graphs: list[dict[str, Any]] = []
    seen_user_ids: set[int] = set()

    for group in groups:
        members = list(group.members.all())
        if not members:
            continue
        # Менеджер этой группы тоже подсвечивается, даже если не в members
        role_ids = set(panel_role_ids)
        profile = getattr(getattr(group, "manager", None), "panel_profile", None)
        if profile and profile.bot_user_id:
            role_ids.add(int(profile.bot_user_id))
        users = _expand_with_family(members)
        for u in users:
            seen_user_ids.add(int(u.id))
        graph = build_group_graph(
            MapGroup(id=int(group.id), name=group.name),
            users,
            panel_role_bot_ids=role_ids,
        )
        if graph:
            graphs.append(graph)

    # Блок «Без группы» только для администратора (полный обзор).
    if group_ids is None:
        orphan_users = list(
            BotUser.objects.filter(is_active=True)
            .exclude(id__in=seen_user_ids)
            .select_related("family_payer")
            .order_by("id")[:200]
        )
        if orphan_users:
            users = _expand_with_family(orphan_users)
            users = [u for u in users if int(u.id) not in seen_user_ids]
            if users:
                graph = build_group_graph(
                    MapGroup(id=None, name="Без группы"),
                    users,
                    panel_role_bot_ids=panel_role_ids,
                )
                if graph:
                    graphs.append(graph)

    return graphs
