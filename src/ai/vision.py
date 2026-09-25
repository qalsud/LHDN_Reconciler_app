"""PDF invoice ingestion via vision LLM (Phase 3).

Pipeline: PDF path -> base64 PNG images (PyMuPDF, no system deps) ->
OpenAI chat completion with a strict JSON-schema prompt -> validated field
dict -> appended to the GL DataFrame feeding the Phase 1 matching engine.

LLM calls are wrapped in ``tenacity`` retries. Failures (missing key, API
error, malformed JSON) raise :class:`VisionExtractionError` with the cause
preserved — callers decide whether to skip the vendor file or abort.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import pandas as pd
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

REQUIRED_FIELDS = ["invoice_ref", "txn_date", "tin_number", "tax_amount", "total_amount"]

DEFAULT_VISION_MODEL = "gpt-4o"
VISION_FALLBACK_MODEL = "gpt-4-vision-preview"
#: xAI's OpenAI-compatible endpoint (set AI_VISION_BASE_URL to use Grok vision).
DEFAULT_VISION_BASE_URL = "https://api.openai.com/v1"

EXTRACTION_SYSTEM_PROMPT = (
    "You are a financial document parser for Malaysian SST invoices. "
    "Extract the requested fields exactly. Reply with a single JSON object "
    "and nothing else. Dates must use ISO format (YYYY-MM-DD). Amounts must "
    "be plain numbers with optional decimals, no currency symbols or commas. "
    "If a field is genuinely absent, use null."
)

EXTRACTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_ref": {"type": ["string", "null"], "description": "Invoice / bill number"},
        "txn_date": {"type": ["string", "null"], "description": "Invoice date, YYYY-MM-DD"},
        "tin_number": {"type": ["string", "null"], "description": "Supplier Tax Identification Number"},
        "tax_amount": {"type": ["number", "null"], "description": "SST amount in RM"},
        "total_amount": {"type": ["number", "null"], "description": "Grand total in RM"},
    },
    "required": REQUIRED_FIELDS,
    "additionalProperties": False,
}


class VisionExtractionError(RuntimeError):
    """Raised when invoice fields cannot be extracted from a PDF."""


def pdf_to_base64_images(pdf_path: str | Path, dpi: int = 150, max_pages: int = 10) -> list[str]:
    """Render PDF pages to base64-encoded PNG images.

    Raises:
        VisionExtractionError: on missing file, unreadable PDF, or no pages.
    """
    path = Path(pdf_path)
    if not path.is_file():
        raise VisionExtractionError(f"PDF not found: {path}")
    try:
        import fitz  # PyMuPDF; imported lazily so vision is optional
        doc = fitz.open(path)
    except Exception as exc:
        raise VisionExtractionError(f"Cannot open PDF '{path}': {exc}") from exc
    if len(doc) == 0:
        raise VisionExtractionError(f"PDF has no pages: {path}")
    images: list[str] = []
    try:
        for page in doc[:max_pages]:
            pix = page.get_pixmap(dpi=dpi)
            images.append(base64.b64encode(pix.tobytes("png")).decode("ascii"))
    except Exception as exc:
        raise VisionExtractionError(f"Failed to render PDF '{path}': {exc}") from exc
    finally:
        doc.close()
    logger.info("Rendered {} page(s) from {}", len(images), path)
    return images


def build_extraction_messages(images_b64: list[str]) -> list[dict]:
    """Build the chat messages (text prompt + image parts) for the vision call."""
    content: list[dict] = [{
        "type": "text",
        "text": ("Extract invoice fields per this JSON schema:\n"
                 + json.dumps(EXTRACTION_JSON_SCHEMA)
                 + "\nReply with the JSON object only."),
    }]
    for img in images_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img}"},
        })
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _extract_json_object(raw: str) -> dict:
    """Pull the first JSON object out of a model reply (tolerates fences)."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise VisionExtractionError(f"Model reply contained no JSON object: {raw[:200]!r}")
        text = text[start:end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VisionExtractionError(f"Model reply is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise VisionExtractionError("Model reply JSON is not an object.")
    return parsed


def validate_invoice_fields(data: dict) -> dict:
    """Validate + coerce extracted fields. Missing values become None/NaN-ready."""
    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise VisionExtractionError(f"Extracted JSON missing fields: {missing}")
    out = {
        "invoice_ref": str(data["invoice_ref"]).strip() if data["invoice_ref"] not in (None, "") else None,
        "txn_date": str(data["txn_date"]).strip() if data["txn_date"] not in (None, "") else None,
        "tin_number": str(data["tin_number"]).strip() if data["tin_number"] not in (None, "") else None,
        "tax_amount": data["tax_amount"],
        "total_amount": data["total_amount"],
    }
    for key in ("tax_amount", "total_amount"):
        if out[key] is None:
            continue
        try:
            out[key] = float(str(out[key]).replace(",", "").strip())
        except (TypeError, ValueError) as exc:
            raise VisionExtractionError(f"Field {key!r} is not numeric: {data[key]!r}") from exc
    parsed_date = pd.to_datetime(out["txn_date"], errors="coerce")
    if out["txn_date"] is not None and pd.isna(parsed_date):
        raise VisionExtractionError(f"Field 'txn_date' is not a valid date: {out['txn_date']!r}")
    return out


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_vision_model(messages: list[dict], model: str, api_key: str) -> str:
    from openai import OpenAI  # lazy: keeps vision optional

    base_url = os.getenv("AI_VISION_BASE_URL") or None  # e.g. https://api.x.ai/v1 for Grok
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=800,
    )
    content = response.choices[0].message.content
    if not content:
        raise VisionExtractionError("Vision model returned empty content.")
    return content


def extract_invoice_fields(
    images_b64: list[str],
    model: str | None = None,
    api_key: str | None = None,
) -> dict:
    """Extract + validate invoice fields from base64 page images.

    Model defaults to ``AI_VISION_MODEL`` (fallback ``gpt-4o``); the key
    defaults to ``OPENAI_API_KEY`` or ``XAI_API_KEY`` (pair with
    ``AI_VISION_BASE_URL=https://api.x.ai/v1`` for Grok vision).
    Raises VisionExtractionError after retries are exhausted.
    """
    chosen = model or os.getenv("AI_VISION_MODEL", DEFAULT_VISION_MODEL)
    key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("XAI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        raise VisionExtractionError("No vision API key set (OPENAI_API_KEY, XAI_API_KEY or GEMINI_API_KEY).")
    if not images_b64:
        raise VisionExtractionError("No page images provided for extraction.")
    messages = build_extraction_messages(images_b64)
    models = [chosen]
    if chosen in (DEFAULT_VISION_MODEL,):  # OpenAI-side fallback only
        models.append(VISION_FALLBACK_MODEL)
    last_exc: Exception | None = None
    for attempt_model in models:
        try:
            raw = _call_vision_model(messages, attempt_model, key)
            return validate_invoice_fields(_extract_json_object(raw))
        except VisionExtractionError:
            raise
        except Exception as exc:
            logger.warning("Vision call with {} failed: {}", attempt_model, exc)
            last_exc = exc
    raise VisionExtractionError(
        f"Vision extraction failed after retries (models tried: {models}).") from last_exc


def extract_invoice_pdf(
    pdf_path: str | Path,
    model: str | None = None,
    api_key: str | None = None,
    dpi: int = 150,
) -> dict:
    """End-to-end: PDF path -> validated field dict."""
    return extract_invoice_fields(pdf_to_base64_images(pdf_path, dpi=dpi),
                                  model=model, api_key=api_key)


def vision_rows_to_gl_df(rows: list[dict]) -> pd.DataFrame:
    """Convert validated vision rows to the engine GL schema.

    ``subtotal`` is derived as ``total - tax`` when both are present,
    else NaN (downstream validation will flag it).
    """
    records = []
    for row in rows:
        tax = row.get("tax_amount")
        total = row.get("total_amount")
        subtotal = (round(float(total) - float(tax), 2)
                    if tax is not None and total is not None else float("nan"))
        records.append({
            "transaction_date": pd.to_datetime(row.get("txn_date"), errors="coerce"),
            "tin": str(row.get("tin_number") or "").strip(),
            "invoice_reference": str(row.get("invoice_ref") or "").strip().upper(),
            "subtotal": subtotal,
            "sst_amount": float(tax) if tax is not None else float("nan"),
            "total_amount": float(total) if total is not None else float("nan"),
        })
    return pd.DataFrame(records, columns=[
        "transaction_date", "tin", "invoice_reference",
        "subtotal", "sst_amount", "total_amount",
    ])


def append_vision_rows(gl_df: pd.DataFrame, vision_rows: list[dict]) -> pd.DataFrame:
    """Append vision-extracted rows to the GL frame for the match engine."""
    extra = vision_rows_to_gl_df(vision_rows)
    return pd.concat([gl_df, extra], ignore_index=True)
