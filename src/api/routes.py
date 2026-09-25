"""HTTP routes for the reconciliation API (v1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from src.api.repository import RunRepository
from src.api.schemas import (
    BucketCounts,
    HealthResponse,
    ReconcileResponse,
    RunDetailResponse,
)
from src.api.service import InputValidationError, run_reconciliation, to_record

API_VERSION = "1.0.0"

router = APIRouter(prefix="/api/v1")


def get_store() -> RunRepository:
    from src.api.app import get_repository

    return get_repository()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(version=API_VERSION)


@router.post("/reconcile", response_model=ReconcileResponse, status_code=201)
async def reconcile_endpoint(
    gl_file: UploadFile = File(description="General Ledger CSV"),
    lhdn_file: UploadFile = File(description="LHDN MyInvois JSON export"),
    date_tolerance: int = Form(default=2),
    amount_tolerance: float = Form(default=0.05),
    sst_tolerance: float = Form(default=0.05),
    ai_narratives: bool = Form(default=False),
    store: RunRepository = Depends(get_store),
) -> ReconcileResponse:
    """Run the full pipeline; returns the run record + workbook URL."""
    for label, upload, suffixes in (("gl_file", gl_file, (".csv",)),
                                    ("lhdn_file", lhdn_file, (".json",))):
        filename = (upload.filename or "").lower()
        if not filename.endswith(suffixes):
            raise HTTPException(
                status_code=422, detail=f"{label} must be a {suffixes[0]} file.")
    try:
        gl_bytes, lhdn_bytes = await gl_file.read(), await lhdn_file.read()
        result, workbook = run_reconciliation(
            gl_bytes, lhdn_bytes,
            date_tolerance=date_tolerance,
            amount_tolerance=amount_tolerance,
            sst_tolerance=sst_tolerance,
            ai_narratives=ai_narratives,
        )
    except InputValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record = to_record(result, workbook)
    store.save(record)
    return ReconcileResponse(
        run_id=record.run_id,
        created_at=record.created_at,
        summary=record.summary,
        buckets=BucketCounts(**record.bucket_counts),
        preview=record.buckets_preview,
        workbook_url=f"/api/v1/runs/{record.run_id}/workbook",
    )


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def run_detail(run_id: str, store: RunRepository = Depends(get_store)) -> RunDetailResponse:
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return RunDetailResponse(
        run_id=record.run_id,
        created_at=record.created_at,
        summary=record.summary,
        buckets=BucketCounts(**record.bucket_counts),
        workbook_url=f"/api/v1/runs/{record.run_id}/workbook",
    )


@router.get("/runs/{run_id}/workbook")
def run_workbook(run_id: str, store: RunRepository = Depends(get_store)) -> Response:
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return Response(
        content=record.workbook_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="reconciliation_{run_id}.xlsx"'},
    )


def health_root() -> HealthResponse:
    return HealthResponse(version=API_VERSION)
