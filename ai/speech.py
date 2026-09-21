from __future__ import annotations

import logging

import requests
from django.conf import settings

from ai.base import SpeechToTextProvider

logger = logging.getLogger(__name__)


class YandexSpeechKitProvider(SpeechToTextProvider):
    name = "yandex-speechkit"

    def __init__(
        self,
        api_key: str,
        folder_id: str = "",
        endpoint: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.folder_id = folder_id
        self.endpoint = endpoint or settings.YANDEX_STT_URL

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        lang: str = "ru-RU",
        audio_format: str = "oggopus",
    ) -> str:
        params: dict[str, str] = {
            "lang": lang,
            "format": audio_format,
            "topic": "general",
        }
        if self.folder_id:
            params["folderId"] = self.folder_id

        headers = {"Authorization": f"Api-Key {self.api_key}"}
        response = requests.post(
            self.endpoint,
            params=params,
            data=audio_bytes,
            headers=headers,
            timeout=60,
        )
        if response.status_code >= 400:
            logger.error("SpeechKit error %s: %s", response.status_code, response.text[:500])
            response.raise_for_status()
        data = response.json()
        try:
            from ai.usage import log_stt_call

            log_stt_call(audio_format=audio_format)
        except Exception:
            logger.exception("Failed to record SpeechKit usage")
        return (data.get("result") or "").strip()
