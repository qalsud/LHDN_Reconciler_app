"""LLM audit narratives for exceptional transactions (Phase 4).

Generates a 2-sentence executive summary for any bucket row categorised as
``SST_Rate_Mismatch`` or carrying an anomaly score above 85. The prompt passes
the GL row, the LHDN row and the anomaly score, asking for a variance
explanation plus a recommended accounting action (e.g. "Issue credit note").

Execution is defensive (per AGENT.md rule 3):
  - ``tenacity`` retries (3 attempts, exponential backoff) around the LLM call;
  - graceful fallback to a deterministic template narrative when no API key
    is configured or the endpoint keeps failing — the pipeline never crashes
    because the AI layer is down.
"""

from __future__ import annotations

import os

import pandas as pd
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

ANOMALY_NARRATIVE_THRESHOLD = 85.0
DEFAULT_AUDIT_MODEL = "gemini/gemini-3.6-flash"

#: Env vars recognised as "an LLM key is configured" (litellm provider keys).
LLM_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY")

NARRATIVE_SYSTEM_PROMPT = (
    "You are a senior financial auditor reviewing Malaysian SST e-invoice "
    "reconciliation variances. Reply with exactly two sentences: sentence 1 "
    "explains the most likely cause of the variance; sentence 2 recommends a "
    "concrete accounting action (e.g. verify SST computation, issue credit "
    "note, submit the missing e-invoice, link the LHDN UUID). Be specific "
    "with the RM amounts provided. No preamble, no bullet points."
)


def _row_text(label: str, row: pd.Series | dict | None) -> str:
    if row is None:
        return f"{label}: (no counterpart record)"
    items = row.to_dict() if isinstance(row, pd.Series) else dict(row)
    interesting = {k: items.get(k) for k in (
        "transaction_date", "invoice_date_lhdn", "tin",
        "invoice_reference_gl", "invoice_reference_lhdn", "lhdn_uuid",
        "subtotal_gl", "sst_gl", "sst_lhdn", "sst_variance",
        "total_gl", "total_lhdn", "total_variance", "match_stage") if k in items}
    return f"{label}: " + ", ".join(f"{k}={v}" for k, v in interesting.items())


def build_narrative_prompt(gl_row, lhdn_row, anomaly_score: float, bucket: str) -> str:
    """Render the user prompt for the narrative call (pure, testable)."""
    score = float(anomaly_score) if anomaly_score is not None else float("nan")
    return (
        f"Audit bucket: {bucket}\n"
        f"Anomaly score (0-100, higher is stranger): {score:.1f}\n"
        f"{_row_text('GL row', gl_row)}\n"
        f"{_row_text('LHDN row', lhdn_row)}\n"
        "Explain the variance and recommend the accounting action."
    )


def fallback_narrative(gl_row, lhdn_row, anomaly_score: float, bucket: str) -> str:
    """Deterministic template used when the LLM is unavailable."""
    sst_var = total_var = None
    source = gl_row if gl_row is not None else lhdn_row
    if isinstance(source, pd.Series):
        source = source.to_dict()
    if isinstance(source, dict):
        sst_var, total_var = source.get("sst_variance"), source.get("total_variance")
    detail = f"SST variance RM {sst_var}, total variance RM {total_var}."
    action = {
        "SST_Rate_Mismatch": "Recompute the SST at the correct rate and issue a credit/debit note as needed.",
        "Missing_UUID": "Link the LHDN UUID to the GL reference or submit the missing e-invoice.",
        "Unsubmitted": "Submit the sale to MyInvois immediately.",
    }.get(bucket, "Escalate to the finance lead for manual review.")
    return (f"[{bucket}] {detail} Anomaly score "
            f"{float(anomaly_score):.1f}/100. {action} (Template narrative — LLM unavailable.)")


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=45),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_narrative_model(prompt: str, model: str, api_key: str | None = None,
                          max_tokens: int = 160) -> str:
    import litellm  # lazy: keeps the auditor import-light

    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": NARRATIVE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if model.startswith("gemini/"):
        # Gemini 3 Flash thinks at HIGH level by default and bills thought
        # tokens against max_tokens; low thinking keeps short audit replies
        # fast, cheap and untruncated.
        kwargs["reasoning_effort"] = "low"
    if api_key:
        kwargs["api_key"] = api_key
    response = litellm.completion(**kwargs)
    text = response.choices[0].message.content
    if not text or not text.strip():
        raise ValueError("Narrative model returned empty content.")
    # Normalise spacing but preserve line breaks (batched "Row N:" markers).
    return "\n".join(" ".join(line.split()) for line in text.strip().splitlines() if line.strip())


