"""Reconciliation orchestration for the API (thin layer over the engine).

All matching, scoring and narrative rules live in the existing modules;
this service only wires them together and shapes the run record.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pandas as pd
from loguru import logger

from src.ai.auditor import attach_narratives
from src.engine.anomaly import score_by_reference
from src.engine.reconciler import ReconcileConfig, ReconcileResult, reconcile
from src.parsers.gl_parser import GLParseError, parse_gl_csv
from src.parsers.lhdn_parser import LHDNParseError, parse_lhdn_json
from src.reports.excel import export_workbook
from src.api.repository import RunRecord, utcnow

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB per file
PREVIEW_ROWS = 5

BUCKET_FIELDS = ("Matched", "Unsubmitted_Sales", "SST_Rate_Mismatch", "Missing_UUID")


class InputValidationError(ValueError):
    """Raised for bad uploads/tolerances; maps to HTTP 422."""


def _write_temp(data: bytes, suffix: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(data)
    finally:
        tmp.close()
    return Path(tmp.name)


def _parse_inputs(gl_bytes: bytes, lhdn_bytes: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not gl_bytes:
        raise InputValidationError("GL file is empty.")
    if not lhdn_bytes:
        raise InputValidationError("LHDN file is empty.")
    if len(gl_bytes) > MAX_UPLOAD_BYTES or len(lhdn_bytes) > MAX_UPLOAD_BYTES:
        raise InputValidationError(f"Files must be under {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB each.")
    gl_path, lhdn_path = _write_temp(gl_bytes, ".csv"), _write_temp(lhdn_bytes, ".json")
    try:
        try:
            gl_df = parse_gl_csv(gl_path)
        except GLParseError as exc:
            raise InputValidationError(f"GL parsing failed: {exc}") from exc
        try:
            lhdn_df = parse_lhdn_json(lhdn_path)
        except LHDNParseError as exc:
            raise InputValidationError(f"LHDN parsing failed: {exc}") from exc
        return gl_df, lhdn_df
    finally:
        gl_path.unlink(missing_ok=True)
        lhdn_path.unlink(missing_ok=True)


def _enrich(result: ReconcileResult, gl_df: pd.DataFrame, ai_narratives: bool) -> ReconcileResult:
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
    if ai_narratives:
        narrated = attach_narratives(result.buckets(), enabled=True)
        result = ReconcileResult(
            matched=narrated["Matched"],
            unsubmitted_sales=narrated["Unsubmitted_Sales"],
            sst_rate_mismatch=narrated["SST_Rate_Mismatch"],
            missing_uuid=narrated["Missing_UUID"],
            summary=result.summary,
        )
    return result


def _preview_frame(frame: pd.DataFrame, n: int = PREVIEW_ROWS) -> list[dict]:
    if frame.empty:
        return []
    preview = frame.head(n).copy()
    for col in ("transaction_date", "invoice_date_lhdn"):
        if col in preview.columns:
            preview[col] = pd.to_datetime(preview[col], errors="coerce").dt.strftime("%Y-%m-%d")
    return preview.fillna("").to_dict(orient="records")


def run_reconciliation(
    gl_bytes: bytes,
    lhdn_bytes: bytes,
    date_tolerance: int = 2,
    amount_tolerance: float = 0.05,
    sst_tolerance: float = 0.05,
    ai_narratives: bool = False,
) -> tuple[ReconcileResult, bytes]:
    """Full pipeline: parse -> match -> score -> (narrate) -> workbook bytes."""
    if not (0 <= date_tolerance <= 30):
        raise InputValidationError("date_tolerance must be between 0 and 30 days.")
    for name, value in (("amount_tolerance", amount_tolerance), ("sst_tolerance", sst_tolerance)):
        if not (0.0 <= value <= 100.0):
            raise InputValidationError(f"{name} must be between 0 and 100 RM.")
    gl_df, lhdn_df = _parse_inputs(gl_bytes, lhdn_bytes)
    cfg = ReconcileConfig(date_tolerance_days=date_tolerance,
                          amount_tolerance_rm=amount_tolerance,
                          sst_tolerance_rm=sst_tolerance)
    try:
        result = reconcile(gl_df, lhdn_df, cfg)
    except ValueError as exc:
        raise InputValidationError(f"Reconciliation failed: {exc}") from exc
    result = _enrich(result, gl_df, ai_narratives)
    out_path = Path(tempfile.mkdtemp()) / "reconciliation_summary.xlsx"
    try:
        export_workbook(result, out_path)
        workbook = out_path.read_bytes()
    finally:
        out_path.unlink(missing_ok=True)
        out_path.parent.rmdir()
    logger.info("API run complete: GL={} LHDN={} matched={}",
                len(gl_df), len(lhdn_df), result.summary["matched"])
    return result, workbook


def to_record(result: ReconcileResult, workbook: bytes) -> RunRecord:
    buckets = result.buckets()
    return RunRecord(
        run_id=uuid.uuid4().hex,
        created_at=utcnow(),
        summary=result.summary,
        bucket_counts={name: len(buckets[name]) for name in BUCKET_FIELDS},
        workbook_bytes=workbook,
        buckets_preview={name: _preview_frame(buckets[name]) for name in BUCKET_FIELDS},
    )
