from __future__ import annotations

import io
import logging
import re
import time
from typing import Any

import requests
from django.conf import settings

from bot.ssl_utils import apply_session_ssl, ssl_verify_value

logger = logging.getLogger(__name__)

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


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
        attachments: list[dict[str, Any]] | None = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if user_id is not None:
            params["user_id"] = user_id
        if chat_id is not None:
            params["chat_id"] = chat_id
        body: dict[str, Any] = {"text": text or ""}
        if attachments:
            body["attachments"] = attachments
        last_exc: Exception | None = None
        for attempt in range(max(1, retries)):
            try:
                return self._request("POST", "/messages", params=params, json=body)
            except MaxApiError as exc:
                last_exc = exc
                # MAX may need a moment after upload before attachment is ready
                if "attachment.not.ready" in (exc.body or "") or "attachment.not.ready" in str(exc):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    def get_upload_url(self, media_type: str = "image") -> dict[str, Any]:
        return self._request("POST", "/uploads", params={"type": media_type})

    @staticmethod
    def _safe_upload_filename(filename: str, *, default: str = "photo.jpg") -> str:
        """CDN multipart rejects non-ASCII Content-Disposition filenames."""
        base = (filename or "").split("/")[-1].split("\\")[-1].strip() or default
        if "." in base:
            stem, ext = base.rsplit(".", 1)
            ext = _SAFE_FILENAME_RE.sub("", ext).lower() or "jpg"
        else:
            stem, ext = base, "jpg"
        stem = _SAFE_FILENAME_RE.sub("_", stem).strip("._") or "photo"
        return f"{stem[:60]}.{ext[:10]}"

    @staticmethod
    def _prepare_image_upload(image_bytes: bytes, filename: str) -> tuple[bytes, str, str]:
        """
        Normalize image for MAX CDN upload.

        Returns (bytes, ascii_filename, content_type). Prefer JPEG so the CDN
        always receives a supported raster payload.
        """
        safe_name = MaxClient._safe_upload_filename(filename)
        try:
            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as img:
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                elif img.mode == "L":
                    img = img.convert("RGB")
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=90, optimize=True)
                data = out.getvalue()
            stem = safe_name.rsplit(".", 1)[0] or "photo"
            return data, f"{stem}.jpg", "image/jpeg"
        except Exception:
            logger.debug("Could not normalize image with Pillow; uploading raw bytes", exc_info=True)
            lower = safe_name.lower()
            if lower.endswith(".png"):
                return image_bytes, safe_name, "image/png"
            if lower.endswith(".gif"):
                return image_bytes, safe_name, "image/gif"
            if lower.endswith(".webp"):
                return image_bytes, safe_name, "image/webp"
            if not lower.endswith((".jpg", ".jpeg")):
                safe_name = f"{safe_name.rsplit('.', 1)[0]}.jpg" if "." in safe_name else "photo.jpg"
            return image_bytes, safe_name, "image/jpeg"

    @staticmethod
    def _extract_image_token(data: Any) -> str:
        if isinstance(data, dict):
            if data.get("error_code") is not None or data.get("error_data"):
                raise MaxApiError(
                    f"MAX image upload rejected: {str(data)[:300]}",
                    body=str(data)[:1000],
                )
            token = data.get("token")
            if isinstance(token, str) and token.strip():
                return token.strip()
            photos = data.get("photos")
            if isinstance(photos, dict):
                for value in photos.values():
                    if isinstance(value, dict):
                        nested = value.get("token")
                        if isinstance(nested, str) and nested.strip():
                            return nested.strip()
            # Some responses nest under payload
            payload = data.get("payload")
            if isinstance(payload, dict):
                nested = payload.get("token")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
        raise MaxApiError(f"No image token in upload response: {str(data)[:300]}")

    def upload_image(self, image_bytes: bytes, filename: str = "photo.jpg") -> str:
        """Upload image bytes to MAX CDN and return attachment token.

        CDN upload must be plain multipart without Authorization / JSON headers.
        Official MAX docs and the Go client send only Content-Type: multipart/form-data.
        """
        if not image_bytes:
            raise MaxApiError("Empty image payload")
        meta = self.get_upload_url("image")
        upload_url = (meta.get("url") or "").strip()
        if not upload_url:
            raise MaxApiError("MAX /uploads did not return url")

        payload, safe_name, content_type = self._prepare_image_upload(image_bytes, filename)
        files = {"data": (safe_name, payload, content_type)}
        # Do NOT reuse self.session here: it carries Authorization + Content-Type: application/json
        # which iu.oneme.ru rejects with {'error_code': '4', 'error_data': 'BAD_REQUEST'}.
        response = requests.post(
            upload_url,
            files=files,
            timeout=120,
            verify=ssl_verify_value(),
            headers={"User-Agent": "VoitosBot/0.1"},
        )
        if response.status_code >= 400:
            raise MaxApiError(
                f"MAX image upload failed {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
                body=response.text[:1000],
            )
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {"raw": response.text}
        # Prefer token from upload response; fall back to token from /uploads only
        # when the CDN body was empty/unrecognized (not an explicit rejection).
        try:
            return self._extract_image_token(data)
        except MaxApiError as exc:
            if "upload rejected" in str(exc).lower():
                raise
            fallback = meta.get("token")
            if isinstance(fallback, str) and fallback.strip():
                return fallback.strip()
            raise

    def image_attachments(self, tokens: list[str]) -> list[dict[str, Any]]:
        return [
            {"type": "image", "payload": {"token": token}}
            for token in tokens
            if token
        ]

    def download(self, url: str) -> bytes:
        response = self.session.get(url, timeout=60, verify=self.session.verify)
        if response.status_code >= 400:
            raise MaxApiError(f"Failed to download media: {response.status_code}")
        return response.content
