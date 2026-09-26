"""Upload & Run — entry page. Submits to the API (auth required)."""

from __future__ import annotations

import streamlit as st

from src.config.settings import settings
from src.ui.api_client import (ApiClientError, api_base_url, check_health,
                               fetch_buckets, submit_reconciliation)
from src.ui.store import has_result, store_frames

st.set_page_config(page_title="Reconciliation — Upload", page_icon="🧾", layout="wide")
st.title("🧾 Upload & Run")
st.caption("Step 1 of 4 — runs through the API, so tenant isolation, audit "
           "and rate limits apply to UI runs too.")

with st.sidebar:
    st.header("API connection")
    base_url = st.text_input("API base URL", value=api_base_url())
    api_key = st.text_input("Tenant API key", value=st.session_state.get("api_key", ""),
                            type="password",
                            help="Mint one via POST /api/v1/admin/tenants.")
    try:
        health = check_health(base_url)
        st.success(f"API v{health.get('version')} reachable")
    except ApiClientError as exc:
        st.error(str(exc))
        st.stop()

    st.header("Inputs")
    use_samples = st.checkbox("Use bundled sample files", value=True)
    gl_file = st.file_uploader("General Ledger CSV", type=["csv"], disabled=use_samples)
    lhdn_file = st.file_uploader("LHDN JSON export", type=["json"], disabled=use_samples)

    st.header("Tolerances")
    date_tol = st.slider("Date window (± days)", 0, 7, settings.date_tolerance_days)
    amount_tol = st.slider("Total tolerance (± RM)", 0.0, 1.0, settings.amount_tolerance_rm,
                           step=0.01, format="RM %.2f")
    sst_tol = st.slider("SST tolerance (± RM)", 0.0, 1.0, settings.sst_tolerance_rm,
                        step=0.01, format="RM %.2f")

    st.header("Intelligence")
    want_narratives = st.checkbox(
        "Generate AI narratives (needs LLM API key server-side)",
        value=False,
    )

if st.button("Run reconciliation", type="primary", width="stretch"):
    st.session_state["api_key"] = api_key
    st.session_state["base_url"] = base_url
    if use_samples:
        gl_bytes = open("samples/gl_sample.csv", "rb").read()
        lhdn_bytes = open("samples/lhdn_sample.json", "rb").read()
        gl_name, lhdn_name = "gl_sample.csv", "lhdn_sample.json"
    elif gl_file is None or lhdn_file is None:
        st.warning("Upload both a GL CSV and an LHDN JSON file, or tick sample mode.")
        st.stop()
    else:
        gl_bytes, lhdn_bytes = gl_file.getvalue(), lhdn_file.getvalue()
        gl_name, lhdn_name = gl_file.name, lhdn_file.name

    with st.spinner("Reconciling via API…"):
        try:
            run = submit_reconciliation(
                gl_bytes, gl_name, lhdn_bytes, lhdn_name, api_key,
                date_tolerance=date_tol, amount_tolerance=amount_tol,
                sst_tolerance=sst_tol, ai_narratives=want_narratives,
                base_url=base_url)
            frames = fetch_buckets(run["run_id"], api_key, base_url=base_url)
        except ApiClientError as exc:
            st.error(str(exc))
            st.stop()
    st.session_state["run"] = run
    store_frames(st.session_state, frames)

if has_result(st.session_state) and st.session_state.get("run"):
    s = st.session_state["run"]["summary"]
    st.success(
        f"Run {st.session_state['run']['run_id'][:8]}… — "
        f"{s['gl_total']} GL × {s['lhdn_total']} LHDN → "
        f"Matched={s['matched']} · Unsubmitted={s.get('unsubmitted_sales', s.get('unsubmitted'))} · "
        f"SST mismatch={s['sst_rate_mismatch']} · Missing UUID={s['missing_uuid']}"
    )
    st.page_link("pages/01_Dashboard.py", label="Next: open the Dashboard", icon="📊")
else:
    st.info("Configure inputs in the sidebar and press **Run reconciliation**.")
