"""FastAPI application factory.

Run locally with:
    uvicorn src.api.app:app --reload --port 8000

Environment:
    DATABASE_URL   sqlite (default ./data/app.db) or Postgres URL
    TENANT_SEED    "name:raw-key" bootstrap tenant created on startup
    ADMIN_KEY      enables POST /api/v1/admin/tenants
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src.api import db as database
from src.api.auth import ensure_seed_tenant
from src.api.middleware import RateLimitMiddleware, RequestIDMiddleware
from src.api.repository import RunRepository
from src.api.routes import API_VERSION, health_root, router
from src.api.schemas import HealthResponse
from src.api.store_sql import SqlRunRepository

_repository: RunRepository = SqlRunRepository()


def get_repository() -> RunRepository:
    return _repository


def set_repository(repo: RunRepository) -> None:
    """Swap the backing store (tests only)."""
    global _repository
    _repository = repo


def seed_bootstrap_tenant() -> None:
    seed = os.getenv("TENANT_SEED", "")
    if not seed or ":" not in seed:
        return
    name, _, raw_key = seed.partition(":")
    if not name.strip() or not raw_key.strip():
        return
    factory = database.get_session_factory()
    session = factory()
    try:
        tenant_id = ensure_seed_tenant(session, name.strip(), raw_key.strip())
        logger.info("Bootstrap tenant ready: {} ({})", name.strip(), tenant_id)
    finally:
        session.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    seed_bootstrap_tenant()
    logger.info("API v{} ready (db={})", API_VERSION, database.database_url().split("://")[0])
    yield


def create_app(use_sql_store: bool = True) -> FastAPI:
    global _repository
    if use_sql_store and not isinstance(_repository, SqlRunRepository):
        _repository = SqlRunRepository()
    app = FastAPI(
        title="LHDN Reconciliation API",
        version=API_VERSION,
        description="GL vs MyInvois reconciliation: upload, match, download the audit workbook.",
        lifespan=lifespan,
    )
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.get("/health", response_model=HealthResponse)(lambda: health_root())
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="site")

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.bind(request_id=getattr(request.state, "request_id", "?")).exception(
            "Unhandled error on {} {}", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "code": "INTERNAL",
                     "detail": "Unexpected failure; the run was not stored."},
        )

    return app


app = create_app()
