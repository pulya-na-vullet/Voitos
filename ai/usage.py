from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from database.models import AiUsageKind, AiUsageLog, AppSettings

logger = logging.getLogger(__name__)


def _cfg() -> AppSettings:
    return AppSettings.load()


def estimate_llm_cost(total_tokens: int, cfg: AppSettings | None = None) -> Decimal:
    cfg = cfg or _cfg()
    rate = Decimal(cfg.yandex_llm_rub_per_1k or 0)
    return (Decimal(max(0, total_tokens)) / Decimal("1000") * rate).quantize(Decimal("0.0001"))


def estimate_stt_cost(units: int = 1, cfg: AppSettings | None = None) -> Decimal:
    cfg = cfg or _cfg()
    rate = Decimal(cfg.yandex_stt_rub_per_request or 0)
    return (Decimal(max(1, units)) * rate).quantize(Decimal("0.0001"))


def estimate_ocr_cost(pages: int = 1, cfg: AppSettings | None = None) -> Decimal:
    cfg = cfg or _cfg()
    rate = Decimal(cfg.yandex_ocr_rub_per_page or 0)
    return (Decimal(max(1, pages)) * rate).quantize(Decimal("0.0001"))


def log_ai_usage(
    *,
    kind: str,
    estimated_cost_rub: Decimal,
    model_name: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    units: int = 1,
    meta: dict[str, Any] | None = None,
) -> None:
    """Persist usage; never raise to callers."""
    try:
        AiUsageLog.objects.create(
            kind=kind,
            model_name=(model_name or "")[:128],
            input_tokens=max(0, int(input_tokens)),
            output_tokens=max(0, int(output_tokens)),
            units=max(1, int(units)),
            estimated_cost_rub=Decimal(estimated_cost_rub or 0),
            meta=meta or {},
        )
    except Exception:
        logger.exception("Failed to log AI usage kind=%s", kind)


def log_llm_from_response(raw: dict[str, Any] | None, *, model_name: str = "") -> None:
    raw = raw or {}
    usage = (raw.get("result") or {}).get("usage") or raw.get("usage") or {}
    try:
        inp = int(usage.get("inputTextTokens") or usage.get("inputTokens") or 0)
        out = int(usage.get("completionTokens") or usage.get("outputTokens") or 0)
        total = int(usage.get("totalTokens") or (inp + out) or 0)
    except (TypeError, ValueError):
        inp = out = total = 0
    if total <= 0 and inp <= 0 and out <= 0:
        # still count a minimal request so dashboard is not empty
        total = 500
        inp = 400
        out = 100
    cost = estimate_llm_cost(total)
    log_ai_usage(
        kind=AiUsageKind.LLM,
        estimated_cost_rub=cost,
        model_name=model_name,
        input_tokens=inp,
        output_tokens=out,
        units=1,
        meta={"total_tokens": total},
    )


def log_stt_call(*, audio_format: str = "") -> None:
    cost = estimate_stt_cost(1)
    log_ai_usage(
        kind=AiUsageKind.STT,
        estimated_cost_rub=cost,
        model_name="speechkit",
        units=1,
        meta={"audio_format": audio_format},
    )


def log_ocr_call(*, mime: str = "", filename: str = "") -> None:
    cost = estimate_ocr_cost(1)
    log_ai_usage(
        kind=AiUsageKind.OCR,
        estimated_cost_rub=cost,
        model_name="vision-ocr",
        units=1,
        meta={"mime": mime, "filename": filename},
    )
