"""LHDN MyInvois JSON export parser.

Accepts the two shapes commonly seen in the wild:

1. A plain JSON array of document objects.
2. An object wrapping the array, e.g. ``{"documents": [...]}``,
   ``{"invoices": [...]}``, ``{"data": [...]}`` or ``{"result": [...]}``.

Field aliases (case-insensitive, ``_``/`` `` equivalent):
  - UUID   : uuid | lhdn_uuid | id | document_id | documentid | internal_id
  - TIN    : tin | supplier_tin | suppliertin | receiver_tin | tax_id
  - Date   : invoice_date | invoice date | date | issued_date | datetime_issued
  - Ref    : invoice_reference | invoice ref | invoice_no | internal_id | id_number
  - SST    : sst_amount | sst | tax_amount | total_tax | sstamount
  - Total  : total_amount | total | grand_total | total_payable | invoice_total
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from loguru import logger

CANONICAL_COLUMNS = [
    "lhdn_uuid",
    "tin",
    "invoice_date",
    "invoice_reference",
    "sst_amount",
    "total_amount",
]

_UUID_KEYS = ("uuid", "lhdnuuid", "documentid", "internalid", "id")
_TIN_KEYS = ("tin", "suppliertin", "issuertin", "receivertin", "taxid")
_DATE_KEYS = ("invoicedate", "issueddate", "datetimeissued", "date")
_REF_KEYS = ("invoicereference", "invoiceref", "invoiceno", "idnumber", "reference", "internalid")
_SST_KEYS = ("sstamount", "taxamount", "totaltax", "sst")
_TOTAL_KEYS = ("totalamount", "grandtotal", "totalpayable", "totalpayableamount", "invoicetotal", "total")


class LHDNParseError(ValueError):
    """Raised when an LHDN JSON export cannot be parsed or validated."""


def _norm_key(key: str) -> str:
    """Canonical key form: lowercase alphanumeric only.

    This makes ``supplierTIN``, ``supplier_tin`` and ``supplier tin``
    collapse to the same key (``suppliertin``).
    """
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _pick(record: dict, candidates: tuple[str, ...]) -> object | None:
    normalised = {_norm_key(k): v for k, v in record.items()}
    for cand in candidates:
        if cand in normalised and normalised[cand] not in (None, ""):
            return normalised[cand]
    return None


def _extract_documents(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for wrapper in ("documents", "invoices", "data", "result", "items"):
            if isinstance(payload.get(wrapper), list):
                return [r for r in payload[wrapper] if isinstance(r, dict)]
        # Single-document object
        return [payload]
    return []


def parse_lhdn_json(path: str | Path) -> pd.DataFrame:
    """Parse an LHDN MyInvois JSON export into a canonical DataFrame.

    Raises:
        LHDNParseError: on missing file, invalid JSON, or validation failures.
    """
    json_path = Path(path)
    if not json_path.is_file():
        raise LHDNParseError(f"LHDN JSON not found: {json_path}")

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise LHDNParseError(f"LHDN file '{json_path}' is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise LHDNParseError(f"Failed to read LHDN file '{json_path}': {exc}") from exc

    records = _extract_documents(payload)
    if not records:
        raise LHDNParseError(f"LHDN file '{json_path}' contains no document records.")

    rows: list[dict] = []
    for idx, rec in enumerate(records):
        uuid = _pick(rec, _UUID_KEYS)
        tin = _pick(rec, _TIN_KEYS)
        date = _pick(rec, _DATE_KEYS)
        ref = _pick(rec, _REF_KEYS)
        sst = _pick(rec, _SST_KEYS)
        total = _pick(rec, _TOTAL_KEYS)
        excluding = _pick(rec, ("totalexcludingtax",))
        rows.append({
            "lhdn_uuid": str(uuid).strip() if uuid is not None else "",
            "tin": str(tin).strip() if tin is not None else "",
            "invoice_date": date,
            "invoice_reference": str(ref).strip().upper() if ref is not None else "",
            "sst_amount": sst,
            "total_amount": total,
            "_total_excluding_tax": excluding,
        })

    df = pd.DataFrame(rows, columns=[*CANONICAL_COLUMNS, "_total_excluding_tax"])

    missing_uuid = int((df["lhdn_uuid"] == "").sum())
    if missing_uuid:
        logger.warning("{} LHDN record(s) have no UUID in {}", missing_uuid, json_path)

    try:
        df["invoice_date"] = pd.to_datetime(df["invoice_date"], errors="coerce")
        df["tin"] = df["tin"].astype(str).str.strip()
        df["sst_amount"] = pd.to_numeric(df["sst_amount"], errors="coerce")
        df["total_amount"] = pd.to_numeric(df["total_amount"], errors="coerce")
        excluding = pd.to_numeric(df["_total_excluding_tax"], errors="coerce")
    except Exception as exc:
        raise LHDNParseError(f"Failed to normalise LHDN file '{json_path}': {exc}") from exc

    # Authentic SDK payloads carry totalExcludingTax instead of a single SST
    # field: derive SST as totalPayable - totalExcluding where SST is absent.
    derived = df["sst_amount"].isna() & df["total_amount"].notna() & excluding.notna()
    df.loc[derived, "sst_amount"] = (df.loc[derived, "total_amount"] - excluding[derived]).round(2)
    df = df.drop(columns=["_total_excluding_tax"])

    bad = int(df["tin"].eq("").sum() + df["invoice_date"].isna().sum()
              + df[["sst_amount", "total_amount"]].isna().sum().sum())
    if bad:
        bad_tin = int(df["tin"].eq("").sum())
        bad_dates = int(df["invoice_date"].isna().sum())
        bad_nums = int(df[["sst_amount", "total_amount"]].isna().sum().sum())
        raise LHDNParseError(
            f"LHDN file '{json_path}' has invalid values: "
            f"{bad_tin} bad TIN(s), {bad_dates} bad date(s), {bad_nums} bad numeric value(s)."
        )

    df = df.reset_index(drop=True)
    logger.info("Parsed LHDN JSON: {} documents from {}", len(df), json_path)
    return df
