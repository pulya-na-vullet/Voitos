from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class MaxApiError(RuntimeError):
    pass


class MaxClient:
    """Thin client for MAX Bot API (platform-api2.max.ru)."""

    def __init__(self, token: str, base_url: str | None = None) -> None:
        self.token = token
        self.base_url = (base_url or settings.MAX_API_BASE_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": token})

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        timeout = kwargs.pop("timeout", 60)
        response = self.session.request(method, self._url(path), timeout=timeout, **kwargs)
        if response.status_code >= 400:
            logger.error("MAX API %s %s -> %s: %s", method, path, response.status_code, response.text[:500])
            raise MaxApiError(f"MAX API error {response.status_code}: {response.text[:300]}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    def get_me(self) -> dict[str, Any]:
        return self._request("GET", "/me")

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
        # Long poll can wait up to `timeout` seconds
        return self._request("GET", "/updates", params=params, timeout=timeout + 15)

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
        response = self.session.get(url, timeout=60)
        if response.status_code >= 400:
            raise MaxApiError(f"Failed to download media: {response.status_code}")
        return response.content
