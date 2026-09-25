"""General Ledger CSV parser.

Expected logical columns (header aliases are normalised, case-insensitive):
  - Transaction Date : transaction_date | transaction date | date | invoice_date
  - TIN              : tin | tax_identification_number | tax id
  - Invoice Ref      : invoice_reference | invoice ref | invoice_no | invoice no | reference | inv_ref
  - Subtotal         : subtotal | sub_total | net_amount | net amount
  - SST Amount       : sst_amount | sst amount | sst | tax_amount | tax amount
  - Total Amount     : total_amount | total amount | total | grand_total | grand total
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

CANONICAL_COLUMNS = [
    "transaction_date",
    "tin",
    "invoice_reference",
    "subtotal",
    "sst_amount",
    "total_amount",
]

_HEADER_ALIASES: dict[str, str] = {
    # date
    "transaction_date": "transaction_date",
    "transaction date": "transaction_date",
    "date": "transaction_date",
    "invoice_date": "transaction_date",
    "invoice date": "transaction_date",
    # tin
    "tin": "tin",
    "tax_identification_number": "tin",
    "tax identification number": "tin",
    "tax id": "tin",
    "tax_id": "tin",
    # reference
    "invoice_reference": "invoice_reference",
    "invoice reference": "invoice_reference",
    "invoice ref": "invoice_reference",
    "invoice_no": "invoice_reference",
    "invoice no": "invoice_reference",
    "reference": "invoice_reference",
    "inv_ref": "invoice_reference",
    "inv ref": "invoice_reference",
    # subtotal
    "subtotal": "subtotal",
    "sub_total": "subtotal",
    "sub total": "subtotal",
    "net_amount": "subtotal",
    "net amount": "subtotal",
    # sst
    "sst_amount": "sst_amount",
    "sst amount": "sst_amount",
    "sst": "sst_amount",
    "tax_amount": "sst_amount",
    "tax amount": "sst_amount",
    # total
    "total_amount": "total_amount",
    "total amount": "total_amount",
    "total": "total_amount",
    "grand_total": "total_amount",
    "grand total": "total_amount",
}


class GLParseError(ValueError):
    """Raised when a GL file cannot be parsed or validated."""


def _normalise_header(name: str) -> str:
    return " ".join(str(name).strip().lower().replace("_", " ").split())


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping: dict[str, str] = {}
    for col in df.columns:
        key = _normalise_header(col).replace(" ", "_")
        alias_key = _normalise_header(col)
        canonical = _HEADER_ALIASES.get(alias_key) or _HEADER_ALIASES.get(key)
        if canonical:
            mapping[col] = canonical
    return df.rename(columns=mapping)


def parse_gl_csv(path: str | Path) -> pd.DataFrame:
    """Parse a General Ledger CSV into a canonical DataFrame.

    Raises:
        GLParseError: on missing file, empty file, missing columns, or bad values.
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise GLParseError(f"GL CSV not found: {csv_path}")

    try:
        df = pd.read_csv(csv_path)
    except pd.errors.EmptyDataError as exc:
        raise GLParseError(f"GL CSV is empty: {csv_path}") from exc
    except Exception as exc:  # defensive: malformed CSV, encoding issues
        raise GLParseError(f"Failed to read GL CSV '{csv_path}': {exc}") from exc

    if df.empty:
        raise GLParseError(f"GL CSV contains no data rows: {csv_path}")

    df = _normalise_columns(df)

    missing = [c for c in CANONICAL_COLUMNS if c not in df.columns]
    if missing:
        raise GLParseError(
            f"GL CSV '{csv_path}' is missing required columns: {missing}. "
            f"Found: {list(df.columns)}"
        )

    try:
        out = pd.DataFrame()
        out["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
        out["tin"] = df["tin"].astype(str).str.strip()
        out["invoice_reference"] = df["invoice_reference"].astype(str).str.strip().str.upper()
        for col in ("subtotal", "sst_amount", "total_amount"):
            out[col] = pd.to_numeric(df[col], errors="coerce")
    except Exception as exc:
        raise GLParseError(f"Failed to normalise GL CSV '{csv_path}': {exc}") from exc

    bad_dates = out["transaction_date"].isna().sum()
    bad_numbers = int(out[["subtotal", "sst_amount", "total_amount"]].isna().sum().sum())
    bad_tin = int((out["tin"].isna() | (out["tin"] == "") | (out["tin"].str.upper() == "NAN")).sum())
    if bad_dates or bad_numbers or bad_tin:
        raise GLParseError(
            f"GL CSV '{csv_path}' has invalid values: "
            f"{bad_dates} bad date(s), {bad_numbers} bad numeric value(s), "
            f"{bad_tin} bad TIN(s)."
        )

    out = out.reset_index(drop=True)
    logger.info("Parsed GL CSV: {} rows from {}", len(out), csv_path)
    return out


def parse_gl_csv_auto(
    path: str | Path,
    threshold: float = 0.70,
    embed_fn=None,
) -> pd.DataFrame:
    """Parse a GL CSV with unknown vendor headers via the AI semantic mapper.

    Headers are mapped to the canonical schema with embeddings + cosine
    similarity (``src/ai/semantics.py``) before the standard validation in
    :func:`parse_gl_csv` runs on the translated frame.

    Raises:
        GLParseError: on missing/empty files or invalid values.
        HeaderMappingError: when a header scores below ``threshold``.
    """
    from src.ai.semantics import (  # lazy: keeps parser import-light
        apply_mapping,
        canonical_to_engine,
        map_headers,
    )

    csv_path = Path(path)
    if not csv_path.is_file():
        raise GLParseError(f"GL CSV not found: {csv_path}")
    try:
        raw = pd.read_csv(csv_path)
    except pd.errors.EmptyDataError as exc:
        raise GLParseError(f"GL CSV is empty: {csv_path}") from exc
    except Exception as exc:
        raise GLParseError(f"Failed to read GL CSV '{csv_path}': {exc}") from exc
    if raw.empty:
        raise GLParseError(f"GL CSV contains no data rows: {csv_path}")

    mapping = map_headers(list(raw.columns), threshold=threshold, embed_fn=embed_fn)
    canonical = apply_mapping(raw, mapping)
    engine = canonical_to_engine(canonical)

    # Reuse strict validation semantics on the translated engine frame.
    out = engine.reset_index(drop=True)
    bad_dates = int(out["transaction_date"].isna().sum())
    bad_numbers = int(out[["subtotal", "sst_amount", "total_amount"]].isna().sum().sum())
    bad_tin = int((out["tin"] == "").sum())
    if bad_dates or bad_numbers or bad_tin:
        raise GLParseError(
            f"GL CSV '{csv_path}' has invalid values after AI mapping: "
            f"{bad_dates} bad date(s), {bad_numbers} bad numeric value(s), "
            f"{bad_tin} bad TIN(s)."
        )
    logger.info("Parsed GL CSV via AI header mapping: {} rows from {}", len(out), csv_path)
    return out
