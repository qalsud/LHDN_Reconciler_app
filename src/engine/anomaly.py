"""Transaction anomaly scoring with scikit-learn (Phase 4).

Trains an :class:`~sklearn.ensemble.IsolationForest` on per-transaction
features — amounts, tax ratios and timing — and assigns every transaction an
anomaly score from 0 (normal) to 100 (highly anomalous).

Pure local computation: no network, no LLM, fully deterministic for a fixed
``random_state``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sklearn.ensemble import IsolationForest

FEATURE_COLUMNS = ["log_total", "tax_ratio", "hour", "day_of_week"]

DEFAULT_CONTAMINATION = 0.10
DEFAULT_RANDOM_STATE = 42


def build_features(gl_df: pd.DataFrame) -> pd.DataFrame:
    """Build the model feature matrix from an engine-schema GL frame."""
    df = gl_df.copy()
    dates = pd.to_datetime(df["transaction_date"], errors="coerce")
    totals = pd.to_numeric(df["total_amount"], errors="coerce").fillna(0.0)
    taxes = pd.to_numeric(df["sst_amount"], errors="coerce").fillna(0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        tax_ratio = np.where(totals.abs() > 1e-9, taxes / totals, 0.0)
    features = pd.DataFrame({
        "log_total": np.log1p(totals.clip(lower=0.0)),
        "tax_ratio": np.clip(tax_ratio, -1.0, 2.0),
        "hour": dates.dt.hour.fillna(0).astype(int),
        "day_of_week": dates.dt.dayofweek.fillna(0).astype(int),
    })
    return features.fillna(0.0)


def score_transactions(
    gl_df: pd.DataFrame,
    contamination: float = DEFAULT_CONTAMINATION,
    random_state: int = DEFAULT_RANDOM_STATE,
) -> pd.Series:
    """Score every row 0-100 (higher = more anomalous). Never mutates input."""
    features = build_features(gl_df)
    if len(features) < 2:
        logger.warning("Anomaly scoring needs >= 2 rows; assigning 0.0.")
        return pd.Series([0.0] * len(features), index=gl_df.index)
    model = IsolationForest(
        contamination=contamination, random_state=random_state, n_estimators=200,
    )
    try:
        raw = -model.fit(features).score_samples(features)  # higher = stranger
    except ValueError as exc:
        logger.warning("IsolationForest failed ({}); assigning 0.0.", exc)
        return pd.Series([0.0] * len(features), index=gl_df.index)
    lo, hi = float(raw.min()), float(raw.max())
    if hi - lo < 1e-12:
        scaled = np.zeros_like(raw)
    else:
        scaled = (raw - lo) / (hi - lo) * 100.0
    scores = pd.Series(np.round(scaled, 1), index=gl_df.index)
    logger.info("Scored {} transaction(s); max anomaly = {}", len(scores), scores.max())
    return scores


def score_by_reference(gl_df: pd.DataFrame, **kwargs) -> dict[str, float]:
    """Map normalised invoice reference -> anomaly score for report enrichment."""
    scores = score_transactions(gl_df, **kwargs)
    refs = gl_df["invoice_reference"].astype(str).str.strip().str.upper()
    return {ref: float(score) for ref, score in zip(refs, scores)}
