"""Tests for the LHDN JSON parser."""

import json

import pytest

from src.parsers.lhdn_parser import LHDNParseError, parse_lhdn_json


def test_parse_lhdn_sample_ok():
    df = parse_lhdn_json("samples/lhdn_sample.json")
    assert list(df.columns) == [
        "lhdn_uuid", "tin", "invoice_date",
        "invoice_reference", "sst_amount", "total_amount",
    ]
    assert len(df) == 10
    assert (df["lhdn_uuid"] != "").all()


def test_parse_lhdn_missing_file():
    with pytest.raises(LHDNParseError, match="not found"):
        parse_lhdn_json("samples/does_not_exist.json")


def test_parse_lhdn_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(LHDNParseError, match="not valid JSON"):
        parse_lhdn_json(bad)


def test_parse_lhdn_empty_documents(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"documents": []}), encoding="utf-8")
    with pytest.raises(LHDNParseError, match="no document records"):
        parse_lhdn_json(empty)


def test_parse_lhdn_realistic_sdk_shape(tmp_path):
    """Authentic MyInvois DocumentDetails field names (sdk.myinvois.hasil.gov.my)."""
    doc = {
        "uuid": "F9D425P6DS7D8IU",
        "submissionUid": "HJSD135P2S7D8IU",
        "internalId": "PZ-234-A",
        "issuerTin": "C2584563200",
        "issuerName": "AMS Setia Jaya Sdn. Bhd.",
        "dateTimeIssued": "2015-02-13T13:15:10Z",
        "totalExcludingTax": 100.70,
        "totalPayableAmount": 124.09,
        "status": "Valid",
    }
    path = tmp_path / "sdk.json"
    path.write_text(json.dumps({"documents": [doc]}), encoding="utf-8")
    df = parse_lhdn_json(path)
    assert df.loc[0, "tin"] == "C2584563200"
    assert df.loc[0, "invoice_reference"] == "PZ-234-A"
    assert df.loc[0, "total_amount"] == pytest.approx(124.09)
    # SST derived as totalPayable - totalExcluding per the SDK shape.
    assert df.loc[0, "sst_amount"] == pytest.approx(124.09 - 100.70)


def test_parse_lhdn_plain_array_and_wrappers(tmp_path):
    doc = {
        "uuid": "u-1", "supplierTIN": "C1", "invoiceDate": "2026-08-01",
        "invoiceNo": "INV-1", "sstAmount": 6.0, "totalAmount": 106.0,
    }
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps([doc]), encoding="utf-8")
    assert len(parse_lhdn_json(plain)) == 1

    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"invoices": [doc]}), encoding="utf-8")
    assert len(parse_lhdn_json(wrapped)) == 1


def test_parse_lhdn_bad_values_raise(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{
        "uuid": "u-1", "supplierTIN": "", "invoiceDate": "not-a-date",
        "invoiceNo": "INV-1", "sstAmount": "x", "totalAmount": "y",
    }]), encoding="utf-8")
    with pytest.raises(LHDNParseError, match="invalid values"):
        parse_lhdn_json(bad)
