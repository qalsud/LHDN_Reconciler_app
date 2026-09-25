"""Build realistic sample inputs from real public transaction data.

Source: UCI "Online Retail II" (D. Chen, 2012) — 1M+ real UK gift-ware
transactions, CC-BY-4.0, https://doi.org/10.24432/C5CG6D
Invoice numbers, dates and line amounts are REAL; see REALDATA_NOTES.md
for the (documented) assumptions bridging retail data to SST e-invoicing.

LHDN JSON mirrors the authentic MyInvois DocumentDetails shape
(sdk.myinvois.hasil.gov.my): uuid, submissionUid, internalId, issuerTin,
issuerName, dateTimeIssued, totalExcludingTax, totalPayableAmount, status.

Usage:
    python samples/build_realdata.py --source <retail.xlsx> [--count 60] [--seed 7]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import uuid
from datetime import timedelta
from pathlib import Path

import pandas as pd
from loguru import logger

SUPPLIER_TIN = "C98765432019"  # imputed demo TIN (documented in REALDATA_NOTES.md)
SUPPLIER_NAME = "Online Retail Ltd (demo)"
SST_RATE = 0.06


def load_invoice_totals(source: Path) -> pd.DataFrame:
    """Aggregate retail line items to one row per real invoice."""
    logger.info("Reading {} ...", source)
    lines = pd.read_excel(source, engine="openpyxl")
    lines = lines.dropna(subset=["Invoice", "Quantity", "Price", "InvoiceDate"])
    lines["InvoiceNo"] = lines["Invoice"].astype(str)
    lines = lines[~lines["InvoiceNo"].str.startswith("C")]  # cancellations
    lines = lines[(lines["Quantity"] > 0) & (lines["Price"] > 0)]
    lines["line_total"] = lines["Quantity"] * lines["Price"]
    invoices = (
        lines.groupby("InvoiceNo")
        .agg(date=("InvoiceDate", "min"), total=("line_total", "sum"))
        .reset_index()
    )
    invoices["total"] = invoices["total"].round(2)
    invoices = invoices[invoices["total"] > 0].sort_values("date").reset_index(drop=True)
    logger.info("Aggregated {} clean invoices", len(invoices))
    return invoices


def split_sst(total: float, rate: float = SST_RATE) -> tuple[float, float]:
    subtotal = round(total / (1.0 + rate), 2)
    return subtotal, round(total - subtotal, 2)


def build_pair(invoices: pd.DataFrame, seed: int = 7) -> tuple[list[dict], list[dict]]:
    """Return (gl_rows, lhdn_docs) with seeded realistic discrepancies."""
    rng = random.Random(seed)
    gl_rows: list[dict] = []
    lhdn_docs: list[dict] = []
    batch_uid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"myinvois-batch-{seed}"))

    for i, inv in invoices.iterrows():
        ref = str(inv["InvoiceNo"])
        day = pd.Timestamp(inv["date"]).date().isoformat()
        total = round(float(inv["total"]), 2)
        subtotal, sst = split_sst(total)
        gl_rows.append({"ref": ref, "tin": SUPPLIER_TIN, "date": day,
                        "subtotal": subtotal, "sst": sst, "total": total})

        if i % 7 == 6:
            continue  # never submitted -> Unsubmitted_Sales
        doc = {
            "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"myinvois:{ref}")),
            "submissionUid": batch_uid,
            "internalId": ref,
            "issuerTin": SUPPLIER_TIN,
            "issuerName": SUPPLIER_NAME,
            "dateTimeIssued": f"{day}T10:00:00Z",
            "totalExcludingTax": subtotal,
            "totalPayableAmount": total,
            "status": "Valid",
        }
        if i % 11 == 4:
            # Wrong SST rate applied upstream (8% instead of 6%).
            bad_sub = subtotal
            bad_total = round(bad_sub * 1.08, 2)
            doc["totalExcludingTax"] = bad_sub
            doc["totalPayableAmount"] = bad_total
        elif i % 13 == 8:
            # Resubmitted under a suffixed reference, one day later.
            doc["internalId"] = f"{ref}-R"
            doc["dateTimeIssued"] = (
                pd.Timestamp(day) + timedelta(days=1)).date().isoformat() + "T10:00:00Z"
        lhdn_docs.append(doc)

    # LHDN-only document (submitted, absent from the ledger).
    lhdn_docs.append({
        "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "myinvois:lhdn-only-1")),
        "submissionUid": batch_uid,
        "internalId": "581499-R",
        "issuerTin": SUPPLIER_TIN,
        "issuerName": SUPPLIER_NAME,
        "dateTimeIssued": f"{pd.Timestamp(invoices.iloc[0]['date']).date().isoformat()}T10:00:00Z",
        "totalExcludingTax": 100.00,
        "totalPayableAmount": 106.00,
        "status": "Valid",
    })
    # Shuffle submission order (deterministic) so input order means nothing.
    lhdn_docs = sorted(lhdn_docs, key=lambda d: rng.random())
    return gl_rows, lhdn_docs


def write_pair(gl_rows: list[dict], lhdn_docs: list[dict],
               gl_path: Path, lhdn_path: Path) -> None:
    gl_path.parent.mkdir(parents=True, exist_ok=True)
    with gl_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Transaction Date", "TIN", "Invoice Reference",
                         "Subtotal", "SST Amount", "Total Amount"])
        for r in gl_rows:
            writer.writerow([r["date"], r["tin"], r["ref"],
                             f'{r["subtotal"]:.2f}', f'{r["sst"]:.2f}', f'{r["total"]:.2f}'])
    lhdn_path.write_text(json.dumps({"documents": lhdn_docs}, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build realistic samples from UCI retail data.")
    parser.add_argument("--source", default="C:/Users/HaiqalSuderman/AppData/Local/Temp/opencode/retail2.zip")
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--offset", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--gl-out", default="samples/realistic_gl.csv")
    parser.add_argument("--lhdn-out", default="samples/realistic_lhdn.json")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if source.suffix == ".zip":
        import zipfile
        with zipfile.ZipFile(source) as zf:
            inner = [n for n in zf.namelist() if n.endswith(".xlsx")][0]
            extract_to = source.parent / inner
            if not extract_to.is_file():
                with zf.open(inner) as src, extract_to.open("wb") as dst:
                    dst.write(src.read())
            source = extract_to

    invoices = load_invoice_totals(source)
    window = invoices.iloc[args.offset:args.offset + args.count].reset_index(drop=True)
    gl_rows, lhdn_docs = build_pair(window, seed=args.seed)
    write_pair(gl_rows, lhdn_docs, Path(args.gl_out), Path(args.lhdn_out))
    print(f"Wrote {len(gl_rows)} realistic GL rows -> {args.gl_out}")
    print(f"Wrote {len(lhdn_docs)} realistic LHDN docs -> {args.lhdn_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
