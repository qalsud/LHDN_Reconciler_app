"""Thin HTTP client so the Streamlit UI goes through the API (auth included).

No silent local fallback: if the API is unreachable, callers surface a
clear error telling the operator how to start it.
"""

from __future__ import annotations

import os

import httpx
import pandas as pd

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
REQUEST_TIMEOUT = 120.0


class ApiClientError(RuntimeError):
    """Raised for transport failures and API error responses."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def api_base_url() -> str:
    return os.getenv("API_BASE_URL", API_BASE_URL).rstrip("/")


def _headers(api_key: str) -> dict[str, str]:
    if not api_key:
        raise ApiClientError(
            "Missing API key. Paste a tenant key in the sidebar "
            "(mint one via POST /api/v1/admin/tenants).")
    return {"X-API-Key": api_key}


def _raise_for_status(response: httpx.Response, action: str) -> None:
    if response.status_code < 400:
        return
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    raise ApiClientError(f"{action} failed ({response.status_code}): {detail}",
                         status_code=response.status_code)


def check_health(base_url: str | None = None, client: httpx.Client | None = None) -> dict:
    """GET /health. Raises ApiClientError when the API is down."""
    url = f"{base_url or api_base_url()}/health"
    own = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            raise ApiClientError(
                f"API unreachable at {url}. Start it with "
                f"'python -m uvicorn src.api.app:app --port 8000'. ({exc})") from exc
        _raise_for_status(response, "Health check")
        return response.json()
    finally:
        if own:
            client.close()


def submit_reconciliation(
    gl_bytes: bytes,
    gl_filename: str,
    lhdn_bytes: bytes,
    lhdn_filename: str,
    api_key: str,
    date_tolerance: int = 2,
    amount_tolerance: float = 0.05,
    sst_tolerance: float = 0.05,
    ai_narratives: bool = False,
    base_url: str | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """POST /api/v1/reconcile. Returns the run response JSON."""
    url = f"{base_url or api_base_url()}/api/v1/reconcile"
    own = client is None
    client = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        try:
            response = client.post(
                url,
                headers=_headers(api_key),
                files={"gl_file": (gl_filename, gl_bytes, "text/csv"),
                       "lhdn_file": (lhdn_filename, lhdn_bytes, "application/json")},
                data={"date_tolerance": str(date_tolerance),
                      "amount_tolerance": str(amount_tolerance),
                      "sst_tolerance": str(sst_tolerance),
                      "ai_narratives": str(bool(ai_narratives)).lower()},
            )
        except httpx.HTTPError as exc:
            raise ApiClientError(f"API unreachable at {url}. ({exc})") from exc
        _raise_for_status(response, "Reconciliation")
        return response.json()
    finally:
        if own:
            client.close()


def fetch_buckets(run_id: str, api_key: str,
                  base_url: str | None = None,
                  client: httpx.Client | None = None) -> dict[str, pd.DataFrame]:
    """GET /runs/{id}/buckets -> {bucket_name: DataFrame}."""
    url = f"{base_url or api_base_url()}/api/v1/runs/{run_id}/buckets"
    own = client is None
    client = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        try:
            response = client.get(url, headers=_headers(api_key))
        except httpx.HTTPError as exc:
            raise ApiClientError(f"API unreachable at {url}. ({exc})") from exc
        _raise_for_status(response, "Bucket fetch")
        return {name: pd.DataFrame(rows)
                for name, rows in response.json().get("buckets", {}).items()}
    finally:
        if own:
            client.close()


def download_workbook(run_id: str, api_key: str,
                      base_url: str | None = None,
                      client: httpx.Client | None = None) -> bytes:
    """GET /runs/{id}/workbook -> xlsx bytes."""
    url = f"{base_url or api_base_url()}/api/v1/runs/{run_id}/workbook"
    own = client is None
    client = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        try:
            response = client.get(url, headers=_headers(api_key))
        except httpx.HTTPError as exc:
            raise ApiClientError(f"API unreachable at {url}. ({exc})") from exc
        _raise_for_status(response, "Workbook download")
        return response.content
    finally:
        if own:
            client.close()
