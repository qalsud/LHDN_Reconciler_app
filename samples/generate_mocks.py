"""Deterministic synthetic data generator for the reconciliation engine.

Creates a mock GL CSV and a mock LHDN JSON API response with engineered
edge cases covering every audit bucket:

  - clean exact matches
  - rounding-tolerance match (total off by RM 0.03)
  - SST mismatches (wrong tax rate on the LHDN side)
  - fuzzy reference variant (LHDN ref differs, same financials, 1-day gap)
  - unsubmitted GL rows (no LHDN counterpart)
  - LHDN-only document (no GL counterpart)

Usage:
    python samples/generate_mocks.py
    python samples/generate_mocks.py --seed 7 --gl-out samples/mock_gl.csv --lhdn-out samples/mock_lhdn.json
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

TINS = ["C12345678010", "C98765432010", "C55566677010"]

GL_HEADER = ["Transaction Date", "TIN", "Invoice Reference",
             "Subtotal", "SST Amount", "Total Amount"]


def _row(ref: str, tin: str, day: int, subtotal: float, sst: float) -> dict:
    total = round(subtotal + sst, 2)
    return {
        "ref": ref, "tin": tin,
        "date": (date(2026, 8, 1) + timedelta(days=day - 1)).isoformat(),
        "subtotal": round(subtotal, 2), "sst": round(sst, 2), "total": total,
    }


def build_dataset(seed: int = 42) -> tuple[list[dict], list[dict]]:
    """Return (gl_rows, lhdn_docs). Deterministic for a given seed."""
    rng = random.Random(seed)
    gl: list[dict] = []
    lhdn: list[dict] = []

    def add_pair(ref: str, tin: str, day: int, subtotal: float, sst: float,
                 lhdn_overrides: dict | None = None, lhdn_ref: str | None = None,
                 lhdn_day: int | None = None, skip_lhdn: bool = False) -> None:
        g = _row(ref, tin, day, subtotal, sst)
        gl.append(g)
        if skip_lhdn:
            return
        doc = {
            "uuid": f"{rng.getrandbits(32):08x}-{ref[-4:]}-4e5f-8000-{rng.getrandbits(48):012x}",
            "supplierTIN": tin,
            "invoiceDate": (date(2026, 8, 1) + timedelta(days=(lhdn_day or day) - 1)).isoformat(),
            "invoiceNo": lhdn_ref or ref,
            "sstAmount": g["sst"],
            "totalAmount": g["total"],
        }
        if lhdn_overrides:
            doc.update(lhdn_overrides)
        lhdn.append(doc)

    # Clean exact matches (one per TIN).
    add_pair("INV-2026-0001", TINS[0], 1, 10000.00, 600.00)
    add_pair("INV-2026-0002", TINS[1], 2, 25000.00, 1500.00)
    add_pair("INV-2026-0003", TINS[2], 3, 8000.00, 480.00)
    # Rounding tolerance: LHDN total RM 0.03 higher.
    add_pair("INV-2026-0004", TINS[0], 4, 12000.00, 720.00,
             lhdn_overrides={"totalAmount": 12720.03})
    # SST mismatch: LHDN side used the wrong tax amount.
    add_pair("INV-2026-0005", TINS[1], 5, 20000.00, 1200.00,
             lhdn_overrides={"sstAmount": 1000.00, "totalAmount": 21000.00})
    # Fuzzy reference variant: same financials, suffixed ref, +1 day.
    add_pair("INV-2026-0006", TINS[0], 6, 15000.00, 900.00,
             lhdn_ref="INV-2026-0006-MYINVOIS", lhdn_day=7)
    # More clean matches.
    add_pair("INV-2026-0007", TINS[2], 8, 5000.00, 300.00)
    add_pair("INV-2026-0008", TINS[1], 9, 9500.00, 570.00)
    # Unsubmitted GL rows.
    add_pair("INV-2026-0009", TINS[0], 10, 7500.00, 450.00, skip_lhdn=True)
    add_pair("INV-2026-0010", TINS[2], 11, 11000.00, 660.00, skip_lhdn=True)
    # LHDN-only document.
    lhdn.append({
        "uuid": f"{rng.getrandbits(32):08x}-0999-4e5f-8000-{rng.getrandbits(48):012x}",
        "supplierTIN": "C00011122010",
        "invoiceDate": "2026-08-12",
        "invoiceNo": "INV-2026-0999",
        "sstAmount": 240.00,
        "totalAmount": 4240.00,
    })
    return gl, lhdn


def write_samples(gl_rows: list[dict], lhdn_docs: list[dict],
                  gl_path: Path, lhdn_path: Path) -> tuple[Path, Path]:
    gl_path.parent.mkdir(parents=True, exist_ok=True)
    lhdn_path.parent.mkdir(parents=True, exist_ok=True)
    with gl_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(GL_HEADER)
        for r in gl_rows:
            writer.writerow([r["date"], r["tin"], r["ref"],
                             f'{r["subtotal"]:.2f}', f'{r["sst"]:.2f}', f'{r["total"]:.2f}'])
    lhdn_path.write_text(json.dumps({"documents": lhdn_docs}, indent=2), encoding="utf-8")
    return gl_path, lhdn_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate mock GL + LHDN samples.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--gl-out", default="samples/mock_gl.csv")
    parser.add_argument("--lhdn-out", default="samples/mock_lhdn.json")
    args = parser.parse_args(argv)
    gl_rows, lhdn_docs = build_dataset(seed=args.seed)
    gl_path, lhdn_path = write_samples(gl_rows, lhdn_docs,
                                       Path(args.gl_out), Path(args.lhdn_out))
    print(f"Wrote {len(gl_rows)} GL rows -> {gl_path}")
    print(f"Wrote {len(lhdn_docs)} LHDN docs -> {lhdn_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
