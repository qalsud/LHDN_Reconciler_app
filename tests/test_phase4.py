"""Tests for anomaly scoring and audit narratives (Phase 4). No network use."""

import os

import pandas as pd
import pytest

from src.ai.auditor import (
    _parse_batch_response,
    attach_narratives,
    build_narrative_prompt,
    fallback_narrative,
    narrate,
    narrate_batch,
    needs_narrative,
)
from src.engine.anomaly import build_features, score_by_reference, score_transactions
from src.engine.matcher import MatchConfig, match
from src.parsers.gl_parser import parse_gl_csv
from src.parsers.lhdn_parser import parse_lhdn_json


@pytest.fixture
def gl():
    return parse_gl_csv("samples/gl_sample.csv")


def test_build_features_columns(gl):
    feats = build_features(gl)
    assert list(feats.columns) == ["log_total", "tax_ratio", "hour", "day_of_week"]
    assert len(feats) == len(gl)
    # Standard 6% SST ratio visible on clean rows.
    assert feats["tax_ratio"].iloc[0] == pytest.approx(600 / 10600)


def test_scores_bounded_and_deterministic(gl):
    first = score_transactions(gl)
    second = score_transactions(gl)
    pd.testing.assert_series_equal(first, second)
    assert ((first >= 0.0) & (first <= 100.0)).all()


def test_outlier_scores_highest():
    rows = [
        {"transaction_date": "2026-08-01", "tin": "C1", "invoice_reference": f"INV-{i}",
         "subtotal": 100.0, "sst_amount": 6.0, "total_amount": 106.0}
        for i in range(10)
    ]
    rows.append({"transaction_date": "2026-08-02", "tin": "C1", "invoice_reference": "INV-X",
                 "subtotal": 100.0, "sst_amount": 6000.0, "total_amount": 6100.0})
    df = pd.DataFrame(rows)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    scores = score_transactions(df)
    assert scores.idxmax() == len(df) - 1
    assert scores.iloc[-1] > 50.0


def test_single_row_scores_zero():
    df = pd.DataFrame([{"transaction_date": "2026-08-01", "tin": "C1",
                        "invoice_reference": "INV-1", "subtotal": 100.0,
                        "sst_amount": 6.0, "total_amount": 106.0}])
    assert score_transactions(df).iloc[0] == 0.0


def test_score_by_reference_keys(gl):
    mapping = score_by_reference(gl)
    assert mapping["INV-2026-0001"] == pytest.approx(
        score_transactions(gl).iloc[0])


def test_needs_narrative_gating():
    assert needs_narrative("SST_Rate_Mismatch", 10.0) is True
    assert needs_narrative("Matched", 90.0) is True
    assert needs_narrative("Matched", 85.0) is False
    assert needs_narrative("Matched", 10.0) is False
    assert needs_narrative("Matched", float("nan")) is False


def test_build_narrative_prompt_contains_rows_and_action():
    prompt = build_narrative_prompt(
        {"sst_variance": -100.0, "total_variance": -100.0,
         "invoice_reference_gl": "INV-3"},
        {"lhdn_uuid": "u-3"}, 70.0, "SST_Rate_Mismatch")
    assert "SST_Rate_Mismatch" in prompt
    assert "70.0" in prompt
    assert "accounting action" in prompt


def test_fallback_narrative_mentions_variance_and_action():
    text = fallback_narrative(
        {"sst_variance": -100.0, "total_variance": -100.0},
        None, 92.5, "SST_Rate_Mismatch")
    assert "RM -100.0" in text
    assert "92.5/100" in text
    assert "credit" in text.lower()


