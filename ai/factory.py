from __future__ import annotations

import logging

from django.conf import settings

from ai.base import LLMProvider, SpeechToTextProvider
from ai.speech import YandexSpeechKitProvider
from ai.yandex import YandexGPTProvider, normalize_yandex_model
from database.models import AppSettings

logger = logging.getLogger(__name__)


class AINotConfiguredError(RuntimeError):
    pass


def repair_ai_settings(cfg: AppSettings | None = None) -> AppSettings:
    """Fix common misconfiguration (e.g. model=deepseek) and persist."""
    cfg = cfg or AppSettings.load()
    dirty = False

    fixed_model = normalize_yandex_model(cfg.yandex_model)
    if fixed_model != (cfg.yandex_model or "").strip():
        logger.warning("Repairing yandex_model: %r → %r", cfg.yandex_model, fixed_model)
        cfg.yandex_model = fixed_model
        dirty = True

    # Seed from env when DB fields are empty
    if not cfg.yandex_api_key and settings.YANDEX_API_KEY:
        cfg.yandex_api_key = settings.YANDEX_API_KEY
        dirty = True
    if not cfg.yandex_folder_id and settings.YANDEX_FOLDER_ID:
        cfg.yandex_folder_id = settings.YANDEX_FOLDER_ID
        dirty = True
    if not cfg.max_bot_token and settings.MAX_BOT_TOKEN:
        cfg.max_bot_token = settings.MAX_BOT_TOKEN
        dirty = True
    if not cfg.allowed_max_user_id and settings.ALLOWED_MAX_USER_ID:
        cfg.allowed_max_user_id = settings.ALLOWED_MAX_USER_ID
        dirty = True

    # Normalize folder id (strip spaces / accidental URI paste)
    folder = (cfg.yandex_folder_id or "").strip()
    if folder.startswith("gpt://"):
        # gpt://FOLDER/model/... → FOLDER
        folder = folder[len("gpt://") :].split("/")[0].strip()
        cfg.yandex_folder_id = folder
        dirty = True
    elif folder != (cfg.yandex_folder_id or ""):
        cfg.yandex_folder_id = folder
        dirty = True

    if dirty:
        cfg.save()
        logger.info(
            "AppSettings repaired: model=%s folder=%s",
            cfg.yandex_model,
            cfg.yandex_folder_id[:8] + "…" if cfg.yandex_folder_id else "(empty)",
        )
    return cfg


def _merged_settings() -> AppSettings:
    return repair_ai_settings(AppSettings.load())


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
