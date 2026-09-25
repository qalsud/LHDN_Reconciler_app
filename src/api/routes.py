"""HTTP routes for the reconciliation API (v1). All routes require a tenant key."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from src.api.auth import create_tenant, get_current_tenant, get_session
from src.api.repository import RunRepository
from src.api.schemas import (
    BucketCounts,
    HealthResponse,
    ReconcileResponse,
    RunDetailResponse,
)
from src.api.service import InputValidationError, run_reconciliation, to_record

API_VERSION = "1.1.0"

router = APIRouter(prefix="/api/v1")


def get_store() -> RunRepository:
    from src.api.app import get_repository

    return get_repository()


class TenantCreate(BaseModel):
    name: str


class TenantCreated(BaseModel):
    tenant_id: str
    name: str
    api_key: str


class RunListItem(BaseModel):
    run_id: str
    created_at: str | None
    status: str
    buckets: dict


class RunEventItem(BaseModel):
    event_type: str
    detail: str = ""
    created_at: str | None = None


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
    tenant=Depends(get_current_tenant),
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
    store.save(record, tenant_id=tenant.id)
    if hasattr(store, "log_event"):
        store.log_event(record.run_id, tenant.id, "run_completed",
                        f"matched={record.summary.get('matched')}")
    return ReconcileResponse(
        run_id=record.run_id,
        created_at=record.created_at,
        summary=record.summary,
        buckets=BucketCounts(**record.bucket_counts),
        preview=record.buckets_preview,
        workbook_url=f"/api/v1/runs/{record.run_id}/workbook",
    )


@router.get("/runs", response_model=list[RunListItem])
def list_runs(limit: int = 50,
              store: RunRepository = Depends(get_store),
              tenant=Depends(get_current_tenant)):
    if not hasattr(store, "list_runs"):
        raise HTTPException(status_code=501, detail="Run listing needs the SQL store.")
    return [RunListItem(**item) for item in store.list_runs(tenant.id, limit=limit)]


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def run_detail(run_id: str,
               store: RunRepository = Depends(get_store),
               tenant=Depends(get_current_tenant)) -> RunDetailResponse:
    record = store.get(run_id, tenant_id=tenant.id)
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
def run_workbook(run_id: str,
                 store: RunRepository = Depends(get_store),
                 tenant=Depends(get_current_tenant)) -> Response:
    record = store.get(run_id, tenant_id=tenant.id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    if hasattr(store, "log_event"):
        store.log_event(record.run_id, tenant.id, "workbook_downloaded", "")
    return Response(
        content=record.workbook_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="reconciliation_{run_id}.xlsx"'},
    )


@router.get("/runs/{run_id}/events", response_model=list[RunEventItem])
def run_events(run_id: str,
               store: RunRepository = Depends(get_store),
               tenant=Depends(get_current_tenant)):
    if hasattr(store, "get") and store.get(run_id, tenant_id=tenant.id) is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    if not hasattr(store, "events"):
        raise HTTPException(status_code=501, detail="Audit events need the SQL store.")
    return [RunEventItem(**item) for item in store.events(run_id, tenant.id)]


@router.post("/admin/tenants", response_model=TenantCreated, status_code=201)
def admin_create_tenant(payload: TenantCreate,
                        session=Depends(get_session),
                        tenant=Depends(get_current_tenant),
                        admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    """Mint a tenant key. Needs a valid tenant key AND the ADMIN_KEY secret."""
    expected = os.getenv("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        raise HTTPException(status_code=403, detail="Admin access denied.")
    tenant_id, raw_key = create_tenant(session, payload.name)
    return TenantCreated(tenant_id=tenant_id, name=payload.name, api_key=raw_key)


def health_root() -> HealthResponse:
    return HealthResponse(version=API_VERSION)
