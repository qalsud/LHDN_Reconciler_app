"""Exports — workbook / CSV downloads plus the review log (all via the API)."""

from __future__ import annotations

import streamlit as st

from src.ui.api_client import ApiClientError, download_workbook
from src.ui.store import exceptions_frame, get_frames, load_reviews, review_status, row_key

st.set_page_config(page_title="Reconciliation — Exports", page_icon="📤", layout="wide")
st.title("📤 Exports")
st.caption("Step 4 of 4 — download the audit pack.")

run = st.session_state.get("run")
buckets = get_frames(st.session_state)
api_key = st.session_state.get("api_key", "")
base_url = st.session_state.get("base_url", "http://localhost:8000")
if run is None or buckets is None or not api_key:
    st.info("Run a reconciliation on the Upload page first.")
    st.page_link("app.py", label="Go to Upload & Run", icon="🧾")
    st.stop()

try:
    content = download_workbook(run["run_id"], api_key, base_url=base_url)
except ApiClientError as exc:
    st.error(str(exc))
    st.stop()

st.download_button(
    label="⬇️ Excel workbook (5 sheets)",
    data=content,
    file_name=f"reconciliation_{run['run_id'][:8]}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
)

reviews = load_reviews()
inbox = exceptions_frame(buckets)
if not inbox.empty:
    annotated = inbox.copy()
    annotated["review_status"] = [
        review_status(reviews, row_key(str(r["bucket"]), r)) for _, r in annotated.iterrows()
    ]
    annotated["review_note"] = [
        reviews.get(row_key(str(r["bucket"]), r), {}).get("note", "")
        for _, r in annotated.iterrows()
    ]
    st.download_button(
        label="⬇️ Exceptions with reviews (CSV)",
        data=annotated.to_csv(index=False).encode("utf-8"),
        file_name="exceptions_with_reviews.csv",
        mime="text/csv",
        width="stretch",
    )
    if reviews:
        import json as _json

        st.download_button(
            label="⬇️ Review log (JSON)",
            data=_json.dumps(reviews, indent=2).encode("utf-8"),
            file_name="reviews.json",
            mime="application/json",
            width="stretch",
        )
