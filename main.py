"""CLI entry point: LHDN MyInvois vs General Ledger Reconciliation Engine."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from src.config.settings import settings
from src.ai.auditor import attach_narratives
from src.engine.anomaly import score_by_reference
from src.engine.reconciler import ReconcileConfig, ReconcileResult, reconcile
from src.parsers.gl_parser import GLParseError, parse_gl_csv
from src.parsers.lhdn_parser import LHDNParseError, parse_lhdn_json
from src.reports.excel import export_workbook


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Reconcile General Ledger sales against LHDN MyInvois submissions.",
    )
    p.add_argument("--gl-path", default=str(settings.gl_path), help="Path to GL CSV file.")
    p.add_argument("--lhdn-path", default=str(settings.lhdn_path), help="Path to LHDN JSON export.")
    p.add_argument("--output", "-o", default=str(settings.output_path),
                   help="Output Excel workbook path.")
    p.add_argument("--date-tolerance", type=int, default=settings.date_tolerance_days,
                   help="Fuzzy date window in days (default: 2).")
    p.add_argument("--amount-tolerance", type=float, default=settings.amount_tolerance_rm,
                   help="Total-amount tolerance in RM (default: 0.05).")
    p.add_argument("--sst-tolerance", type=float, default=settings.sst_tolerance_rm,
                   help="SST-amount tolerance in RM (default: 0.05).")
    p.add_argument("--ai-narratives", action="store_true",
                   help="Generate LLM audit narratives for Mismatch / high-anomaly rows "
                        "(needs an LLM API key; template fallback otherwise).")
    p.add_argument("--log-level", default=settings.log_level, help="Loguru log level.")
    return p


def _enrich_with_intelligence(result: ReconcileResult, gl_df, ai_narratives: bool) -> ReconcileResult:
    """Attach IsolationForest anomaly scores and (optionally) AI narratives."""
    scores = score_by_reference(gl_df)
    scored: dict[str, object] = {}
    for name, frame in result.buckets().items():
        enriched = frame.copy()
        enriched["anomaly_score"] = (
            enriched["invoice_reference_gl"].astype(str).str.strip().str.upper().map(scores)
        )
        scored[name] = enriched
    narrated = attach_narratives(scored, enabled=ai_narratives)
    return ReconcileResult(
        matched=narrated["Matched"],
        unsubmitted_sales=narrated["Unsubmitted_Sales"],
        sst_rate_mismatch=narrated["SST_Rate_Mismatch"],
        missing_uuid=narrated["Missing_UUID"],
        summary=result.summary,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level=str(args.log_level).upper())

    logger.info("GL input: {}", args.gl_path)
    logger.info("LHDN input: {}", args.lhdn_path)
    try:
        gl_df = parse_gl_csv(args.gl_path)
        lhdn_df = parse_lhdn_json(args.lhdn_path)
    except (GLParseError, LHDNParseError) as exc:
        logger.error("Input parsing failed: {}", exc)
        return 2

    cfg = ReconcileConfig(
        date_tolerance_days=args.date_tolerance,
        amount_tolerance_rm=args.amount_tolerance,
        sst_tolerance_rm=args.sst_tolerance,
    )
    try:
        result = reconcile(gl_df, lhdn_df, cfg)
    except ValueError as exc:
        logger.error("Reconciliation failed: {}", exc)
        return 3

    result = _enrich_with_intelligence(result, gl_df, ai_narratives=args.ai_narratives)

    try:
        out = export_workbook(result, args.output)
    except OSError as exc:
        logger.error("Export failed: {}", exc)
        return 4

    s = result.summary
    logger.info(
        "Done. GL={} LHDN={} | Matched={} Unsubmitted={} SST_Mismatch={} Missing_UUID={} -> {}",
        s["gl_total"], s["lhdn_total"], s["matched"], s["unsubmitted_sales"],
        s["sst_rate_mismatch"], s["missing_uuid"], out,
    )
    print(
        f"Matched={s['matched']} Unsubmitted_Sales={s['unsubmitted_sales']} "
        f"SST_Rate_Mismatch={s['sst_rate_mismatch']} Missing_UUID={s['missing_uuid']} "
        f"-> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
