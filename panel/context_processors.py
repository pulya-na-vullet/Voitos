"""Контекст роли для шаблонов панели."""

from __future__ import annotations

from django.conf import settings

from panel.roles import ensure_panel_profile, is_panel_admin, is_panel_manager, manager_groups_qs


def _version_context() -> dict:
    from services.app_version import mobile_version_payload

    payload = mobile_version_payload()
    backend = str(getattr(settings, "VOITOS_BACKEND_VERSION", "") or "").strip() or "—"
    mobile_name = (payload.get("latest_app_version_name") or "").strip() or "—"
    min_code = int(payload.get("min_app_version_code") or 0)
    latest_code = int(payload.get("latest_app_version_code") or 0)
    return {
        "voitos_backend_version": backend,
        "voitos_mobile_version_name": mobile_name,
        "voitos_mobile_min_code": min_code,
        "voitos_mobile_latest_code": latest_code,
    }


def panel_role(request):
    versions = _version_context()
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {
            "panel_is_admin": False,
            "panel_is_manager": False,
            "panel_role_label": "",
            "panel_managed_groups": [],
            **versions,
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
        **versions,
    }