def test_narrate_without_key_uses_fallback(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    text = narrate({"sst_variance": 1.0}, None, 90.0, "Matched")
    assert "Template narrative" in text


def test_narrate_with_mocked_litellm(monkeypatch):
    class _Msg:
        content = "SST was undercharged by RM 100. Issue a debit note for the shortfall."

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    monkeypatch.setattr("litellm.completion", lambda **kwargs: _Resp())
    text = narrate({"sst_variance": -100.0}, {"lhdn_uuid": "u-1"},
                   90.0, "SST_Rate_Mismatch", api_key="test-key")
    assert "debit note" in text


def test_narrate_forwards_provider_model_and_key(monkeypatch):
    """Provider wiring: litellm model id + explicit key pass through untouched."""
    seen: dict = {}

    class _Msg:
        content = "Variance explained. Issue a credit note."

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def _fake_completion(**kwargs):
        seen.update(kwargs)
        return _Resp()

    monkeypatch.setattr("litellm.completion", _fake_completion)
    text = narrate({"sst_variance": -100.0}, {"lhdn_uuid": "u-1"},
                   90.0, "SST_Rate_Mismatch",
                   model="gemini/gemini-2.5-flash", api_key="AIza-test-key")
    assert "credit note" in text
    assert seen["model"] == "gemini/gemini-2.5-flash"
    assert seen["api_key"] == "AIza-test-key"


def test_narrate_picks_up_provider_key_from_env(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-env-key")

    class _Msg:
        content = "Variance explained. Verify the SST computation."

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def _fake_completion(**kwargs):
        assert kwargs["api_key"] == "AIza-env-key"
        return _Resp()

    monkeypatch.setattr("litellm.completion", _fake_completion)
    text = narrate({"sst_variance": -100.0}, None, 90.0, "Matched")
    assert "Verify the SST" in text


def test_attach_narratives_gating_and_immutability(gl, monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    lhdn = parse_lhdn_json("samples/lhdn_sample.json")
    result = match(gl, lhdn, MatchConfig())
    buckets = {
        "Matched": result.matched.assign(anomaly_score=10.0),
        "SST_Rate_Mismatch": result.mismatch.assign(anomaly_score=10.0),
    }
    enriched = attach_narratives(buckets, enabled=True)  # no key -> template fallback
    assert (enriched["Matched"]["ai_narrative"] == "").all()
    assert (enriched["SST_Rate_Mismatch"]["ai_narrative"] != "").all()
    assert (buckets["SST_Rate_Mismatch"]["ai_narrative"] == "").all()  # input untouched

    disabled = attach_narratives(buckets, enabled=False)
    assert (disabled["SST_Rate_Mismatch"]["ai_narrative"] == "").all()


def _batch_resp(text: str):
    class _Msg:
        content = text

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    return _Resp()


def test_parse_batch_response_ok_and_bad():
    good = "Row 1: Cause one. Do action one.\nRow 2: Cause two. Do action two."
    assert _parse_batch_response(good, 2) == ["Cause one. Do action one.", "Cause two. Do action two."]
    assert _parse_batch_response("Row 1: Only one.", 2) is None
    assert _parse_batch_response("Some freeform text.", 1) is None


def test_narrate_batch_single_call_for_many_rows(monkeypatch):
    calls: list = []

    def _fake_completion(**kwargs):
        calls.append(kwargs)
        return _batch_resp("Row 1: Cause one. Do action one.\nRow 2: Cause two. Do action two.")

    monkeypatch.setattr("litellm.completion", _fake_completion)
    out = narrate_batch(
        [{"gl_row": {"sst_variance": 1.0}, "lhdn_row": None,
          "anomaly_score": 90.0, "bucket": "SST_Rate_Mismatch"},
         {"gl_row": {"sst_variance": 2.0}, "lhdn_row": None,
          "anomaly_score": 91.0, "bucket": "SST_Rate_Mismatch"}],
        model="gemini/gemini-2.5-flash", api_key="AIza-test-key",
    )
    assert len(calls) == 1  # quota-friendly: one request for the whole batch
    assert out == ["Cause one. Do action one.", "Cause two. Do action two."]


def test_narrate_batch_falls_back_without_key(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    out = narrate_batch(
        [{"gl_row": {"sst_variance": 1.0}, "lhdn_row": None,
          "anomaly_score": 90.0, "bucket": "Matched"}],
        model="gemini/gemini-2.5-flash",
    )
    assert len(out) == 1
    assert "Template narrative" in out[0]


def test_narrate_batch_unparsable_response_uses_templates(monkeypatch):
    # Multi-row replies without Row markers cannot be split safely -> templates.
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _batch_resp("Freeform blobs."))
    out = narrate_batch(
        [{"gl_row": {"sst_variance": 1.0}, "lhdn_row": None,
          "anomaly_score": 90.0, "bucket": "Matched"},
         {"gl_row": {"sst_variance": 2.0}, "lhdn_row": None,
          "anomaly_score": 91.0, "bucket": "Matched"}],
        model="gemini/gemini-2.5-flash", api_key="AIza-test-key",
    )
    assert len(out) == 2
    assert all("Template narrative" in text for text in out)


def test_narrate_batch_single_row_accepts_unmarked_reply(monkeypatch):
    # A single-row freeform reply is usable as-is.
    monkeypatch.setattr("litellm.completion",
                        lambda **kwargs: _batch_resp("SST was off. Recompute the tax."))
    out = narrate_batch(
        [{"gl_row": {"sst_variance": 1.0}, "lhdn_row": None,
          "anomaly_score": 90.0, "bucket": "Matched"}],
        model="gemini/gemini-2.5-flash", api_key="AIza-test-key",
    )
    assert out == ["SST was off. Recompute the tax."]
