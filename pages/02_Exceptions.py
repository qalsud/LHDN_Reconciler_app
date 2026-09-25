"""Exceptions inbox — review every non-matched row, mark decisions, persist."""

from __future__ import annotations

import streamlit as st

from src.reports.tables import display_frame, style_bucket
from src.ui.store import (
    EXCEPTION_BUCKETS,
    exceptions_frame,
    get_result,
    load_reviews,
    mark_pending,
    mark_reviewed,
    review_progress,
    review_status,
    row_key,
    save_reviews,
)

st.set_page_config(page_title="Reconciliation — Exceptions", page_icon="📥", layout="wide")
st.title("📥 Exceptions inbox")
st.caption("Step 3 of 4 — work every exception. Decisions persist to "
           "`output/reviews.json` and flow into Exports.")

result = get_result(st.session_state)
if result is None:
    st.info("Run a reconciliation on the Upload page first.")
    st.page_link("app.py", label="Go to Upload & Run", icon="🧾")
    st.stop()

reviews = load_reviews()
inbox = exceptions_frame(result.buckets())
if inbox.empty:
    st.success("No exceptions — everything matched.")
    st.stop()

prog = review_progress(inbox, reviews)
st.progress(prog["fraction"], text=f"{prog['reviewed']}/{prog['total']} reviewed")

bucket_filter = st.multiselect("Bucket", list(EXCEPTION_BUCKETS), default=list(EXCEPTION_BUCKETS))
status_filter = st.selectbox("Review status", ["All", "Pending", "Reviewed"])
view = inbox[inbox["bucket"].isin(bucket_filter)].copy()
if status_filter != "All":
    keep = []
    for _, row in view.iterrows():
        status = review_status(reviews, row_key(str(row["bucket"]), row))
        keep.append((status == "reviewed") == (status_filter == "Reviewed"))
    view = view[keep]

if view.empty:
    st.info("No rows match the current filters.")
    st.stop()

for idx, row in view.iterrows():
    key = row_key(str(row["bucket"]), row)
    status = review_status(reviews, key)
    icon = "✅" if status == "reviewed" else "⏳"
    ref = row.get("invoice_reference_gl") or row.get("invoice_reference_lhdn") or "?"
    title = f"{icon} [{row['bucket']}] {ref} · SST var {row.get('sst_variance', '?')} · score {row.get('anomaly_score', '?')}"
    with st.expander(title):
        st.dataframe(style_bucket(display_frame(row.to_frame().T),
                                  "sst" if row["bucket"] == "SST_Rate_Mismatch" else
                                  "missing" if row["bucket"] == "Missing_UUID" else "plain"),
                     width="stretch")
        note = st.text_input("Reviewer note", value=reviews.get(key, {}).get("note", ""),
                             key=f"note-{key}")
        col_a, col_b = st.columns(2)
        if col_a.button("Mark reviewed", key=f"rev-{key}", width="stretch"):
            reviews = mark_reviewed(reviews, key, note=note)
            save_reviews(reviews)
            st.rerun()
        if col_b.button("Reopen", key=f"open-{key}", width="stretch"):
            reviews = mark_pending(reviews, key)
            save_reviews(reviews)
            st.rerun()
