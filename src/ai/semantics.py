"""Semantic CSV header mapper (Phase 2).

Maps unknown vendor column names (e.g. ``Tarikh_Invois``, ``Bill_Date``) to
the canonical schema using vector embeddings + cosine similarity.

Canonical columns:
    ['invoice_ref', 'txn_date', 'tin_number',
     'subtotal', 'tax_amount', 'total_amount']

Primary embeddings come from ``sentence-transformers`` (``all-MiniLM-L6-v2``),
loaded lazily so the module imports without the model. If the model (or the
library) is unavailable, a deterministic character-trigram fallback embedding
is used instead and a warning is logged — cosine similarity + the 0.70
threshold still apply either way.

Any header whose best similarity is below the threshold raises
:class:`HeaderMappingError` so a human maps it manually.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache

import numpy as np
import pandas as pd
from loguru import logger

CANONICAL_COLUMNS = ["invoice_ref", "txn_date", "tin_number",
                     "subtotal", "tax_amount", "total_amount"]

#: Synonym phrases embedded on the canonical side. A header's score for a
#: column is the max cosine over that column's synonyms; the 0.70 threshold
#: is unchanged. Bare snake_case tokens alone sit too close in embedding
#: space (typical best 0.4-0.6), so synonyms carry the semantic signal.
CANONICAL_SYNONYMS = {
    "invoice_ref": ["invoice ref", "invoice number", "invoice no",
                    "bill number", "bill no", "document number",
                    "no invois", "nombor invois"],
    "txn_date": ["txn date", "transaction date", "invoice date",
                 "billing date", "bill date",
                 "tarikh invois", "tarikh"],
    "tin_number": ["tin number", "tax identification number",
                   "supplier tin", "tax id", "no tin"],
    "subtotal": ["subtotal", "net amount", "amount before tax",
                 "jumlah kecil"],
    "tax_amount": ["tax amount", "sst amount", "sales tax", "tax",
                   "cukai sst", "cukai", "amaun cukai"],
    "total_amount": ["total amount", "grand total", "total payable",
                     "jumlah besar", "jumlah keseluruhan"],
}

#: Canonical-AI name -> deterministic engine column (see src/engine/matcher.py).
ENGINE_COLUMN_MAP = {
    "invoice_ref": "invoice_reference",
    "txn_date": "transaction_date",
    "tin_number": "tin",
    "subtotal": "subtotal",
    "tax_amount": "sst_amount",
    "total_amount": "total_amount",
}

SIMILARITY_THRESHOLD = 0.70
DEFAULT_EMBED_MODEL = "all-MiniLM-L6-v2"

_TRIGRAM_DIM = 512


class HeaderMappingError(ValueError):
    """Raised when one or more headers cannot be mapped above the threshold."""


class EmbeddingUnavailableError(RuntimeError):
    """Raised when the transformer model cannot be loaded (fallback is used)."""


def _normalise(text: str) -> str:
    # Split camelCase without shredding ALLCAPS acronyms (TIN stays "TIN").
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(text))
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    text = re.sub(r"[_\-\.\/]+", " ", text)
    return " ".join(text.lower().split())


def _trigram_vector(text: str) -> np.ndarray:
    """Deterministic char-trigram embedding used as an offline fallback."""
    vec = np.zeros(_TRIGRAM_DIM, dtype=np.float64)
    padded = f"  {_normalise(text)}  "
    for i in range(len(padded) - 2):
        trigram = padded[i:i + 3]
        idx = int(hashlib.md5(trigram.encode()).hexdigest(), 16) % _TRIGRAM_DIM
        vec[idx] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def fallback_embed(texts: list[str]) -> np.ndarray:
    """Offline embedding (no model download). Returns L2-normalised rows."""
    return np.vstack([_trigram_vector(t) for t in texts])


@lru_cache(maxsize=2)
def _load_model(name: str = DEFAULT_EMBED_MODEL):
    from sentence_transformers import SentenceTransformer  # lazy: heavy dep

    return SentenceTransformer(name)


def transformer_embed(texts: list[str], model_name: str = DEFAULT_EMBED_MODEL) -> np.ndarray:
    """Embed with sentence-transformers; raises EmbeddingUnavailableError on failure."""
    try:
        model = _load_model(model_name)
        vecs = model.encode([_normalise(t) for t in texts], normalize_embeddings=True)
    except Exception as exc:
        raise EmbeddingUnavailableError(
            f"Could not load embedding model '{model_name}': {exc}") from exc
    return np.asarray(vecs, dtype=np.float64)


def default_embed(texts: list[str], model_name: str = DEFAULT_EMBED_MODEL) -> np.ndarray:
    """Transformer embeddings with graceful fallback to trigram vectors."""
    try:
        return transformer_embed(texts, model_name)
    except EmbeddingUnavailableError as exc:
        logger.warning("{} — falling back to offline trigram embeddings.", exc)
        return fallback_embed(texts)


def cosine_similarity(matrix_a: np.ndarray, matrix_b: np.ndarray) -> np.ndarray:
    """Cosine similarity between L2-normalised row vectors."""
    return np.clip(matrix_a @ matrix_b.T, -1.0, 1.0)


def map_headers(
    headers: list[str],
    canonical: list[str] | None = None,
    threshold: float = SIMILARITY_THRESHOLD,
    embed_fn=None,
) -> dict[str, str]:
    """Map unknown CSV headers to canonical columns via cosine similarity.

    Args:
        headers: raw header strings from the vendor file.
        canonical: target column names (defaults to CANONICAL_COLUMNS).
        threshold: minimum similarity; headers below it raise HeaderMappingError.
        embed_fn: ``(texts) -> np.ndarray``; defaults to transformer w/ fallback.

    Returns:
        Mapping of ``{raw_header: canonical_column}``.

    Raises:
        HeaderMappingError: if any header scores below ``threshold``.
    """
    canonical = list(canonical or CANONICAL_COLUMNS)
    if not headers:
        raise HeaderMappingError("No headers provided for mapping.")
    embed = embed_fn or default_embed
    header_vecs = np.asarray(embed([str(h) for h in headers]), dtype=np.float64)
    syn_texts: list[str] = []
    syn_cols: list[str] = []
    for col in canonical:
        for syn in CANONICAL_SYNONYMS.get(col, [col]):
            syn_texts.append(syn)
            syn_cols.append(col)
    syn_vecs = np.asarray(embed(syn_texts), dtype=np.float64)
    sims = cosine_similarity(header_vecs, syn_vecs)

    mapping: dict[str, str] = {}
    unmapped: list[str] = []
    for i, raw in enumerate(headers):
        best = int(np.argmax(sims[i]))
        column = syn_cols[best]
        score = float(sims[i, best])
        if score < threshold:
            unmapped.append(f"{raw!r} (best={column} @ {score:.2f})")
        else:
            mapping[str(raw)] = column
            logger.debug("Mapped header {!r} -> {} ({:.2f})", raw, column, score)
    if unmapped:
        raise HeaderMappingError(
            f"{len(unmapped)} header(s) below similarity threshold {threshold:.2f}; "
            f"map manually: {', '.join(unmapped)}"
        )
    return mapping


def apply_mapping(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """Rename a vendor frame to canonical-AI columns; validates completeness."""
    targets = list(mapping.values())
    duplicates = sorted({t for t in targets if targets.count(t) > 1})
    if duplicates:
        raise HeaderMappingError(
            f"Ambiguous mapping: multiple vendor headers collapsed onto "
            f"{duplicates}; resolve manually."
        )
    out = df.rename(columns=mapping)
    missing = [c for c in CANONICAL_COLUMNS if c not in out.columns]
    if missing:
        raise HeaderMappingError(
            f"Mapped frame is missing canonical columns: {missing}. "
            f"Duplicate vendor headers may have collapsed onto one target."
        )
    return out


def canonical_to_engine(df: pd.DataFrame) -> pd.DataFrame:
    """Translate canonical-AI columns to the deterministic engine schema."""
    out = df.rename(columns=ENGINE_COLUMN_MAP).copy()
    out["transaction_date"] = pd.to_datetime(out["transaction_date"], errors="coerce")
    out["tin"] = out["tin"].astype(str).str.strip()
    out["invoice_reference"] = out["invoice_reference"].astype(str).str.strip().str.upper()
    for col in ("subtotal", "sst_amount", "total_amount"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out[[
        "transaction_date", "tin", "invoice_reference",
        "subtotal", "sst_amount", "total_amount",
    ]]
