"""Exports — workbook / CSV downloads plus the review log."""

from __future__ import annotations

import streamlit as st

from src.config.settings import settings
from src.reports.excel import export_workbook
from src.ui.store import exceptions_frame, get_result, load_reviews, review_status, row_key

st.set_page_config(page_title="Reconciliation — Exports", page_icon="📤", layout="wide")
st.title("📤 Exports")
st.caption("Step 4 of 4 — download the audit pack.")

result = get_result(st.session_state)
if result is None:
    st.info("Run a reconciliation on the Upload page first.")
    st.page_link("app.py", label="Go to Upload & Run", icon="🧾")
    st.stop()

try:
    out_path = export_workbook(result, settings.output_path)
except OSError as exc:
    st.error(f"Export failed: {exc}")
    st.stop()

st.download_button(
    label="⬇️ Excel workbook (5 sheets)",
    data=out_path.read_bytes(),
    file_name=out_path.name,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
)
st.caption(f"Also saved to `{out_path}`.")

reviews = load_reviews()
inbox = exceptions_frame(result.buckets())
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
