"""Tests for the UI API client (httpx.MockTransport — no network)."""

import httpx
import pytest

from src.ui.api_client import (ApiClientError, check_health, download_workbook,
                               fetch_buckets, submit_reconciliation)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_check_health_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok", "version": "1.1.0"})

    assert check_health("http://x", client=_client(handler))["status"] == "ok"


def test_check_health_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ApiClientError, match="API unreachable"):
        check_health("http://x", client=_client(handler))


def test_submit_reconciliation_shapes_request():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("X-API-Key")
        return httpx.Response(201, json={"run_id": "abc123", "summary": {}})

    body = submit_reconciliation(b"a,b\n1,2", "gl.csv", b"[]", "lhdn.json",
                                 api_key="k-1", base_url="http://x",
                                 client=_client(handler))
    assert body["run_id"] == "abc123"
    assert seen["auth"] == "k-1"


def test_submit_missing_key_rejected_locally():
    with pytest.raises(ApiClientError, match="Missing API key"):
        submit_reconciliation(b"x", "gl.csv", b"y", "lhdn.json", api_key="")


def test_submit_api_error_surfaced():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "GL parsing failed: nope"})

    with pytest.raises(ApiClientError, match="422.*GL parsing failed"):
        submit_reconciliation(b"x", "gl.csv", b"y", "lhdn.json",
                              api_key="k", base_url="http://x",
                              client=_client(handler))


def test_fetch_buckets_to_frames():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "k"
        return httpx.Response(200, json={"run_id": "r",
                                         "buckets": {"Matched": [{"tin": "C1"}]}})

    frames = fetch_buckets("r", "k", base_url="http://x", client=_client(handler))
    assert list(frames["Matched"]["tin"]) == ["C1"]


def test_download_workbook_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"PK\x03\x04fake-xlsx",
                              headers={"content-type": "application/octet-stream"})

    assert download_workbook("r", "k", base_url="http://x",
                             client=_client(handler)).startswith(b"PK")
