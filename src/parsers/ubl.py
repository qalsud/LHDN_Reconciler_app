"""UBL invoice extractor (MyInvois document source format).

The Get Document API returns the submitted source in UBL 2.1 JSON, where
every value is wrapped as ``{"_": value}`` (per the SDK FAQ). This module
extracts the five reconciliation fields from that shape:

  ref    Invoice[0].ID[0]._
  date   Invoice[0].IssueDate[0]._
  tin    AccountingSupplierParty.Party.PartyIdentification[] entry
         whose ID schemeID == "TIN"
  tax    sum of TaxTotal[].TaxAmount[]._  (multi-tax safe)
  total  LegalMonetaryTotal[0].PayableAmount[0]._
"""

from __future__ import annotations

import pandas as pd


class UBLParseError(ValueError):
    """Raised when a UBL document lacks the required invoice fields."""


def _first(node: object) -> object | None:
    if isinstance(node, list) and node:
        return node[0]
    return node if node is not None else None


def _value(node: object) -> object | None:
    """Unwrap ``[{"_": v}]`` / ``{"_": v}`` / scalar chains to the scalar."""
    current = node
    for _ in range(4):
        if isinstance(current, list):
            if not current:
                return None
            current = current[0]
        elif isinstance(current, dict) and set(current) == {"_"}:
            current = current["_"]
        elif isinstance(current, dict) and "_" in current:
            current = current["_"]
        else:
            return current
    return current if not isinstance(current, (list, dict)) else None


def _party_tin(party_node: object, scheme: str = "TIN") -> str | None:
    parties = party_node if isinstance(party_node, list) else [party_node]
    for party in parties:
        if not isinstance(party, dict):
            continue
        identifications = party.get("PartyIdentification") or []
        if not isinstance(identifications, list):
            identifications = [identifications]
        for ident in identifications:
            if not isinstance(ident, dict):
                continue
            ids = ident.get("ID") or []
            if not isinstance(ids, list):
                ids = [ids]
            for entry in ids:
                if isinstance(entry, dict) and entry.get("schemeID") == scheme:
                    value = _value(entry)
                    if value not in (None, ""):
                        return str(value).strip()
    return None


def extract_ubl_invoice(document: dict) -> dict:
    """Extract reconciliation fields from a UBL invoice JSON object.

    Accepts the whole payload (``{"Invoice": [...]}``) or the bare
    invoice node. Raises UBLParseError on missing ref/date/total.
    """
    if not isinstance(document, dict):
        raise UBLParseError("UBL document must be a JSON object.")
    invoice = document.get("Invoice", document)
    node = _first(invoice)
    if not isinstance(node, dict):
        raise UBLParseError("UBL document has no Invoice node.")

    ref = _value(node.get("ID"))
    date = _value(node.get("IssueDate"))
    supplier = _first((node.get("AccountingSupplierParty") or [{}])[0].get("Party")) \
        if node.get("AccountingSupplierParty") else None
    tin = _party_tin(supplier) if supplier is not None else None

    tax = 0.0
    tax_seen = False
    tax_totals = node.get("TaxTotal") or []
    for entry in tax_totals if isinstance(tax_totals, list) else [tax_totals]:
        amount = _value((entry or {}).get("TaxAmount") if isinstance(entry, dict) else None)
        if amount is not None:
            tax_seen = True
            tax += float(amount)

    monetary = _first(node.get("LegalMonetaryTotal")) or {}
    monetary = monetary if isinstance(monetary, dict) else {}
    total = _value(monetary.get("PayableAmount"))
    subtotal = _value(monetary.get("TaxExclusiveAmount"))

    if ref in (None, ""):
        raise UBLParseError("UBL invoice is missing ID (invoice reference).")
    if total is None:
        raise UBLParseError("UBL invoice is missing LegalMonetaryTotal/PayableAmount.")
    try:
        total_f = float(total)
    except (TypeError, ValueError) as exc:
        raise UBLParseError(f"UBL PayableAmount is not numeric: {total!r}") from exc

    return {
        "invoice_ref": str(ref).strip(),
        "txn_date": str(date).strip() if date not in (None, "") else None,
        "tin_number": str(tin).strip() if tin not in (None, "") else None,
        "tax_amount": round(tax, 2) if tax_seen else None,
        "subtotal": float(subtotal) if subtotal is not None else None,
        "total_amount": round(total_f, 2),
    }


def ubl_record_to_gl_row(record: dict) -> dict:
    """Translate an extracted UBL record to the engine GL schema."""
    tax = record.get("tax_amount")
    total = record.get("total_amount")
    subtotal = record.get("subtotal")
    if subtotal is None and tax is not None and total is not None:
        subtotal = round(float(total) - float(tax), 2)
    return {
        "transaction_date": pd.to_datetime(record.get("txn_date"), errors="coerce"),
        "tin": str(record.get("tin_number") or "").strip(),
        "invoice_reference": str(record.get("invoice_ref") or "").strip().upper(),
        "subtotal": float(subtotal) if subtotal is not None else float("nan"),
        "sst_amount": float(tax) if tax is not None else float("nan"),
        "total_amount": float(total) if total is not None else float("nan"),
    }
