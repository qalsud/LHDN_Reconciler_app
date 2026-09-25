"""Deterministic 2-pass GL vs. LHDN matching engine (Phase 1 core).

This module is the canonical home of the matching algorithm. AI must never
perform financial arithmetic — all amount/date comparisons live here in Pandas.

Canonical input schema (engine columns):
  GL:   transaction_date, tin, invoice_reference, subtotal, sst_amount, total_amount
  LHDN: lhdn_uuid, tin, invoice_date, invoice_reference, sst_amount, total_amount

Pass 1 (exact): same TIN + same normalised invoice reference.
Pass 2 (fuzzy): same TIN + |date diff| <= date_tolerance_days
                + |total diff| <= amount_tolerance_rm.

Buckets:
  Matched           — counterpart found, Total and SST within tolerance.
  SST_Rate_Mismatch — counterpart found, SST (or Total on exact-ref) outside tolerance.
  Missing_UUID      — fuzzy financial match with a different reference (UUID
                      unlinked), plus LHDN-only documents with no GL counterpart.
  Unsubmitted       — GL rows with no counterpart at either pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

GL_COLUMNS = ["transaction_date", "tin", "invoice_reference",
              "subtotal", "sst_amount", "total_amount"]
LHDN_COLUMNS = ["lhdn_uuid", "tin", "invoice_date",
                "invoice_reference", "sst_amount", "total_amount"]

OUTPUT_COLUMNS = [
    "transaction_date", "invoice_date_lhdn", "tin",
    "invoice_reference_gl", "invoice_reference_lhdn", "lhdn_uuid",
    "subtotal_gl", "sst_gl", "sst_lhdn", "sst_variance",
    "total_gl", "total_lhdn", "total_variance",
    "date_diff_days", "match_stage",
    "anomaly_score", "ai_narrative",
]

BUCKETS = ("Matched", "Unsubmitted", "SST_Rate_Mismatch", "Missing_UUID")


@dataclass
class MatchConfig:
    date_tolerance_days: int = 2
    amount_tolerance_rm: float = 0.05
    sst_tolerance_rm: float = 0.05


@dataclass
class MatchResult:
    matched: pd.DataFrame
    unsubmitted: pd.DataFrame
    mismatch: pd.DataFrame
    missing_uuid: pd.DataFrame
    summary: dict = field(default_factory=dict)

    def buckets(self) -> dict[str, pd.DataFrame]:
        return {
            "Matched": self.matched,
            "Unsubmitted": self.unsubmitted,
            "SST_Rate_Mismatch": self.mismatch,
            "Missing_UUID": self.missing_uuid,
        }


def _prepare(gl: pd.DataFrame, lhdn: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if missing := set(GL_COLUMNS) - set(gl.columns):
        raise ValueError(f"GL frame missing columns: {sorted(missing)}")
    if missing := set(LHDN_COLUMNS) - set(lhdn.columns):
        raise ValueError(f"LHDN frame missing columns: {sorted(missing)}")
    gl = gl.copy()
    lhdn = lhdn.copy()
    # Authentic API datetimes carry UTC offsets while ledger dates are naive
    # (and a column may mix both): normalise everything to naive UTC.
    gl["transaction_date"] = pd.to_datetime(
        gl["transaction_date"], errors="coerce", utc=True).dt.tz_convert(None)
    lhdn["invoice_date"] = pd.to_datetime(
        lhdn["invoice_date"], errors="coerce", utc=True).dt.tz_convert(None)
    if gl["transaction_date"].isna().any() or lhdn["invoice_date"].isna().any():
        raise ValueError("Unparseable dates found in GL or LHDN input.")
    gl["_tin"] = gl["tin"].astype(str).str.strip()
    lhdn["_tin"] = lhdn["tin"].astype(str).str.strip()
    gl["_ref"] = gl["invoice_reference"].astype(str).str.strip().str.upper()
    lhdn["_ref"] = lhdn["invoice_reference"].astype(str).str.strip().str.upper()
    for col in ("subtotal", "sst_amount", "total_amount"):
        gl[col] = pd.to_numeric(gl[col], errors="coerce")
    for col in ("sst_amount", "total_amount"):
        lhdn[col] = pd.to_numeric(lhdn[col], errors="coerce")
    if gl[["subtotal", "sst_amount", "total_amount"]].isna().any().any():
        raise ValueError("Non-numeric amounts found in GL input.")
    if lhdn[["sst_amount", "total_amount"]].isna().any().any():
        raise ValueError("Non-numeric amounts found in LHDN input.")
    return gl, lhdn


def _combine(gl_row: pd.Series, lhdn_row: pd.Series | None, stage: str) -> dict:
    if lhdn_row is None:
        return {
            "transaction_date": gl_row["transaction_date"],
            "invoice_date_lhdn": pd.NaT,
            "tin": gl_row["tin"],
            "invoice_reference_gl": gl_row["invoice_reference"],
            "invoice_reference_lhdn": "",
            "lhdn_uuid": "",
            "subtotal_gl": gl_row["subtotal"],
            "sst_gl": gl_row["sst_amount"],
            "sst_lhdn": float("nan"),
            "sst_variance": float("nan"),
            "total_gl": gl_row["total_amount"],
            "total_lhdn": float("nan"),
            "total_variance": float("nan"),
            "date_diff_days": None,
            "match_stage": stage,
            "anomaly_score": float("nan"),
            "ai_narrative": "",
        }
    return {
        "transaction_date": gl_row["transaction_date"],
        "invoice_date_lhdn": lhdn_row["invoice_date"],
        "tin": gl_row["tin"],
        "invoice_reference_gl": gl_row["invoice_reference"],
        "invoice_reference_lhdn": lhdn_row["invoice_reference"],
        "lhdn_uuid": lhdn_row["lhdn_uuid"],
        "subtotal_gl": gl_row["subtotal"],
        "sst_gl": gl_row["sst_amount"],
        "sst_lhdn": lhdn_row["sst_amount"],
        "sst_variance": round(float(gl_row["sst_amount"]) - float(lhdn_row["sst_amount"]), 2),
        "total_gl": gl_row["total_amount"],
        "total_lhdn": lhdn_row["total_amount"],
        "total_variance": round(float(gl_row["total_amount"]) - float(lhdn_row["total_amount"]), 2),
        "date_diff_days": abs((pd.Timestamp(gl_row["transaction_date"]).normalize()
                               - pd.Timestamp(lhdn_row["invoice_date"]).normalize()).days),
        "match_stage": stage,
        "anomaly_score": float("nan"),
        "ai_narrative": "",
    }


def _frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def match(gl: pd.DataFrame, lhdn: pd.DataFrame,
          config: MatchConfig | None = None) -> MatchResult:
    """Run the 2-pass reconciliation. Inputs are never mutated."""
    cfg = config or MatchConfig()
    gl_p, lhdn_p = _prepare(gl, lhdn)

    by_key: dict[tuple[str, str], list[int]] = {}
    for pos, row in lhdn_p.iterrows():
        by_key.setdefault((row["_tin"], row["_ref"]), []).append(pos)

    used: set[int] = set()
    matched_rows: list[dict] = []
    mismatch_rows: list[dict] = []
    missing_rows: list[dict] = []
    pending: list[int] = []

    # ---- Pass 1: exact TIN + reference --------------------------------------
    for gl_pos, grow in gl_p.iterrows():
        options = [p for p in by_key.get((grow["_tin"], grow["_ref"]), []) if p not in used]
        if not options:
            pending.append(gl_pos)
            continue
        best = min(options, key=lambda p: abs(float(grow["total_amount"])
                                              - float(lhdn_p.loc[p, "total_amount"])))
        used.add(best)
        row = _combine(grow, lhdn_p.loc[best], "Pass1_Exact")
        total_ok = abs(row["total_variance"]) <= cfg.amount_tolerance_rm + 1e-9
        sst_ok = abs(row["sst_variance"]) <= cfg.sst_tolerance_rm + 1e-9
        (matched_rows if (total_ok and sst_ok) else mismatch_rows).append(row)

    # ---- Pass 2: fuzzy TIN + date window + total tolerance -------------------
    unmatched: list[int] = []
    for gl_pos in pending:
        grow = gl_p.loc[gl_pos]
        best_pos: int | None = None
        best_score: tuple[float, float] | None = None
        for lpos, lrow in lhdn_p.iterrows():
            if lpos in used or lrow["_tin"] != grow["_tin"]:
                continue
            date_diff = abs((pd.Timestamp(grow["transaction_date"]).normalize()
                             - pd.Timestamp(lrow["invoice_date"]).normalize()).days)
            if date_diff > cfg.date_tolerance_days:
                continue
            total_diff = abs(float(grow["total_amount"]) - float(lrow["total_amount"]))
            if total_diff > cfg.amount_tolerance_rm + 1e-9:
                continue
            score = (total_diff, float(date_diff))
            if best_score is None or score < best_score:
                best_score, best_pos = score, lpos
        if best_pos is None:
            unmatched.append(gl_pos)
            continue
        used.add(best_pos)
        row = _combine(grow, lhdn_p.loc[best_pos], "Pass2_Fuzzy")
        sst_ok = abs(row["sst_variance"]) <= cfg.sst_tolerance_rm + 1e-9
        same_ref = grow["_ref"] == lhdn_p.loc[best_pos, "_ref"]
        if sst_ok and same_ref:
            matched_rows.append(row)
        elif not sst_ok:
            mismatch_rows.append(row)
        else:
            missing_rows.append(row)

    # ---- Leftovers ------------------------------------------------------------
    unsubmitted_rows = [_combine(gl_p.loc[p], None, "Unmatched") for p in unmatched]
    for lpos, lrow in lhdn_p.iterrows():
        if lpos not in used:
            missing_rows.append({
                "transaction_date": pd.NaT,
                "invoice_date_lhdn": lrow["invoice_date"],
                "tin": lrow["tin"],
                "invoice_reference_gl": "",
                "invoice_reference_lhdn": lrow["invoice_reference"],
                "lhdn_uuid": lrow["lhdn_uuid"],
                "subtotal_gl": float("nan"),
                "sst_gl": float("nan"),
                "sst_lhdn": lrow["sst_amount"],
                "sst_variance": float("nan"),
                "total_gl": float("nan"),
                "total_lhdn": lrow["total_amount"],
                "total_variance": float("nan"),
                "date_diff_days": None,
                "match_stage": "LHDN_Only",
                "anomaly_score": float("nan"),
                "ai_narrative": "",
            })

    matched = _frame(matched_rows)
    unsubmitted = _frame(unsubmitted_rows)
    mismatch = _frame(mismatch_rows)
    missing = _frame(missing_rows)
    summary = {
        "gl_total": int(len(gl_p)),
        "lhdn_total": int(len(lhdn_p)),
        "matched": int(len(matched)),
        "unsubmitted": int(len(unsubmitted)),
        "sst_rate_mismatch": int(len(mismatch)),
        "missing_uuid": int(len(missing)),
        "date_tolerance_days": cfg.date_tolerance_days,
        "amount_tolerance_rm": cfg.amount_tolerance_rm,
        "sst_tolerance_rm": cfg.sst_tolerance_rm,
    }
    logger.info(
        "Match complete: {} GL x {} LHDN -> matched={} unsubmitted={} mismatch={} missing_uuid={}",
        summary["gl_total"], summary["lhdn_total"], summary["matched"],
        summary["unsubmitted"], summary["sst_rate_mismatch"], summary["missing_uuid"],
    )
    return MatchResult(matched=matched, unsubmitted=unsubmitted,
                       mismatch=mismatch, missing_uuid=missing, summary=summary)
