"""Pydantic schemas for the reconciliation API (v1)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    engine: str = "pandas-deterministic"


class BucketCounts(BaseModel):
    Matched: int = 0
    Unsubmitted_Sales: int = 0
    SST_Rate_Mismatch: int = 0
    Missing_UUID: int = 0


class ReconcileResponse(BaseModel):
    run_id: str
    created_at: datetime
    summary: dict
    buckets: BucketCounts
    preview_rows_per_bucket: int = Field(default=5, ge=0, le=50)
    preview: dict[str, list[dict]] = Field(default_factory=dict)
    workbook_url: str


class RunDetailResponse(BaseModel):
    run_id: str
    created_at: datetime
    summary: dict
    buckets: BucketCounts
    workbook_url: str


class ErrorResponse(BaseModel):
    error: str
    code: str
    detail: str = ""
