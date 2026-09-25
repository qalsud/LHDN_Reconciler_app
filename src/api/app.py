"""FastAPI application factory.

Run locally with:
    uvicorn src.api.app:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from src.api.repository import InMemoryRunRepository, RunRepository
from src.api.routes import API_VERSION, health_root, router
from src.api.schemas import HealthResponse

_repository: RunRepository = InMemoryRunRepository()


def get_repository() -> RunRepository:
    return _repository


def set_repository(repo: RunRepository) -> None:
    """Swap the backing store (tests, and Postgres in phase 3)."""
    global _repository
    _repository = repo


def create_app() -> FastAPI:
    app = FastAPI(
        title="LHDN Reconciliation API",
        version=API_VERSION,
        description="GL vs MyInvois reconciliation: upload, match, download the audit workbook.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.get("/health", response_model=HealthResponse)(lambda: health_root())

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on {} {}", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "code": "INTERNAL",
                     "detail": "Unexpected failure; the run was not stored."},
        )

    return app


app = create_app()
