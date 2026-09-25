"""Shared session-state store for the multi-page Streamlit workflow app.

Pages: Upload (app.py) -> Dashboard -> Exceptions inbox -> Exports.
Review decisions persist to ``output/reviews.json`` so they survive
reruns; reconciliation results live in memory (st.session_state).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

REVIEW_PATH = Path("output/reviews.json")

EXCEPTION_BUCKETS = ("Unsubmitted_Sales", "SST_Rate_Mismatch", "Missing_UUID")

STATUS_PENDING = "pending"
STATUS_REVIEWED = "reviewed"


def get_result(session: dict):
    """Return the stored ReconcileResult or None."""
    return session.get("result")


def store_result(session: dict, result) -> None:
    session["result"] = result


def has_result(session: dict) -> bool:
    return session.get("result") is not None


def row_key(bucket: str, row: pd.Series | dict) -> str:
    """Stable identity for a bucket row across reruns."""
    data = row.to_dict() if isinstance(row, pd.Series) else dict(row)
    ref = str(data.get("invoice_reference_gl") or "").strip().upper()
    uuid = str(data.get("lhdn_uuid") or "").strip()
    tin = str(data.get("tin") or "").strip()
    return f"{bucket}|{ref}|{uuid}|{tin}"


def load_reviews(path: Path | str = REVIEW_PATH) -> dict:
    """Load persisted reviews; corrupt files yield {} (never crash the UI)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_reviews(reviews: dict, path: Path | str = REVIEW_PATH) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reviews, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def mark_reviewed(reviews: dict, key: str, note: str = "") -> dict:
    updated = dict(reviews)
    updated[key] = {"status": STATUS_REVIEWED, "note": note}
    return updated


def mark_pending(reviews: dict, key: str) -> dict:
    updated = dict(reviews)
    updated.pop(key, None)
    return updated


def review_status(reviews: dict, key: str) -> str:
    return reviews.get(key, {}).get("status", STATUS_PENDING)


def exceptions_frame(buckets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One combined inbox frame across the three exception buckets."""
    parts = []
    for bucket in EXCEPTION_BUCKETS:
        frame = buckets.get(bucket)
        if frame is None or frame.empty:
            continue
        chunk = frame.copy()
        chunk["bucket"] = bucket
        parts.append(chunk)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def review_progress(exceptions: pd.DataFrame, reviews: dict) -> dict:
    total = len(exceptions)
    if total == 0:
        return {"total": 0, "reviewed": 0, "pending": 0, "fraction": 1.0}
    reviewed = 0
    for _, row in exceptions.iterrows():
        if review_status(reviews, row_key(str(row.get("bucket", "")), row)) == STATUS_REVIEWED:
            reviewed += 1
    return {"total": total, "reviewed": reviewed, "pending": total - reviewed,
            "fraction": reviewed / total}
