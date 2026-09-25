"""Backward-compatible reconciliation façade over :mod:`src.engine.matcher`.

All matching logic lives in ``matcher`` (deterministic Pandas only).
This module preserves the historic ``reconcile`` API, bucket names
(``Matched`` / ``Unsubmitted_Sales`` / ``SST_Rate_Mismatch`` / ``Missing_UUID``)
and ``Pass``-style stage labels expected by the CLI, UI and Excel exporter.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.engine.matcher import MatchConfig, MatchResult, match

BUCKETS = ("Matched", "Unsubmitted_Sales", "SST_Rate_Mismatch", "Missing_UUID")


@dataclass
class ReconcileConfig:
    date_tolerance_days: int = 2
    amount_tolerance_rm: float = 0.05
    sst_tolerance_rm: float = 0.05

    def to_match_config(self) -> MatchConfig:
        return MatchConfig(
            date_tolerance_days=self.date_tolerance_days,
            amount_tolerance_rm=self.amount_tolerance_rm,
            sst_tolerance_rm=self.sst_tolerance_rm,
        )


@dataclass
class ReconcileResult:
    matched: pd.DataFrame
    unsubmitted_sales: pd.DataFrame
    sst_rate_mismatch: pd.DataFrame
    missing_uuid: pd.DataFrame
    summary: dict

    def buckets(self) -> dict[str, pd.DataFrame]:
        return {
            "Matched": self.matched,
            "Unsubmitted_Sales": self.unsubmitted_sales,
            "SST_Rate_Mismatch": self.sst_rate_mismatch,
            "Missing_UUID": self.missing_uuid,
        }


def _rename_summary(summary: dict) -> dict:
    renamed = dict(summary)
    renamed["unsubmitted_sales"] = renamed.pop("unsubmitted")
    return renamed


def reconcile(
    gl: pd.DataFrame,
    lhdn: pd.DataFrame,
    config: ReconcileConfig | None = None,
) -> ReconcileResult:
    """Reconcile GL rows against LHDN documents. Never mutates the inputs."""
    cfg = config or ReconcileConfig()
    result: MatchResult = match(gl, lhdn, cfg.to_match_config())
    return ReconcileResult(
        matched=result.matched,
        unsubmitted_sales=result.unsubmitted,
        sst_rate_mismatch=result.mismatch,
        missing_uuid=result.missing_uuid,
        summary=_rename_summary(result.summary),
    )
