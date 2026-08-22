"""Django settings for Voitos MVP."""

from __future__ import annotations

import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, True),
    HOST=(str, "0.0.0.0"),
    PORT=(int, 18765),
    ALLOWED_HOSTS=(list, ["*"]),
)

environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="voitos-dev-secret-change-me")
DEBUG = env("DEBUG")
# В TestCase Django ставит DEBUG=False; для локальной OTP-отладки / тестов API:
MOBILE_OTP_DEBUG = env.bool("MOBILE_OTP_DEBUG", default=True)
# Shared secret for bot → POST /api/v1/auth/max/start (header X-Voitos-Internal).
AUTH_BOT_INTERNAL_TOKEN = env("AUTH_BOT_INTERNAL_TOKEN", default="")
# Fallback deep link to open MAX bot from the app.
MAX_BOT_OPEN_URL = env("MAX_BOT_OPEN_URL", default="")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "database",
    "panel",
    "bot",
    "ai",
    "memory",
    "reminders",
    "tasks",
    "logs",
    "subscriptions",
    "services",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "panel.middleware.PanelRoleMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "panel.context_processors.panel_role",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DUMPS_DIR = DATA_DIR / "dumps"
DUMPS_DIR.mkdir(exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "voitos.sqlite3",
        "OPTIONS": {
            "timeout": 30,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/panel/login/"
LOGIN_REDIRECT_URL = "/panel/"
LOGOUT_REDIRECT_URL = "/panel/login/"

YANDEX_VISION_URL = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"
YANDEX_OCR_URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"

LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOGS_DIR / "voitos.log",
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO",
    },
}

# Bootstrap admin credentials (created on first launch)
ADMIN_USERNAME = env("ADMIN_USERNAME", default="admin")
ADMIN_PASSWORD = env("ADMIN_PASSWORD", default="admin")
ADMIN_EMAIL = env("ADMIN_EMAIL", default="admin@localhost")

HOST = env("HOST")
PORT = env("PORT")

# Публичный URL стенда (https://voitos.example.com) — если задан, в MAX
# уходит он. Если пусто — локальный режим: http://LAN_IP:PORT + пометка про Wi‑Fi.
PANEL_PUBLIC_URL = env("PANEL_PUBLIC_URL", default="")

# Optional env overrides for runtime settings
MAX_BOT_TOKEN = env("MAX_BOT_TOKEN", default="")
ALLOWED_MAX_USER_ID = env("ALLOWED_MAX_USER_ID", default="")
YANDEX_API_KEY = env("YANDEX_API_KEY", default="")
YANDEX_FOLDER_ID = env("YANDEX_FOLDER_ID", default="")
YANDEX_MODEL = env("YANDEX_MODEL", default="yandexgpt-lite")

MAX_API_BASE_URL = "https://platform-api2.max.ru"
YANDEX_LLM_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"

# Mobile push (FCM Legacy). Пустой ключ + FCM_DRY_RUN=true → только лог.
FCM_SERVER_KEY = env("FCM_SERVER_KEY", default="")
FCM_DRY_RUN = env.bool("FCM_DRY_RUN", default=True)

# MAX uses TLS certificates from Минцифры. Voitos ships them under certs/.
# Set MAX_SSL_VERIFY=false only as a temporary workaround on broken Windows trust stores.
MAX_SSL_VERIFY = env.bool("MAX_SSL_VERIFY", default=True)
MAX_SSL_CA_BUNDLE = env("MAX_SSL_CA_BUNDLE", default="")

CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=[f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"],
)

# Waitress без nginx: раздавать media/static самим Django (отключить за reverse-proxy).
SERVE_MEDIA = env.bool("SERVE_MEDIA", default=True)
SERVE_STATIC = env.bool("SERVE_STATIC", default=True)

# Prod hardening when DEBUG=False (включайте HTTPS-флаги через env за TLS-прокси).
if not DEBUG:
    SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
    CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=False)
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
    if SECURE_HSTS_SECONDS:
        SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
            "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
        )
        SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    if SECRET_KEY == "voitos-dev-secret-change-me":
        import warnings

        warnings.warn(
            "DEBUG=False with default SECRET_KEY — set a strong SECRET_KEY in .env",
            RuntimeWarning,
            stacklevel=1,
        )
    if ADMIN_PASSWORD in {"admin", "password", "123456", ""}:
        import warnings

        warnings.warn(
            "DEBUG=False with weak ADMIN_PASSWORD — change ADMIN_PASSWORD in .env",
            RuntimeWarning,
            stacklevel=1,
        )
