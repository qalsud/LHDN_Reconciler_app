"""HTTP middleware: request IDs, per-key rate limiting, access logs."""

from __future__ import annotations

import threading
import time
import uuid

from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

EXEMPT_PATHS = ("/health", "/docs", "/openapi.json", "/redoc", "/")

RATE_LIMIT_PER_MINUTE = 120


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a request ID for log correlation; echo it back to callers."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        logger.bind(request_id=request_id).info(
            "{} {} -> {} ({} ms)", request.method, request.url.path,
            response.status_code, elapsed_ms)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory per-key token bucket (phase-1; Redis in production)."""

    def __init__(self, app, per_minute: int = RATE_LIMIT_PER_MINUTE) -> None:
        super().__init__(app)
        self.per_minute = per_minute
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _identity(self, request: Request) -> str:
        return request.headers.get("X-API-Key") or (request.client.host if request.client else "?")

    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS or not request.url.path.startswith("/api/"):
            return await call_next(request)
        now = time.time()
        key = self._identity(request)
        with self._lock:
            window = [t for t in self._hits.get(key, []) if now - t < 60.0]
            if len(window) >= self.per_minute:
                return JSONResponse(
                    status_code=429,
                    content={"error": "rate_limited", "code": "RATE_LIMIT",
                             "detail": f"Over {self.per_minute} requests/minute."})
            window.append(now)
            self._hits[key] = window
        return await call_next(request)
