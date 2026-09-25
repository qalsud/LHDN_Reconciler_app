"""Edge-case tests for the reconciliation engine."""

import pandas as pd
import pytest

from src.engine.reconciler import ReconcileConfig, reconcile
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
def cfg() -> ReconcileConfig:
    return ReconcileConfig(date_tolerance_days=2, amount_tolerance_rm=0.05, sst_tolerance_rm=0.05)


def test_sample_population_buckets(cfg):
    gl = parse_gl_csv("samples/gl_sample.csv")
    lhdn = parse_lhdn_json("samples/lhdn_sample.json")
    result = reconcile(gl, lhdn, cfg)
    assert result.summary["matched"] == 6
    assert result.summary["sst_rate_mismatch"] == 2
    assert result.summary["missing_uuid"] == 2  # 1 fuzzy + 1 LHDN-only
    assert result.summary["unsubmitted_sales"] == 3
    # Every GL row lands in exactly one GL-side bucket.
    assert result.summary["matched"] + result.summary["sst_rate_mismatch"] \
        + result.summary["missing_uuid"] - 1 + result.summary["unsubmitted_sales"] == 12


def test_exact_match_case_insensitive_reference(cfg):
    gl = _gl([{"transaction_date": "2026-08-01", "tin": "C1", "invoice_reference": "inv-1",
               "subtotal": 100.0, "sst_amount": 6.0, "total_amount": 106.0}])
    lhdn = _lhdn([{"lhdn_uuid": "u1", "tin": "C1", "invoice_date": "2026-08-01",
                   "invoice_reference": "INV-1", "sst_amount": 6.0, "total_amount": 106.0}])
    result = reconcile(gl, lhdn, cfg)
    assert len(result.matched) == 1
    assert result.matched.iloc[0]["match_stage"] == "Pass1_Exact"


def test_rounding_tolerance_boundary(cfg):
    """Total diff of exactly RM 0.05 must still count as matched."""
    gl = _gl([{"transaction_date": "2026-08-01", "tin": "C1", "invoice_reference": "INV-1",
               "subtotal": 100.0, "sst_amount": 6.0, "total_amount": 106.00}])
    lhdn = _lhdn([{"lhdn_uuid": "u1", "tin": "C1", "invoice_date": "2026-08-01",
                   "invoice_reference": "INV-1", "sst_amount": 6.0, "total_amount": 106.05}])
    assert len(reconcile(gl, lhdn, cfg).matched) == 1

    lhdn2 = lhdn.copy()
    lhdn2.loc[0, "total_amount"] = 106.06  # beyond tolerance on exact-ref match
    result = reconcile(gl, lhdn2, cfg)
    assert len(result.sst_rate_mismatch) == 1


def test_fuzzy_date_window_edge(cfg):
    base = {"tin": "C1", "invoice_reference": "DIFFERENT",
            "subtotal": 100.0, "sst_amount": 6.0, "total_amount": 106.0}
    gl = _gl([{**base, "transaction_date": "2026-08-01", "invoice_reference": "INV-GL"}])
    lhdn_inside = _lhdn([{**base, "lhdn_uuid": "u1", "invoice_date": "2026-08-03",
                          "invoice_reference": "INV-LHDN"}])  # +2 days -> in window
    assert len(reconcile(gl, lhdn_inside, cfg).missing_uuid) == 1

    lhdn_outside = _lhdn([{**base, "lhdn_uuid": "u1", "invoice_date": "2026-08-04",
                           "invoice_reference": "INV-LHDN"}])  # +3 days -> out
    result = reconcile(gl, lhdn_outside, cfg)
    assert len(result.unsubmitted_sales) == 1
    assert len(result.missing_uuid) == 1  # the LHDN-only doc


def test_sst_mismatch_flagged(cfg):
    gl = _gl([{"transaction_date": "2026-08-01", "tin": "C1", "invoice_reference": "INV-1",
               "subtotal": 100.0, "sst_amount": 6.0, "total_amount": 106.0}])
    lhdn = _lhdn([{"lhdn_uuid": "u1", "tin": "C1", "invoice_date": "2026-08-01",
                   "invoice_reference": "INV-1", "sst_amount": 8.0, "total_amount": 106.0}])
    result = reconcile(gl, lhdn, cfg)
    assert len(result.sst_rate_mismatch) == 1
    assert result.sst_rate_mismatch.iloc[0]["sst_variance"] == pytest.approx(-2.0)


def test_tin_mismatch_never_matches(cfg):
    gl = _gl([{"transaction_date": "2026-08-01", "tin": "C1", "invoice_reference": "INV-1",
               "subtotal": 100.0, "sst_amount": 6.0, "total_amount": 106.0}])
    lhdn = _lhdn([{"lhdn_uuid": "u1", "tin": "C2", "invoice_date": "2026-08-01",
                   "invoice_reference": "INV-1", "sst_amount": 6.0, "total_amount": 106.0}])
    result = reconcile(gl, lhdn, cfg)
    assert len(result.unsubmitted_sales) == 1
    assert len(result.matched) == 0


def test_inputs_not_mutated(cfg):
    gl = parse_gl_csv("samples/gl_sample.csv")
    lhdn = parse_lhdn_json("samples/lhdn_sample.json")
    gl_cols, lhdn_cols = list(gl.columns), list(lhdn.columns)
    reconcile(gl, lhdn, cfg)
    assert list(gl.columns) == gl_cols
    assert list(lhdn.columns) == lhdn_cols
