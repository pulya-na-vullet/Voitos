"""Клиентская карта: графы групп, родственники в одной вершине.

Вершина = семья / домохозяйство (плательщик + прикреплённые).
Название группы только в заголовке карточки — хаб-узел не рисуем.
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
    """root_id -> household dict with members (related users merged)."""
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


def build_group_graph(group: MapGroup, users: list[BotUser]) -> dict[str, Any] | None:
    """Данные одного графа группы в формате элементов Cytoscape.js."""
    households_map = build_households(users)
    households = sorted(
        households_map.values(),
        key=lambda h: (-h["member_count"], h["label"].lower(), h["root_id"]),
    )
    if not households:
        return None

    elements: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    for household in households:
        labels = household["labels"]
        cy_label = "\n".join(labels)
        href = reverse("panel:user_dashboard", args=[household["root_id"]])
        classes = ["household"]
        if household["has_family"]:
            classes.append("family")
        if household["is_paying"]:
            classes.append("paying")
        if household["active_subscription"]:
            classes.append("active-sub")
        elements.append(
            {
                "data": {
                    "id": household["id"],
                    "label": cy_label,
                    "kind": "household",
                    "href": href,
                    "root_id": household["root_id"],
                    "member_count": household["member_count"],
                    "is_paying": household["is_paying"],
                },
                "classes": " ".join(classes),
            }
        )
        nodes.append(
            {
                "id": household["id"],
                "kind": "household",
                "label": household["label"],
                "labels": labels,
                "has_family": household["has_family"],
                "member_count": household["member_count"],
                "root_id": household["root_id"],
                "is_paying": household["is_paying"],
            }
        )

    # Связи между семейными вершинами одной группы (без хаба с названием).
    n = len(households)
    if n >= 2:
        ring_steps = n if n > 2 else 1
        for i in range(ring_steps):
            a = households[i]["id"]
            b = households[(i + 1) % n]["id"]
            elements.append(
                {
                    "data": {
                        "id": f"e-ring-{a}-{b}",
                        "source": a,
                        "target": b,
                        "kind": "ring",
                    },
                    "classes": "ring",
                }
            )

    payer_count = sum(1 for h in households if h["is_paying"])
    return {
        "group_id": group.id,
        "group_name": group.name,
        "vertex_count": len(households),
        "payer_count": payer_count,
        "household_count": len(households),
        "user_count": sum(h["member_count"] for h in households),
        "nodes": nodes,
        "elements": elements,
    }


def _expand_with_family(users: list[BotUser]) -> list[BotUser]:
    """Include family payers and dependents so relatives share one vertex."""
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


def build_clients_map() -> list[dict[str, Any]]:
    """Все группы сервисов как отдельные графы + блок без группы."""
    groups = list(
        ServiceGroup.objects.prefetch_related(
            Prefetch(
                "members",
                queryset=BotUser.objects.select_related("family_payer").order_by("id"),
            )
        ).order_by("name", "id")
    )

    graphs: list[dict[str, Any]] = []
    seen_user_ids: set[int] = set()

    for group in groups:
        members = list(group.members.all())
        if not members:
            continue
        users = _expand_with_family(members)
        for u in users:
            seen_user_ids.add(int(u.id))
        graph = build_group_graph(
            MapGroup(id=int(group.id), name=group.name),
            users,
        )
        if graph:
            graphs.append(graph)

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
            )
            if graph:
                graphs.append(graph)

    return graphs
