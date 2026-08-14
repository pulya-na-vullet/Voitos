"""Контекст роли для шаблонов панели."""

from __future__ import annotations

from panel.roles import ensure_panel_profile, is_panel_admin, is_panel_manager, manager_groups_qs


def panel_role(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {
            "panel_is_admin": False,
            "panel_is_manager": False,
            "panel_role_label": "",
            "panel_managed_groups": [],
        }
    profile = ensure_panel_profile(user)
    admin = is_panel_admin(user)
    manager = is_panel_manager(user)
    label = ""
    if profile:
        label = profile.get_role_display()
    elif admin:
        label = "Администратор"
    managed = []
    if manager:
        managed = list(manager_groups_qs(user).order_by("name"))
    return {
        "panel_is_admin": admin,
        "panel_is_manager": manager,
        "panel_role_label": label,
        "panel_managed_groups": managed,
    }
