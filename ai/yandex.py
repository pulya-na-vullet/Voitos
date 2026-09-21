from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

from ai.base import ChatMessageDTO, CompletionResult, LLMProvider

logger = logging.getLogger(__name__)

# Names that are NOT Yandex Foundation Models — auto-map to a working default.
INVALID_MODEL_ALIASES = {
    "deepseek": "yandexgpt-lite",
    "deepseek-chat": "yandexgpt-lite",
    "deepseek-coder": "yandexgpt-lite",
    "gpt-4": "yandexgpt",
    "gpt-4o": "yandexgpt",
    "gpt-3.5-turbo": "yandexgpt-lite",
    "chatgpt": "yandexgpt-lite",
    "openai": "yandexgpt-lite",
}

ALLOWED_PREFIXES = ("yandexgpt", "summarization", "translator")


def normalize_yandex_model(model: str | None) -> str:
    raw = (model or "").strip()
    if not raw:
        return "yandexgpt-lite"

    # User pasted full URI: gpt://folder/model/latest
    if raw.startswith("gpt://"):
        parts = raw[len("gpt://") :].strip("/").split("/")
        if len(parts) >= 2:
            raw = parts[1]
        elif parts:
            raw = parts[0]

    key = raw.lower()
    if key in INVALID_MODEL_ALIASES:
        fixed = INVALID_MODEL_ALIASES[key]
        logger.warning("Model '%s' is not a YandexGPT model — using '%s'", raw, fixed)
        return fixed

    if not key.startswith(ALLOWED_PREFIXES) and key not in {"yandexgpt-lite", "yandexgpt", "yandexgpt-5-pro"}:
        logger.warning("Unusual Yandex model '%s' — falling back to yandexgpt-lite", raw)
        return "yandexgpt-lite"

    return raw


class YandexGPTProvider(LLMProvider):
    name = "yandexgpt"

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        model: str = "yandexgpt-lite",
        endpoint: str | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.folder_id = folder_id.strip()
        self.model = normalize_yandex_model(model)
        self.endpoint = endpoint or settings.YANDEX_LLM_URL
        if not self.folder_id:
            raise ValueError("Yandex Folder ID пустой — укажите его в настройках панели.")

    @property
    def model_uri(self) -> str:
        # Official format: gpt://<folder_id>/<model>/latest
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
                "maxTokens": int(max_tokens),
            },
            "messages": [{"role": m.role, "text": m.text} for m in messages],
        }
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
            "x-folder-id": self.folder_id,
        }
        logger.info("YandexGPT request modelUri=%s", self.model_uri)
        response = requests.post(self.endpoint, json=payload, headers=headers, timeout=60)
        if response.status_code >= 400:
            logger.error("YandexGPT error %s: %s", response.status_code, response.text[:500])
            # Friendlier message for common misconfig
            if response.status_code == 404 and "unknown model" in response.text.lower():
                raise RuntimeError(
                    f"Неизвестная модель YandexGPT ({self.model}). "
                    "В панели укажите yandexgpt-lite или yandexgpt."
                )
            response.raise_for_status()
        data = response.json()
        alternatives = data.get("result", {}).get("alternatives") or []
        text = ""
        if alternatives:
            text = alternatives[0].get("message", {}).get("text", "") or ""
        try:
            from ai.usage import log_llm_from_response

            log_llm_from_response(data, model_name=self.model)
        except Exception:
            logger.exception("Failed to record YandexGPT usage")
        return CompletionResult(text=text, raw=data)
