"""Helpers for safe redirects and input hardening."""

from __future__ import annotations

from urllib.parse import urlparse

from django.http import HttpRequest


def safe_redirect_target(raw: str | None, *, fallback: str) -> str:
    """
    Allow only relative same-site paths.
    Reject open redirects like //evil.com or https://evil.com.
    """
    if not raw or not isinstance(raw, str):
        return fallback
    target = raw.strip()
    if not target.startswith("/") or target.startswith("//"):
        return fallback
    # Block scheme-relative and backslash tricks
    if "\\" in target or "://" in target:
        return fallback
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return fallback
    return target


def redirect_after_post(request: HttpRequest, *, fallback: str):
    from django.shortcuts import redirect

    return redirect(safe_redirect_target(request.POST.get("next"), fallback=fallback))
