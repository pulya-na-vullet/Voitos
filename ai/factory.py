from __future__ import annotations

from django.conf import settings

from ai.base import LLMProvider, SpeechToTextProvider
from ai.speech import YandexSpeechKitProvider
from ai.yandex import YandexGPTProvider
from database.models import AppSettings


class AINotConfiguredError(RuntimeError):
    pass


def _merged_settings() -> AppSettings:
    cfg = AppSettings.load()
    # Env overrides win when DB fields are empty — handy for first boot.
    if not cfg.yandex_api_key and settings.YANDEX_API_KEY:
        cfg.yandex_api_key = settings.YANDEX_API_KEY
    if not cfg.yandex_folder_id and settings.YANDEX_FOLDER_ID:
        cfg.yandex_folder_id = settings.YANDEX_FOLDER_ID
    if (not cfg.yandex_model or cfg.yandex_model == "yandexgpt-lite") and settings.YANDEX_MODEL:
        cfg.yandex_model = settings.YANDEX_MODEL
    if not cfg.max_bot_token and settings.MAX_BOT_TOKEN:
        cfg.max_bot_token = settings.MAX_BOT_TOKEN
    if not cfg.allowed_max_user_id and settings.ALLOWED_MAX_USER_ID:
        cfg.allowed_max_user_id = settings.ALLOWED_MAX_USER_ID
    return cfg


def get_runtime_settings() -> AppSettings:
    return _merged_settings()


def get_llm_provider() -> LLMProvider:
    cfg = get_runtime_settings()
    if not cfg.yandex_api_key or not cfg.yandex_folder_id:
        raise AINotConfiguredError(
            "Yandex AI не настроен. Откройте панель администратора и укажите API Key и Folder ID."
        )
    return YandexGPTProvider(
        api_key=cfg.yandex_api_key,
        folder_id=cfg.yandex_folder_id,
        model=cfg.yandex_model or "yandexgpt-lite",
    )


def get_stt_provider() -> SpeechToTextProvider:
    cfg = get_runtime_settings()
    if not cfg.yandex_api_key:
        raise AINotConfiguredError(
            "Yandex AI не настроен. Откройте панель администратора и укажите API Key."
        )
    return YandexSpeechKitProvider(
        api_key=cfg.yandex_api_key,
        folder_id=cfg.yandex_folder_id,
    )
