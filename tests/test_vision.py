"""Tests for PDF invoice vision ingestion (src/ai/vision.py). No network use."""

import base64

import pandas as pd
import pytest

from src.ai import vision
from src.ai.vision import (
    VisionExtractionError,
    append_vision_rows,
    build_extraction_messages,
    extract_invoice_fields,
    pdf_to_base64_images,
    validate_invoice_fields,
    vision_rows_to_gl_df,
)


def _make_pdf(path, text="TAX INVOICE INV-99 Total RM 106.00 SST RM 6.00"):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_pdf_to_base64_images_roundtrip(tmp_path):
    pdf = tmp_path / "inv.pdf"
    _make_pdf(str(pdf))
    images = pdf_to_base64_images(pdf)
    assert len(images) == 1
    raw = base64.b64decode(images[0])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_pdf_to_base64_missing_file(tmp_path):
    with pytest.raises(VisionExtractionError, match="not found"):
        pdf_to_base64_images(tmp_path / "nope.pdf")


def test_build_extraction_messages_shape():
    messages = build_extraction_messages(["abc123"])
    assert messages[0]["role"] == "system"
    assert messages[1]["content"][0]["type"] == "text"
    assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "invoice_ref" in messages[1]["content"][0]["text"]


def test_validate_invoice_fields_ok():
    out = validate_invoice_fields({
        "invoice_ref": "INV-1", "txn_date": "2026-08-01",
        "tin_number": "C1", "tax_amount": 6.0, "total_amount": "106.00",
    })
    assert out["tax_amount"] == pytest.approx(6.0)
    assert out["total_amount"] == pytest.approx(106.0)


def test_validate_invoice_fields_missing_and_bad():
    with pytest.raises(VisionExtractionError, match="missing fields"):
        validate_invoice_fields({"invoice_ref": "INV-1"})
    with pytest.raises(VisionExtractionError, match="not numeric"):
        validate_invoice_fields({
            "invoice_ref": "INV-1", "txn_date": "2026-08-01",
            "tin_number": "C1", "tax_amount": "six", "total_amount": 106.0})
    with pytest.raises(VisionExtractionError, match="not a valid date"):
        validate_invoice_fields({
            "invoice_ref": "INV-1", "txn_date": "yesterday-ish",
            "tin_number": "C1", "tax_amount": 6.0, "total_amount": 106.0})


def test_extract_invoice_fields_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(VisionExtractionError, match="API key"):
        extract_invoice_fields(["abc"], api_key=None)


def test_extract_invoice_fields_mocked_model(monkeypatch):
    payload = ('{"invoice_ref": "INV-7", "txn_date": "2026-08-07", '
               '"tin_number": "C7", "tax_amount": 12.0, "total_amount": 212.0}')

    class _Msg:
        content = payload

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            assert kwargs["response_format"] == {"type": "json_object"}
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, api_key=None):
            self.chat = _Chat()

    monkeypatch.setattr("openai.OpenAI", _Client)
    out = extract_invoice_fields(["abc"], api_key="test-key")
    assert out["invoice_ref"] == "INV-7"
    assert out["total_amount"] == pytest.approx(212.0)


def test_extract_uses_configured_endpoint_from_env(monkeypatch):
    """Vision: AI_VISION_MODEL + AI_VISION_BASE_URL reach the OpenAI client."""
    monkeypatch.setenv("AI_VISION_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("AI_VISION_BASE_URL",
                        "https://generativelanguage.googleapis.com/v1beta/openai/")
    seen: dict = {}

    payload = ('{"invoice_ref": "INV-X", "txn_date": "2026-08-01", '
               '"tin_number": "CX", "tax_amount": 6.0, "total_amount": 106.0}')

    class _Msg:
        content = payload

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            assert kwargs["model"] == "gemini-3.8-flash"
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, api_key=None, base_url=None):
            seen["api_key"] = api_key
            seen["base_url"] = base_url
            self.chat = _Chat()

    monkeypatch.setattr("openai.OpenAI", _Client)
    out = extract_invoice_fields(["abc"], api_key="AIza-test-key")
    assert out["invoice_ref"] == "INV-X"
    assert seen["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert seen["api_key"] == "AIza-test-key"


def test_vision_rows_to_gl_df_math_and_append():
    rows = [{"invoice_ref": "inv-9", "txn_date": "2026-08-09",
             "tin_number": "C9", "tax_amount": 6.0, "total_amount": 106.0}]
    df = vision_rows_to_gl_df(rows)
    assert df.loc[0, "invoice_reference"] == "INV-9"
    assert df.loc[0, "subtotal"] == pytest.approx(100.0)

    base = pd.DataFrame([{
        "transaction_date": pd.Timestamp("2026-08-01"), "tin": "C1",
        "invoice_reference": "INV-1", "subtotal": 100.0,
        "sst_amount": 6.0, "total_amount": 106.0}])
    combined = append_vision_rows(base, rows)
    assert len(combined) == 2
    # Base frame untouched.
    assert len(base) == 1
    assert vision is not None
