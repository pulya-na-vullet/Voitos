from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

from ai.base import ChatMessageDTO, CompletionResult, LLMProvider

logger = logging.getLogger(__name__)


class YandexGPTProvider(LLMProvider):
    name = "yandexgpt"

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        model: str = "yandexgpt-lite",
        endpoint: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.folder_id = folder_id
        self.model = model or "yandexgpt-lite"
        self.endpoint = endpoint or settings.YANDEX_LLM_URL

    @property
    def model_uri(self) -> str:
        return f"gpt://{self.folder_id}/{self.model}/latest"

    def complete(
        self,
        messages: list[ChatMessageDTO],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> CompletionResult:
        payload: dict[str, Any] = {
            "modelUri": self.model_uri,
            "completionOptions": {
                "stream": False,
                "temperature": temperature,
                "maxTokens": max_tokens,
            },
            "messages": [{"role": m.role, "text": m.text} for m in messages],
        }
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
            "x-folder-id": self.folder_id,
        }
        response = requests.post(self.endpoint, json=payload, headers=headers, timeout=60)
        if response.status_code >= 400:
            logger.error("YandexGPT error %s: %s", response.status_code, response.text[:500])
            response.raise_for_status()
        data = response.json()
        alternatives = data.get("result", {}).get("alternatives") or []
        text = ""
        if alternatives:
            text = alternatives[0].get("message", {}).get("text", "") or ""
        return CompletionResult(text=text, raw=data)
