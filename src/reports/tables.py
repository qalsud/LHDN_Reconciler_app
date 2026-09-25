"""Presentation helpers shared by the Streamlit UI (and importable in tests).

Pure pandas logic only — no Streamlit dependency, so this module is safe
to unit-test headlessly.
"""

from __future__ import annotations

import pandas as pd

MONEY_COLS = ["subtotal_gl", "sst_gl", "sst_lhdn", "sst_variance",
              "total_gl", "total_lhdn", "total_variance"]

FRIENDLY_COLS = {
    "transaction_date": "GL Date",
    "invoice_date_lhdn": "LHDN Date",
    "tin": "TIN",
    "invoice_reference_gl": "GL Reference",
    "invoice_reference_lhdn": "LHDN Reference",
    "lhdn_uuid": "LHDN UUID",
    "subtotal_gl": "Subtotal (RM)",
    "sst_gl": "SST GL (RM)",
    "sst_lhdn": "SST LHDN (RM)",
    "sst_variance": "SST Variance (RM)",
    "total_gl": "Total GL (RM)",
    "total_lhdn": "Total LHDN (RM)",
    "total_variance": "Total Variance (RM)",
    "date_diff_days": "Date Diff (days)",
    "match_stage": "Match Stage",
    "anomaly_score": "Anomaly (0-100)",
    "ai_narrative": "AI Narrative",
}


def display_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return a presentation copy: friendly headers, 2-decimal money, ISO dates."""
    if df.empty:
        return df.copy()
    out = df.copy()
    for col in MONEY_COLS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(2)
    for col in ("transaction_date", "invoice_date_lhdn"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%d")
            out[col] = out[col].replace("NaT", "")
    return out.rename(columns=FRIENDLY_COLS)


def style_bucket(df: pd.DataFrame, kind: str):
    """Apply variance highlighting; returns a pandas Styler.

    Expects a *display* frame as produced by :func:`display_frame`
    (friendly headers), which is exactly what the UI passes in.
    """
    money_labels = [FRIENDLY_COLS[c] for c in MONEY_COLS if FRIENDLY_COLS.get(c) in df.columns]
    sst_label = FRIENDLY_COLS["sst_variance"]
    styled = df.style.format({c: "{:.2f}" for c in money_labels}, na_rep="")
    if kind == "sst" and sst_label in df.columns:
        styled = styled.map(
            lambda v: "background-color: #FFEB9C; font-weight: bold"
            if isinstance(v, (int, float)) and pd.notna(v) and abs(v) > 0.051
            else "",
            subset=[sst_label],
        )
    if kind == "missing":
        styled = styled.set_properties(**{"background-color": "#FFC7CE"})
    if kind == "matched":
        styled = styled.set_properties(**{"background-color": "#C6EFCE"})
    return styled
