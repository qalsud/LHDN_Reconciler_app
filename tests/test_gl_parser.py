"""Tests for the GL CSV parser."""

import pandas as pd
import pytest

from src.parsers.gl_parser import GLParseError, parse_gl_csv


def test_parse_gl_sample_ok():
    df = parse_gl_csv("samples/gl_sample.csv")
    assert list(df.columns) == [
        "transaction_date", "tin", "invoice_reference",
        "subtotal", "sst_amount", "total_amount",
    ]
    assert len(df) == 12
    assert pd.api.types.is_datetime64_any_dtype(df["transaction_date"])


def test_parse_gl_missing_file():
    with pytest.raises(GLParseError, match="not found"):
        parse_gl_csv("samples/does_not_exist.csv")


def test_parse_gl_missing_column(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Transaction Date,TIN,Invoice Reference\n2026-08-01,C123,INV-1\n", encoding="utf-8")
    with pytest.raises(GLParseError, match="missing required columns"):
        parse_gl_csv(bad)


def test_parse_gl_invalid_values(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "Transaction Date,TIN,Invoice Reference,Subtotal,SST Amount,Total Amount\n"
        "not-a-date,C123,INV-1,abc,def,ghi\n",
        encoding="utf-8",
    )
    with pytest.raises(GLParseError, match="invalid values"):
        parse_gl_csv(bad)


def test_parse_gl_empty(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(GLParseError):
        parse_gl_csv(empty)


def test_parse_gl_header_aliases(tmp_path):
    aliased = tmp_path / "alias.csv"
    aliased.write_text(
        "Date,Tax ID,Invoice No,Net Amount,Tax Amount,Grand Total\n"
        "2026-08-01,C123,inv-9,100.00,6.00,106.00\n",
        encoding="utf-8",
    )
    df = parse_gl_csv(aliased)
    assert df.loc[0, "invoice_reference"] == "INV-9"
    assert df.loc[0, "total_amount"] == pytest.approx(106.00)