def narrate(
    gl_row,
    lhdn_row,
    anomaly_score: float,
    bucket: str,
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    """Generate (or fall back to) the 2-sentence narrative for one row."""
    results = narrate_batch(
        [{"gl_row": gl_row, "lhdn_row": lhdn_row,
          "anomaly_score": anomaly_score, "bucket": bucket}],
        model=model, api_key=api_key,
    )
    return results[0]


def narrate_batch(
    items: list[dict],
    model: str | None = None,
    api_key: str | None = None,
) -> list[str]:
    """Narrate many flagged rows in ONE model call (quota-friendly).

    Each item: ``{"gl_row", "lhdn_row", "anomaly_score", "bucket"}``.
    Returns one narrative per item, in order; falls back to per-row
    templates (no extra API calls) when the key is missing or the call fails.
    """
    import re as _re

    templates = [fallback_narrative(it.get("gl_row"), it.get("lhdn_row"),
                                    it.get("anomaly_score", float("nan")),
                                    it.get("bucket", ""))
                 for it in items]
    if not items:
        return []
    chosen = model or os.getenv("AI_MODEL", DEFAULT_AUDIT_MODEL)
    key = api_key or next((os.getenv(var) for var in LLM_KEY_VARS if os.getenv(var)), None)
    if not key:
        logger.warning("No LLM API key configured; using template narratives.")
        return templates
    lines = []
    for n, it in enumerate(items, start=1):
        score = it.get("anomaly_score", float("nan"))
        try:
            score_txt = f"{float(score):.1f}"
        except (TypeError, ValueError):
            score_txt = "n/a"
        lines.append(
            f"Row {n} [bucket={it.get('bucket', '')}, anomaly={score_txt}]:\n"
            f"{_row_text('GL', it.get('gl_row'))}\n"
            f"{_row_text('LHDN', it.get('lhdn_row'))}"
        )
    prompt = (
        "For EACH row below write exactly two sentences: sentence 1 explains "
        "the most likely cause of the variance; sentence 2 recommends a "
        "concrete accounting action (e.g. verify SST computation, issue "
        "credit note, submit the missing e-invoice, link the LHDN UUID). "
        "Be specific with the RM amounts. Format: one line per row as "
        "'Row N: <sentence 1> <sentence 2>' with no extra text.\n\n"
        + "\n\n".join(lines)
    )
    try:
        text = _call_narrative_model(prompt, chosen, api_key=key,
                                     max_tokens=min(4000, 500 * len(items) + 300))
    except Exception as exc:
        logger.warning("Narrative LLM failed after retries ({}); using templates.", exc)
        return templates
    parsed = _parse_batch_response(text, len(items))
    if parsed is None:
        if len(items) == 1 and text.strip():
            return [text.strip()]  # single-row replies often skip the "Row 1:" marker
        logger.warning("Could not parse batched narratives; using templates.")
        return templates
    return parsed


def _parse_batch_response(text: str, count: int) -> list[str] | None:
    """Split 'Row N: ...' lines; None when the shape is wrong."""
    import re as _re

    parts = _re.split(r"(?m)^\s*Row\s+(\d+)\s*:\s*", text.strip())
    # parts: ['', '1', 'body1', '2', 'body2', ...]
    bodies: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        try:
            bodies[int(parts[i])] = " ".join(parts[i + 1].strip().split())
        except ValueError:
            return None
    if set(bodies) != set(range(1, count + 1)) or any(not v for v in bodies.values()):
        return None
    return [bodies[n] for n in range(1, count + 1)]


def needs_narrative(bucket: str, anomaly_score: float) -> bool:
    """True for Mismatch rows or any row scoring above 85."""
    try:
        score = float(anomaly_score)
    except (TypeError, ValueError):
        score = float("nan")
    return bucket == "SST_Rate_Mismatch" or (pd.notna(score) and score > ANOMALY_NARRATIVE_THRESHOLD)


def attach_narratives(
    buckets: dict[str, pd.DataFrame],
    model: str | None = None,
    api_key: str | None = None,
    enabled: bool = True,
) -> dict[str, pd.DataFrame]:
    """Fill the ``ai_narrative`` column per the gating rule. Never mutates inputs."""
    enriched: dict[str, pd.DataFrame] = {}
    for bucket, frame in buckets.items():
        out = frame.copy()
        if "ai_narrative" not in out.columns:
            out["ai_narrative"] = ""
        if not enabled or out.empty:
            enriched[bucket] = out
            continue
        narratives = [""] * len(out)
        flagged = [
            (pos, row) for pos, (_, row) in enumerate(out.iterrows())
            if needs_narrative(bucket, row.get("anomaly_score", float("nan")))
        ]
        if flagged:
            batch = narrate_batch(
                [{"gl_row": row, "lhdn_row": row,
                  "anomaly_score": row.get("anomaly_score", float("nan")),
                  "bucket": bucket} for _, row in flagged],
                model=model, api_key=api_key,
            )
            for (pos, _), text in zip(flagged, batch):
                narratives[pos] = text
        out["ai_narrative"] = narratives
        enriched[bucket] = out
    return enriched
