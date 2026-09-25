"""Tests for the UI presentation helpers (src/reports/tables.py)."""

import pandas as pd

from src.engine.reconciler import ReconcileConfig, reconcile
from src.parsers.gl_parser import parse_gl_csv
from src.parsers.lhdn_parser import parse_lhdn_json
from src.reports.tables import display_frame, style_bucket


def _matched_bucket() -> pd.DataFrame:
    gl = parse_gl_csv("samples/gl_sample.csv")
    lhdn = parse_lhdn_json("samples/lhdn_sample.json")
    return reconcile(gl, lhdn, ReconcileConfig()).matched


def test_display_frame_friendly_headers_and_rounding():
    df = display_frame(_matched_bucket())
    assert "GL Reference" in df.columns
    assert "SST Variance (RM)" in df.columns
    assert "transaction_date" not in df.columns
    money = df["Total GL (RM)"].dropna()
    assert ((money * 100).round() == money * 100).all()


def test_display_frame_empty_passthrough():
    empty = pd.DataFrame(columns=["tin"])
    assert display_frame(empty).empty


def test_display_frame_does_not_mutate_input():
    raw = _matched_bucket()
    snapshot = raw.copy(deep=True)
    display_frame(raw)
    pd.testing.assert_frame_equal(raw, snapshot)


def test_style_bucket_returns_styler_with_highlight():
    df = display_frame(_matched_bucket())
    styler = style_bucket(df, "matched")
    html = styler.to_html()
    assert "C6EFCE" in html  # matched green tint


def test_style_bucket_sst_variance_flagged():
    gl = parse_gl_csv("samples/gl_sample.csv")
    lhdn = parse_lhdn_json("samples/lhdn_sample.json")
    mismatch = reconcile(gl, lhdn, ReconcileConfig()).sst_rate_mismatch
    html = style_bucket(display_frame(mismatch), "sst").to_html()
    assert "FFEB9C" in html  # yellow variance highlight
