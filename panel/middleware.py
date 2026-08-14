"""Ограничение маршрутов панели по роли менеджера."""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from panel.roles import (
    ensure_panel_profile,
    is_panel_manager,
    manager_url_allowed,
    panel_home_url_name,
)


class PanelRoleMiddleware:
    """Менеджер видит только разрешённые разделы панели."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or ""
        if (
            path.startswith("/panel/")
            and not path.startswith("/panel/login")
            and getattr(request, "user", None) is not None
            and request.user.is_authenticated
        ):
            ensure_panel_profile(request.user)
            if is_panel_manager(request.user):
                try:
                    match = resolve(path)
                except Resolver404:
                    match = None
                url_name = match.url_name if match and match.namespace == "panel" else None
                if not manager_url_allowed(url_name):
                    messages.error(
                        request,
                        "Этот раздел доступен только администратору.",
                    )
                    return redirect(panel_home_url_name(request.user))
        return self.get_response(request)
