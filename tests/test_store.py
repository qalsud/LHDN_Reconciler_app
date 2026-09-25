"""Tests for the workflow-app review store (src/ui/store.py)."""

import pandas as pd

from src.ui.store import (
    exceptions_frame,
    load_reviews,
    mark_pending,
    mark_reviewed,
    review_progress,
    review_status,
    row_key,
    save_reviews,
)


def _row(**overrides):
    base = {"tin": "C1", "invoice_reference_gl": "INV-1",
            "lhdn_uuid": "u-1", "sst_variance": 1.0}
    base.update(overrides)
    return pd.Series(base)


def test_row_key_stable_and_unique():
    assert row_key("B", _row()) == row_key("B", _row())
    assert row_key("B", _row()) != row_key("B", _row(invoice_reference_gl="INV-2"))
    assert row_key("A", _row()) != row_key("B", _row())


def test_review_roundtrip_and_status(tmp_path):
    path = tmp_path / "reviews.json"
    reviews = mark_reviewed({}, row_key("B", _row()), note="checked")
    assert review_status(reviews, row_key("B", _row())) == "reviewed"
    assert review_status(reviews, "nope") == "pending"
    save_reviews(reviews, path)
    assert load_reviews(path) == reviews
    reopened = mark_pending(reviews, row_key("B", _row()))
    assert review_status(reopened, row_key("B", _row())) == "pending"


def test_load_reviews_corrupt_file(tmp_path):
    bad = tmp_path / "reviews.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_reviews(bad) == {}
    assert load_reviews(tmp_path / "missing.json") == {}


def test_exceptions_frame_and_progress():
    buckets = {
        "Matched": pd.DataFrame([{"tin": "C1"}]),
        "Unsubmitted_Sales": pd.DataFrame([{"tin": "C1", "invoice_reference_gl": "A",
                                            "lhdn_uuid": "", "sst_variance": float("nan")}]),
        "SST_Rate_Mismatch": pd.DataFrame([{"tin": "C2", "invoice_reference_gl": "B",
                                            "lhdn_uuid": "u", "sst_variance": 5.0}]),
        "Missing_UUID": pd.DataFrame(),
    }
    inbox = exceptions_frame(buckets)
    assert len(inbox) == 2
    assert set(inbox["bucket"]) == {"Unsubmitted_Sales", "SST_Rate_Mismatch"}

    prog = review_progress(inbox, {})
    assert prog == {"total": 2, "reviewed": 0, "pending": 2, "fraction": 0.0}
    reviews = mark_reviewed({}, row_key("Unsubmitted_Sales", inbox.iloc[0]))
    prog = review_progress(inbox, reviews)
    assert prog["reviewed"] == 1 and prog["pending"] == 1


def test_review_progress_empty():
    assert review_progress(pd.DataFrame(), {})["fraction"] == 1.0
