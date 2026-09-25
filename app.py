"""Upload & Run — entry page of the reconciliation workflow app.

Flow: Upload -> Dashboard -> Exceptions inbox -> Exports.
Run with:  streamlit run app.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from src.ai.auditor import attach_narratives
from src.config.settings import settings
from src.engine.anomaly import score_by_reference
from src.engine.reconciler import ReconcileConfig, ReconcileResult, reconcile
from src.parsers.gl_parser import GLParseError, parse_gl_csv
from src.parsers.lhdn_parser import LHDNParseError, parse_lhdn_json
from src.ui.store import has_result, store_result

st.set_page_config(page_title="Reconciliation — Upload", page_icon="🧾", layout="wide")
st.title("🧾 Upload & Run")
st.caption("Step 1 of 4 — load inputs, reconcile, then work the Dashboard, "
           "Exceptions inbox and Exports pages in the sidebar.")


def _save_upload(uploaded, suffix: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(uploaded.getvalue())
    finally:
        tmp.close()
    return Path(tmp.name)


def _enrich(result: ReconcileResult, gl_df, want_narratives: bool) -> ReconcileResult:
    scores = score_by_reference(gl_df)
    scored = {}
    for name, frame in result.buckets().items():
        enriched = frame.copy()
        enriched["anomaly_score"] = (
            enriched["invoice_reference_gl"].astype(str).str.strip().str.upper().map(scores)
        )
        scored[name] = enriched
    result = ReconcileResult(
        matched=scored["Matched"],
        unsubmitted_sales=scored["Unsubmitted_Sales"],
        sst_rate_mismatch=scored["SST_Rate_Mismatch"],
        missing_uuid=scored["Missing_UUID"],
        summary=result.summary,
    )
    if want_narratives:
        narrated = attach_narratives(result.buckets(), enabled=True)
        result = ReconcileResult(
            matched=narrated["Matched"],
            unsubmitted_sales=narrated["Unsubmitted_Sales"],
            sst_rate_mismatch=narrated["SST_Rate_Mismatch"],
            missing_uuid=narrated["Missing_UUID"],
            summary=result.summary,
        )
    return result


with st.sidebar:
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
        "Generate AI narratives (needs LLM API key)",
        value=False,
        help="2-sentence audit summaries for Mismatch / high-anomaly rows.",
    )

if st.button("Run reconciliation", type="primary", width="stretch"):
    cfg = ReconcileConfig(date_tolerance_days=date_tol,
                          amount_tolerance_rm=amount_tol,
                          sst_tolerance_rm=sst_tol)
    if use_samples:
        gl_path, lhdn_path = Path("samples/gl_sample.csv"), Path("samples/lhdn_sample.json")
    elif gl_file is None or lhdn_file is None:
        st.warning("Upload both a GL CSV and an LHDN JSON file, or tick sample mode.")
        st.stop()
    else:
        gl_path, lhdn_path = _save_upload(gl_file, ".csv"), _save_upload(lhdn_file, ".json")

    try:
        gl_df = parse_gl_csv(gl_path)
        lhdn_df = parse_lhdn_json(lhdn_path)
    except (GLParseError, LHDNParseError) as exc:
        st.error(f"Input parsing failed: {exc}")
        st.stop()

    with st.spinner("Reconciling and scoring…"):
        try:
            result = reconcile(gl_df, lhdn_df, cfg)
            result = _enrich(result, gl_df, want_narratives)
        except ValueError as exc:
            st.error(f"Reconciliation failed: {exc}")
            st.stop()
    store_result(st.session_state, result)

if has_result(st.session_state):
    s = st.session_state["result"].summary
    st.success(
        f"Reconciled {s['gl_total']} GL × {s['lhdn_total']} LHDN → "
        f"Matched={s['matched']} · Unsubmitted={s['unsubmitted_sales']} · "
        f"SST mismatch={s['sst_rate_mismatch']} · Missing UUID={s['missing_uuid']}"
    )
    st.page_link("pages/01_Dashboard.py", label="Next: open the Dashboard", icon="📊")
else:
    st.info("Configure inputs in the sidebar and press **Run reconciliation**.")
