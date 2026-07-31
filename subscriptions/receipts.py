from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from django.conf import settings

from ai.factory import get_llm_provider, get_runtime_settings
from ai.yandex import normalize_yandex_model

logger = logging.getLogger(__name__)

VISION_URL = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"
OCR_URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if digits.startswith("7") and len(digits) == 11:
        digits = "8" + digits[1:]
    return digits


def names_match(expected: str, found: str) -> bool:
    def norm(s: str) -> str:
        s = (s or "").lower().replace("ё", "е")
        s = re.sub(r"[^а-яa-z\s]", " ", s)
        return " ".join(s.split())

    exp = norm(expected)
    got = norm(found)
    if not exp or not got:
        return False
    if exp in got or got in exp:
        return True
    # tolerate typo Вячесолавович / Вячеславович
    exp_tokens = set(exp.split())
    got_tokens = set(got.split())
    # require last name + at least one first name match
    if "григорьев" in got_tokens and ("дмитрий" in got_tokens or "дмитрию" in got_tokens):
        return True
    overlap = exp_tokens & got_tokens
    return len(overlap) >= 2


@dataclass
class ReceiptParseResult:
    amount: Decimal | None
    transfer_date: date | None
    recipient_phone: str
    recipient_name: str
    ocr_text: str
    notes: str
    details_match: bool


def ocr_image_bytes(image_bytes: bytes) -> str:
    cfg = get_runtime_settings()
    if not cfg.yandex_api_key:
        raise RuntimeError("Yandex API Key не настроен")

    b64 = base64.b64encode(image_bytes).decode("ascii")
    headers = {
        "Authorization": f"Api-Key {cfg.yandex_api_key}",
        "Content-Type": "application/json",
        "x-folder-id": cfg.yandex_folder_id,
    }

    # Prefer modern OCR endpoint, fallback to Vision batchAnalyze
    try:
        resp = requests.post(
            OCR_URL,
            headers=headers,
            json={
                "mimeType": "JPEG",
                "languageCodes": ["ru", "en"],
                "model": "page",
                "content": b64,
            },
            timeout=60,
        )
        if resp.status_code < 400:
            data = resp.json()
            text = _extract_ocr_v1_text(data)
            if text.strip():
                return text
        else:
            logger.warning("OCR v1 failed %s: %s", resp.status_code, resp.text[:300])
    except Exception:
        logger.exception("OCR v1 request failed")

    payload = {
        "folderId": cfg.yandex_folder_id,
        "analyze_specs": [
            {
                "content": b64,
                "features": [
                    {
                        "type": "TEXT_DETECTION",
                        "text_detection_config": {"language_codes": ["ru", "en"]},
                    }
                ],
            }
        ],
    }
    resp = requests.post(VISION_URL, headers=headers, json=payload, timeout=60)
    if resp.status_code >= 400:
        logger.error("Vision OCR error %s: %s", resp.status_code, resp.text[:400])
        resp.raise_for_status()
    return _extract_vision_text(resp.json())


def _extract_ocr_v1_text(data: dict[str, Any]) -> str:
    result = data.get("result") or {}
    ann = result.get("textAnnotation") or {}
    if ann.get("fullText"):
        return ann["fullText"]
    blocks = ann.get("blocks") or []
    lines_out = []
    for block in blocks:
        for line in block.get("lines") or []:
            if line.get("text"):
                lines_out.append(line["text"])
            else:
                words = [w.get("text", "") for w in line.get("words") or []]
                lines_out.append(" ".join(words))
    return "\n".join(lines_out)


def _extract_vision_text(data: dict[str, Any]) -> str:
    chunks: list[str] = []
    for res in data.get("results") or []:
        for r in res.get("results") or []:
            text_det = (r.get("textDetection") or {})
            for page in text_det.get("pages") or []:
                for block in page.get("blocks") or []:
                    for line in block.get("lines") or []:
                        words = [w.get("text", "") for w in line.get("words") or []]
                        if words:
                            chunks.append(" ".join(words))
    return "\n".join(chunks)


def analyze_receipt_text(ocr_text: str) -> ReceiptParseResult:
    cfg = get_runtime_settings()
    expected_phone = normalize_phone(cfg.payment_phone or "89625507832")
    expected_name = cfg.payment_name or "Григорьев Дмитрий Вячеславович"

    system = (
        "Ты извлекаешь данные из текста банковского чека/перевода. "
        "Ответь ТОЛЬКО JSON без markdown:\n"
        "{\n"
        '  "amount": 100.00,\n'
        '  "transfer_date": "YYYY-MM-DD" или null,\n'
        '  "recipient_phone": "строка или null",\n'
        '  "recipient_name": "строка или null",\n'
        '  "notes": "кратко"\n'
        "}\n"
        f"Ожидаемый телефон получателя: {expected_phone}. "
        f"Ожидаемое ФИО: {expected_name}."
    )
    amount = None
    transfer_date = None
    phone = ""
    name = ""
    notes = ""
    try:
        llm = get_llm_provider()
        raw = llm.complete_text(system, ocr_text[:6000], temperature=0.1, max_tokens=500)
        data = _extract_json(raw)
        amount = _to_decimal(data.get("amount"))
        transfer_date = _to_date(data.get("transfer_date"))
        phone = str(data.get("recipient_phone") or "")
        name = str(data.get("recipient_name") or "")
        notes = str(data.get("notes") or "")
    except Exception:
        logger.exception("LLM receipt parse failed, using heuristics")
        amount, transfer_date, phone, name = _heuristic_parse(ocr_text)
        notes = "heuristic"

    phone_norm = normalize_phone(phone) or _find_phone_in_text(ocr_text)
    if not name:
        name = expected_name if names_match(expected_name, ocr_text) else ""
    phone_ok = phone_norm == expected_phone or expected_phone in normalize_phone(ocr_text)
    name_ok = names_match(expected_name, name) or names_match(expected_name, ocr_text)
    # «даты совпадают» — перевод не старше 14 дней и не из будущего
    today = date.today()
    date_ok = True
    if transfer_date:
        delta = (today - transfer_date).days
        date_ok = -1 <= delta <= 14

    details_match = bool(phone_ok and name_ok and date_ok and amount and amount >= 1)
    return ReceiptParseResult(
        amount=amount,
        transfer_date=transfer_date,
        recipient_phone=phone_norm or phone,
        recipient_name=name,
        ocr_text=ocr_text,
        notes=notes,
        details_match=details_match,
    )


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        s = str(value).replace(" ", "").replace(",", ".")
        s = re.sub(r"[^\d.]", "", s)
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _find_phone_in_text(text: str) -> str:
    for m in re.finditer(r"(?:\+?7|8)\s*[\d\-()\s]{9,}", text):
        p = normalize_phone(m.group(0))
        if len(p) == 11:
            return p
    return ""


def _heuristic_parse(text: str) -> tuple[Decimal | None, date | None, str, str]:
    amount = None
    m = re.search(r"(\d+[\s\d]*[.,]\d{2}|\d{2,6})\s*(?:₽|руб|RUB)?", text, re.I)
    if m:
        amount = _to_decimal(m.group(1))
    transfer_date = None
    m = re.search(r"(\d{2}[./]\d{2}[./]\d{4})", text)
    if m:
        transfer_date = _to_date(m.group(1))
    phone = _find_phone_in_text(text)
    name = "Григорьев" if "григорьев" in text.lower() else ""
    return amount, transfer_date, phone, name
