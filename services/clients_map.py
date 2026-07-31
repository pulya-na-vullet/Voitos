"""Клиентская карта: графы групп, родственники в одной вершине."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import cos, pi, sin
from typing import Any

from django.db.models import Prefetch

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
        # unique by id (payer may appear twice when expanded)
        uniq: dict[int, BotUser] = {}
        for m in members:
            uniq[int(m.id)] = m
        ordered = sorted(
            uniq.values(),
            key=lambda u: (0 if int(u.id) == root_id else 1, _display_name(u).lower(), int(u.id)),
        )
        labels = [_display_name(u) for u in ordered]
        households[root_id] = {
            "id": f"h{root_id}",
            "root_id": root_id,
            "members": ordered,
            "label": " · ".join(labels),
            "labels": labels,
            "member_count": len(ordered),
            "has_family": len(ordered) > 1,
            "active_subscription": any(u.has_feature_access() for u in ordered),
        }
    return households


def _layout_ring(
    center_x: float,
    center_y: float,
    radius: float,
    count: int,
) -> list[tuple[float, float]]:
    if count <= 0:
        return []
    if count == 1:
        return [(center_x + radius, center_y)]
    coords: list[tuple[float, float]] = []
    for i in range(count):
        angle = -pi / 2 + (2 * pi * i) / count
        coords.append((center_x + radius * cos(angle), center_y + radius * sin(angle)))
    return coords


def build_group_graph(
    group: MapGroup,
    users: list[BotUser],
    *,
    width: float = 640,
    height: float = 420,
) -> dict[str, Any]:
    households_map = build_households(users)
    households = sorted(
        households_map.values(),
        key=lambda h: (-h["member_count"], h["label"].lower(), h["root_id"]),
    )

    cx, cy = width / 2, height / 2 + 8
    hub_r = 46
    node_r = 38 if len(households) <= 8 else 32
    ring_r = min(width, height) * 0.34
    if len(households) > 10:
        ring_r = min(width, height) * 0.38

    coords = _layout_ring(cx, cy, ring_r, len(households))
    hub_id = f"g{group.id if group.id is not None else 'none'}"
    nodes_by_id: dict[str, dict[str, Any]] = {
        hub_id: {
            "id": hub_id,
            "kind": "group",
            "label": group.name,
            "x": cx,
            "y": cy,
            "r": hub_r,
        }
    }
    edge_specs: list[tuple[str, str, str]] = []

    for household, (x, y) in zip(households, coords):
        labels = household["labels"]
        # Смещение первой строки подписи, чтобы блок имён был по центру круга.
        label_start_dy = 0.35 - 0.55 * (len(labels) - 1)
        nodes_by_id[household["id"]] = {
            "id": household["id"],
            "kind": "household",
            "label": household["label"],
            "labels": labels,
            "label_start_dy": f"{label_start_dy:.2f}em",
            "member_count": household["member_count"],
            "has_family": household["has_family"],
            "active_subscription": household["active_subscription"],
            "root_id": household["root_id"],
            "x": round(x, 2),
            "y": round(y, 2),
            "r": node_r + (4 if household["has_family"] else 0),
        }
        edge_specs.append((hub_id, household["id"], "member"))

    # Кольцо между соседними домохозяйствами — визуально «одна группа».
    if len(households) >= 2:
        for i in range(len(households)):
            a = households[i]["id"]
            b = households[(i + 1) % len(households)]["id"]
            edge_specs.append((a, b, "ring"))

    edges: list[dict[str, Any]] = []
    for source_id, target_id, kind in edge_specs:
        src = nodes_by_id[source_id]
        tgt = nodes_by_id[target_id]
        edges.append(
            {
                "source": source_id,
                "target": target_id,
                "kind": kind,
                "x1": src["x"],
                "y1": src["y"],
                "x2": tgt["x"],
                "y2": tgt["y"],
            }
        )

    # hub first, then households in ring order
    nodes = [nodes_by_id[hub_id]] + [
        nodes_by_id[h["id"]] for h in households
    ]

    return {
        "group_id": group.id,
        "group_name": group.name,
        "width": width,
        "height": height,
        "household_count": len(households),
        "user_count": sum(h["member_count"] for h in households),
        "nodes": nodes,
        "edges": edges,
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
        graphs.append(
            build_group_graph(
                MapGroup(id=int(group.id), name=group.name),
                users,
            )
        )

    orphan_qs = BotUser.objects.filter(is_active=True).exclude(id__in=seen_user_ids)
    orphan_users = list(
        orphan_qs.select_related("family_payer").order_by("id")[:200]
    )
    if orphan_users:
        users = _expand_with_family(orphan_users)
        # Не тянуть в «Без группы» тех, кто уже в графах групп.
        users = [u for u in users if int(u.id) not in seen_user_ids]
        if users:
            graphs.append(
                build_group_graph(
                    MapGroup(id=None, name="Без группы"),
                    users,
                )
            )

    return graphs
