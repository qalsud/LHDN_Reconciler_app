"""Tests for the UBL extractor against the official LHDN sample payload."""

import json

import pytest

from src.parsers.ubl import UBLParseError, extract_ubl_invoice, ubl_record_to_gl_row

SAMPLE = "samples/myinvois_ubl_invoice_sample.json"


def _sample_doc():
    return json.load(open(SAMPLE, encoding="utf-8"))


def test_extract_official_sample():
    record = extract_ubl_invoice(_sample_doc())
    assert record["invoice_ref"] == "JSON-INV12345"
    assert record["txn_date"] == "2024-07-23"
    assert record["tax_amount"] == pytest.approx(87.63)
    assert record["total_amount"] == pytest.approx(1436.5)
    assert record["subtotal"] == pytest.approx(1436.5)


def test_extract_bare_invoice_node():
    doc = _sample_doc()
    record = extract_ubl_invoice({"Invoice": doc["Invoice"]})
    assert record["invoice_ref"] == "JSON-INV12345"


def test_ubl_record_to_gl_row_math():
    row = ubl_record_to_gl_row(extract_ubl_invoice(_sample_doc()))
    assert row["invoice_reference"] == "JSON-INV12345"
    assert row["sst_amount"] == pytest.approx(87.63)
    assert row["total_amount"] == pytest.approx(1436.5)


def test_extract_missing_fields_raise():
    with pytest.raises(UBLParseError, match="missing ID"):
        extract_ubl_invoice({})
    with pytest.raises(UBLParseError, match="PayableAmount"):
        extract_ubl_invoice({"Invoice": [{"ID": [{"_": "X"}]}]})
    with pytest.raises(UBLParseError, match="must be a JSON object"):
        extract_ubl_invoice([])


def test_extract_multi_tax_sums():
    doc = {"Invoice": [{
        "ID": [{"_": "INV-M"}],
        "IssueDate": [{"_": "2026-01-01"}],
        "TaxTotal": [{"TaxAmount": [{"_": 6.0}]}, {"TaxAmount": [{"_": 2.5}]}],
        "LegalMonetaryTotal": [{"PayableAmount": [{"_": 108.5}]}],
    }]}
    assert extract_ubl_invoice(doc)["tax_amount"] == pytest.approx(8.5)
