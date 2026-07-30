from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

from bot.ssl_utils import apply_session_ssl

logger = logging.getLogger(__name__)


class MaxApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class MaxClient:
    """Thin client for MAX Bot API (platform-api2.max.ru)."""

    def __init__(self, token: str, base_url: str | None = None) -> None:
        self.token = token.strip()
        self.base_url = (base_url or settings.MAX_API_BASE_URL).rstrip("/")
        self.session = requests.Session()
        apply_session_ssl(self.session)
        # MAX expects the raw access token in Authorization (no Bearer prefix).
        self.session.headers.update(
            {
                "Authorization": self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "VoitosBot/0.1",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        timeout = kwargs.pop("timeout", 60)
        kwargs.setdefault("verify", self.session.verify)
        try:
            response = self.session.request(method, self._url(path), timeout=timeout, **kwargs)
        except requests.exceptions.SSLError as exc:
            logger.error(
                "SSL error talking to MAX. Certs of Минцифры are required. "
                "Voitos ships them in certs/. Or set MAX_SSL_VERIFY=false in .env as a temporary workaround. "
                "Details: %s",
                exc,
            )
            raise
        if response.status_code >= 400:
            logger.error(
                "MAX API %s %s -> %s: %s",
                method,
                path,
                response.status_code,
                response.text[:500],
            )
            raise MaxApiError(
                f"MAX API error {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
                body=response.text[:1000],
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    def get_me(self) -> dict[str, Any]:
        return self._request("GET", "/me")

    def get_subscriptions(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/subscriptions")
        if isinstance(data, list):
            return data
        return list(data.get("subscriptions") or [])

    def unsubscribe(self, url: str) -> dict[str, Any]:
        return self._request("DELETE", "/subscriptions", params={"url": url})

    def clear_webhooks(self) -> int:
        """Remove webhook subscriptions so long-polling can receive updates."""
        removed = 0
        try:
            subs = self.get_subscriptions()
        except MaxApiError as exc:
            logger.warning("Could not list subscriptions: %s", exc)
            return 0
        for sub in subs:
            url = (sub.get("url") or "").strip()
            if not url:
                continue
            try:
                self.unsubscribe(url)
                removed += 1
                logger.info("Removed MAX webhook subscription: %s", url)
            except MaxApiError:
                logger.exception("Failed to remove webhook %s", url)
        return removed

    def get_updates(
        self,
        *,
        marker: int | None = None,
        limit: int = 100,
        timeout: int = 30,
        types: list[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "timeout": timeout}
        if marker is not None:
            params["marker"] = marker
        if types:
            params["types"] = ",".join(types)
        return self._request("GET", "/updates", params=params, timeout=timeout + 20)

    def send_message(
        self,
        text: str,
        *,
        user_id: str | int | None = None,
        chat_id: str | int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if user_id is not None:
            params["user_id"] = user_id
        if chat_id is not None:
            params["chat_id"] = chat_id
        return self._request("POST", "/messages", params=params, json={"text": text})

    def download(self, url: str) -> bytes:
        response = self.session.get(url, timeout=60, verify=self.session.verify)
        if response.status_code >= 400:
            raise MaxApiError(f"Failed to download media: {response.status_code}")
        return response.content
