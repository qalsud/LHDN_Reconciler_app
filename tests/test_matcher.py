"""Tests for the deterministic matching core (src/engine/matcher.py)."""

import pandas as pd
import pytest

from src.engine.matcher import MatchConfig, match
from src.parsers.gl_parser import parse_gl_csv
from src.parsers.lhdn_parser import parse_lhdn_json


def _gl(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=[
        "transaction_date", "tin", "invoice_reference",
        "subtotal", "sst_amount", "total_amount",
    ]).assign(transaction_date=lambda d: pd.to_datetime(d["transaction_date"]))


def _lhdn(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=[
        "lhdn_uuid", "tin", "invoice_date",
        "invoice_reference", "sst_amount", "total_amount",
    ]).assign(invoice_date=lambda d: pd.to_datetime(d["invoice_date"]))


@pytest.fixture
def cfg() -> MatchConfig:
    return MatchConfig(date_tolerance_days=2, amount_tolerance_rm=0.05, sst_tolerance_rm=0.05)


def test_matcher_sample_population(cfg):
    gl = parse_gl_csv("samples/gl_sample.csv")
    lhdn = parse_lhdn_json("samples/lhdn_sample.json")
    result = match(gl, lhdn, cfg)
    assert result.summary == {
        "gl_total": 12, "lhdn_total": 10,
        "matched": 6, "unsubmitted": 3,
        "sst_rate_mismatch": 2, "missing_uuid": 2,
        "date_tolerance_days": 2,
        "amount_tolerance_rm": 0.05, "sst_tolerance_rm": 0.05,
    }


def test_matcher_generated_mocks_roundtrip(cfg, tmp_path):
    import subprocess
    import sys
    gl_out = tmp_path / "mock_gl.csv"
    lhdn_out = tmp_path / "mock_lhdn.json"
    proc = subprocess.run(
        [sys.executable, "samples/generate_mocks.py", "--seed", "42",
         "--gl-out", str(gl_out), "--lhdn-out", str(lhdn_out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    result = match(parse_gl_csv(gl_out), parse_lhdn_json(lhdn_out), cfg)
    assert result.summary["matched"] == 6          # 5 clean + 1 rounding
    assert result.summary["sst_rate_mismatch"] == 1
    assert result.summary["missing_uuid"] == 2     # 1 fuzzy + 1 LHDN-only
    assert result.summary["unsubmitted"] == 2
    assert result.summary["gl_total"] == 10


def test_matcher_output_schema(cfg):
    gl = parse_gl_csv("samples/gl_sample.csv")
    lhdn = parse_lhdn_json("samples/lhdn_sample.json")
    result = match(gl, lhdn, cfg)
    for frame in result.buckets().values():
        assert "anomaly_score" in frame.columns
        assert "ai_narrative" in frame.columns


def test_matcher_pass_labels(cfg):
    gl = parse_gl_csv("samples/gl_sample.csv")
    lhdn = parse_lhdn_json("samples/lhdn_sample.json")
    result = match(gl, lhdn, cfg)
    assert set(result.matched["match_stage"].unique()) <= {"Pass1_Exact", "Pass2_Fuzzy"}
    assert "Pass1_Exact" in set(result.matched["match_stage"].unique())


def test_matcher_each_lhdn_used_once(cfg):
    gl = _gl([{"transaction_date": "2026-08-01", "tin": "C1", "invoice_reference": "INV-A",
               "subtotal": 100.0, "sst_amount": 6.0, "total_amount": 106.0}])
    lhdn = _lhdn([
        {"lhdn_uuid": "u1", "tin": "C1", "invoice_date": "2026-08-01",
         "invoice_reference": "INV-A", "sst_amount": 6.0, "total_amount": 106.0},
        {"lhdn_uuid": "u2", "tin": "C1", "invoice_date": "2026-08-02",
         "invoice_reference": "INV-B", "sst_amount": 6.0, "total_amount": 106.0},
    ])
    result = match(gl, lhdn, cfg)
    assert len(result.matched) == 1
    assert len(result.missing_uuid) == 1  # u2 surfaces as LHDN-only
    assert result.missing_uuid.iloc[0]["lhdn_uuid"] == "u2"


def test_matcher_rejects_bad_schema(cfg):
    gl = _gl([{"transaction_date": "2026-08-01", "tin": "C1", "invoice_reference": "INV-1",
               "subtotal": 100.0, "sst_amount": 6.0, "total_amount": 106.0}])
    with pytest.raises(ValueError, match="missing columns"):
        match(gl.drop(columns=["tin"]), _lhdn([]), cfg)


def test_matcher_mixed_timezone_dates(cfg):
    """Authentic SDK dateTimeIssued values carry UTC offsets; GL dates don't."""
    gl = _gl([{"transaction_date": "2026-08-01", "tin": "C1", "invoice_reference": "INV-1",
               "subtotal": 100.0, "sst_amount": 6.0, "total_amount": 106.0}])
    lhdn = _lhdn([{"lhdn_uuid": "u1", "tin": "C1", "invoice_date": "2026-08-01T10:00:00Z",
                   "invoice_reference": "INV-1", "sst_amount": 6.0, "total_amount": 106.0}])
    result = match(gl, lhdn, cfg)
    assert len(result.matched) == 1
    assert result.matched.iloc[0]["date_diff_days"] == 0


def test_matcher_realistic_files_roundtrip(cfg):
    gl = parse_gl_csv("samples/realistic_gl.csv")
    lhdn = parse_lhdn_json("samples/realistic_lhdn.json")
    result = match(gl, lhdn, cfg)
    assert result.summary["gl_total"] == 60
    assert result.summary["lhdn_total"] == 53
    assert result.summary["matched"] == 44
    assert result.summary["unsubmitted"] == 8
    assert result.summary["sst_rate_mismatch"] == 5
    assert result.summary["missing_uuid"] == 4
