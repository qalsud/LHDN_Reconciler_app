"""Dashboard — KPIs, bucket mix, variance and timeline charts."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from src.reports.tables import display_frame
from src.ui.store import get_result

st.set_page_config(page_title="Reconciliation — Dashboard", page_icon="📊", layout="wide")
st.title("📊 Dashboard")
st.caption("Step 2 of 4 — portfolio view. Drill into rows in the Exceptions inbox.")

result = get_result(st.session_state)
if result is None:
    st.info("Run a reconciliation on the Upload page first.")
    st.page_link("app.py", label="Go to Upload & Run", icon="🧾")
    st.stop()

s = result.summary
buckets = result.buckets()

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("GL records", s["gl_total"])
m2.metric("LHDN documents", s["lhdn_total"])
m3.metric("Matched", s["matched"])
m4.metric("Unsubmitted sales", s["unsubmitted_sales"])
m5.metric("SST mismatches", s["sst_rate_mismatch"])
m6.metric("Missing UUID", s["missing_uuid"])

left, right = st.columns(2)
with left:
    mix = pd.DataFrame({
        "Bucket": ["Matched", "Unsubmitted_Sales", "SST_Rate_Mismatch", "Missing_UUID"],
        "Records": [s["matched"], s["unsubmitted_sales"],
                    s["sst_rate_mismatch"], s["missing_uuid"]],
    })
    st.plotly_chart(
        px.pie(mix, names="Bucket", values="Records", hole=0.45,
               title="Bucket mix", color="Bucket",
               color_discrete_map={"Matched": "#2ca02c",
                                   "Unsubmitted_Sales": "#7f7f7f",
                                   "SST_Rate_Mismatch": "#ffbb00",
                                   "Missing_UUID": "#d62728"}),
        width="stretch",
    )
with right:
    fin = buckets["SST_Rate_Mismatch"]
    if fin.empty:
        st.info("No SST mismatches — nothing to chart.")
    else:
        st.plotly_chart(
            px.bar(fin.assign(ref=fin["invoice_reference_gl"]),
                   x="ref", y="sst_variance", title="SST variance by invoice (RM)",
                   labels={"ref": "GL reference", "sst_variance": "SST variance (RM)"},
                   color="sst_variance", color_continuous_scale="RdYlGn_r"),
            width="stretch",
        )

matched = buckets["Matched"]
if not matched.empty and "anomaly_score" in matched.columns:
    scores = matched.assign(score=pd.to_numeric(matched["anomaly_score"], errors="coerce"))
    st.plotly_chart(
        px.histogram(scores, x="score", nbins=20, title="Anomaly-score distribution (matched rows)",
                     labels={"score": "Anomaly score (0-100)"}),
        width="stretch",
    )

with st.expander("Preview matched rows"):
    st.dataframe(display_frame(buckets["Matched"].head(50)), width="stretch")
