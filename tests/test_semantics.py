"""Tests for the semantic header mapper (src/ai/semantics.py)."""

import numpy as np
import pytest

from src.ai.semantics import (
    CANONICAL_COLUMNS,
    CANONICAL_SYNONYMS,
    HeaderMappingError,
    apply_mapping,
    canonical_to_engine,
    cosine_similarity,
    fallback_embed,
    map_headers,
)
from src.parsers.gl_parser import parse_gl_csv_auto


def _fake_embed_factory(vectors: dict[str, list[float]]):
    """Build an embed_fn returning canned vectors keyed by raw text."""
    def _embed(texts: list[str]) -> np.ndarray:
        return np.array([vectors[t] for t in texts], dtype=np.float64)
    return _embed


def _one_hot(dim: int, idx: int) -> list[float]:
    vec = [0.0] * dim
    vec[idx] = 1.0
    return vec


def _canon_vectors(dim: int) -> dict[str, list[float]]:
    """Canned canonical-side vectors keyed by every synonym string."""
    vectors: dict[str, list[float]] = {}
    for i, col in enumerate(CANONICAL_COLUMNS):
        for syn in CANONICAL_SYNONYMS[col]:
            vectors[syn] = _one_hot(dim, i)
    return vectors


def test_map_headers_exact_aliases_with_fake_embeddings():
    dim = len(CANONICAL_COLUMNS)
    fake = _fake_embed_factory({
        "Tarikh_Invois": _one_hot(dim, 1),   # -> txn_date
        "Bill_No": _one_hot(dim, 0),          # -> invoice_ref
        "Cukai_TIN": _one_hot(dim, 2),        # -> tin_number
        "Subtotal": _one_hot(dim, 3),
        "Cukai": _one_hot(dim, 4),            # -> tax_amount
        "Jumlah": _one_hot(dim, 5),           # -> total_amount
        **_canon_vectors(dim),
    })
    mapping = map_headers(
        ["Tarikh_Invois", "Bill_No", "Cukai_TIN", "Subtotal", "Cukai", "Jumlah"],
        embed_fn=fake,
    )
    assert mapping == {
        "Tarikh_Invois": "txn_date",
        "Bill_No": "invoice_ref",
        "Cukai_TIN": "tin_number",
        "Subtotal": "subtotal",
        "Cukai": "tax_amount",
        "Jumlah": "total_amount",
    }


def test_map_headers_below_threshold_raises():
    dim = len(CANONICAL_COLUMNS)
    weak = [0.5 / dim] * dim  # cosine ~0.41 against any one-hot vector
    fake = _fake_embed_factory({"Mystery_Column_XYZ": weak, **_canon_vectors(dim)})
    with pytest.raises(HeaderMappingError, match="below similarity threshold"):
        map_headers(["Mystery_Column_XYZ"], threshold=0.70, embed_fn=fake)


def test_map_headers_empty_raises():
    with pytest.raises(HeaderMappingError, match="No headers"):
        map_headers([])


def test_fallback_embed_cosine_discovers_close_headers():
    # Offline trigram path: near-identical spellings must clear 0.70.
    mapping = map_headers(
        ["Invoice_Ref", "Txn_Date", "TIN_Number", "SubTotal", "Tax_Amount", "Total_Amount"],
        embed_fn=fallback_embed,
    )
    assert mapping == {
        "Invoice_Ref": "invoice_ref",
        "Txn_Date": "txn_date",
        "TIN_Number": "tin_number",
        "SubTotal": "subtotal",
        "Tax_Amount": "tax_amount",
        "Total_Amount": "total_amount",
    }


def test_fallback_embed_rejects_garbage():
    with pytest.raises(HeaderMappingError):
        map_headers(["zzz_qqq", "xxx_www", "jjj_kkk"], embed_fn=fallback_embed)


def test_apply_mapping_and_engine_translation():
    import pandas as pd
    raw = pd.DataFrame([{
        "Tarikh_Invois": "2026-08-01", "Bill_No": "inv-1", "Cukai_TIN": "C1",
        "Subtotal": 100.0, "Cukai": 6.0, "Jumlah": 106.0,
    }])
    mapping = {
        "Tarikh_Invois": "txn_date", "Bill_No": "invoice_ref", "Cukai_TIN": "tin_number",
        "Subtotal": "subtotal", "Cukai": "tax_amount", "Jumlah": "total_amount",
    }
    engine = canonical_to_engine(apply_mapping(raw, mapping))
    assert list(engine.columns) == ["transaction_date", "tin", "invoice_reference",
                                    "subtotal", "sst_amount", "total_amount"]
    assert engine.loc[0, "invoice_reference"] == "INV-1"


def test_apply_mapping_rejects_duplicates():
    import pandas as pd
    raw = pd.DataFrame([{"A": 1, "B": 2}])
    with pytest.raises(HeaderMappingError, match="Ambiguous mapping"):
        apply_mapping(raw, {"A": "subtotal", "B": "subtotal"})


def test_parse_gl_csv_auto_end_to_end(tmp_path):
    csv_path = tmp_path / "vendor.csv"
    csv_path.write_text(
        "Tarikh_Invois,Bill_No,Cukai_TIN,Subtotal,Cukai,Jumlah\n"
        "2026-08-01,INV-1,C1,100.00,6.00,106.00\n",
        encoding="utf-8",
    )
    dim = len(CANONICAL_COLUMNS)
    fake = _fake_embed_factory({
        "Tarikh_Invois": _one_hot(dim, 1), "Bill_No": _one_hot(dim, 0),
        "Cukai_TIN": _one_hot(dim, 2), "Subtotal": _one_hot(dim, 3),
        "Cukai": _one_hot(dim, 4), "Jumlah": _one_hot(dim, 5),
        **_canon_vectors(dim),
    })
    df = parse_gl_csv_auto(csv_path, embed_fn=fake)
    assert len(df) == 1
    assert df.loc[0, "total_amount"] == pytest.approx(106.00)


def test_cosine_similarity_known_values():
    a = np.array([[1.0, 0.0]])
    b = np.array([[1.0, 0.0], [0.0, 1.0]])
    sims = cosine_similarity(a, b)
    assert sims[0, 0] == pytest.approx(1.0)
    assert sims[0, 1] == pytest.approx(0.0)


def test_map_headers_real_model_english_and_malay():
    """Integration against all-MiniLM-L6-v2; skips when the model is offline."""
    from src.ai.semantics import EmbeddingUnavailableError, transformer_embed

    try:
        transformer_embed(["connectivity probe"])
    except EmbeddingUnavailableError:
        pytest.skip("embedding model unavailable offline")
    english = map_headers(
        ["Bill_Date", "Invoice No", "TIN Number",
         "Net Amount", "Tax Amount", "Grand Total"])
    assert english == {
        "Bill_Date": "txn_date", "Invoice No": "invoice_ref",
        "TIN Number": "tin_number", "Net Amount": "subtotal",
        "Tax Amount": "tax_amount", "Grand Total": "total_amount",
    }
    malay = map_headers(
        ["Tarikh_Invois", "No_Invois", "No_TIN",
         "Jumlah_Kecil", "Cukai_SST", "Jumlah_Besar"])
    assert malay == {
        "Tarikh_Invois": "txn_date", "No_Invois": "invoice_ref",
        "No_TIN": "tin_number", "Jumlah_Kecil": "subtotal",
        "Cukai_SST": "tax_amount", "Jumlah_Besar": "total_amount",
    }
