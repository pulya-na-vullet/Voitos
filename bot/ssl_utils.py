from __future__ import annotations

import logging
import warnings
from functools import lru_cache
from pathlib import Path

import certifi
from django.conf import settings

logger = logging.getLogger(__name__)

CERTS_DIR = Path(__file__).resolve().parent.parent / "certs"
ROOT_CA = CERTS_DIR / "russian_trusted_root_ca.cer"
SUB_CA = CERTS_DIR / "russian_trusted_sub_ca.cer"
BUNDLE = CERTS_DIR / "ca_bundle.pem"


def ensure_ca_bundle() -> Path:
    """Build certifi + Минцифры CA bundle used by MAX API."""
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    need_rebuild = not BUNDLE.exists()
    if not need_rebuild and ROOT_CA.exists() and SUB_CA.exists():
        # Rebuild if source certs are newer than bundle
        need_rebuild = BUNDLE.stat().st_mtime < max(ROOT_CA.stat().st_mtime, SUB_CA.stat().st_mtime)
    if need_rebuild:
        chunks = [Path(certifi.where()).read_text(encoding="utf-8"), "\n"]
        for path, title in (
            (ROOT_CA, "Russian Trusted Root CA"),
            (SUB_CA, "Russian Trusted Sub CA"),
        ):
            if path.exists():
                chunks.append(f"\n# {title}\n")
                chunks.append(path.read_text(encoding="utf-8"))
                if not chunks[-1].endswith("\n"):
                    chunks.append("\n")
            else:
                logger.warning("Missing CA file: %s", path)
        BUNDLE.write_text("".join(chunks), encoding="utf-8")
        logger.info("Built SSL CA bundle at %s", BUNDLE)
    return BUNDLE


@lru_cache(maxsize=1)
def ssl_verify_value() -> bool | str:
    """
    Return requests `verify=` argument.

    - False when MAX_SSL_VERIFY=false (last-resort for broken local trust stores)
    - path to CA bundle with Минцифры certs otherwise
    """
    enabled = getattr(settings, "MAX_SSL_VERIFY", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in {"0", "false", "no", "off"}
    if not enabled:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        logger.warning("SSL verification disabled (MAX_SSL_VERIFY=false)")
        return False
    custom = getattr(settings, "MAX_SSL_CA_BUNDLE", "") or ""
    if custom and Path(custom).exists():
        return custom
    return str(ensure_ca_bundle())


def apply_session_ssl(session) -> None:
    session.verify = ssl_verify_value()
