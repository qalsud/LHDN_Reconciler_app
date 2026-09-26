"""Tests for the FastAPI service: auth, runs, audit, rate limits (no network)."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from src.api import db as database
from src.api.app import create_app, set_repository
from src.api.auth import create_tenant
from src.api.store_sql import SqlRunRepository


@pytest.fixture
def authed(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    database.reset_engine()
    database.init_db()
    set_repository(SqlRunRepository())
    factory = database.get_session_factory()
    session = factory()
    try:
        tenant_id, raw_key = create_tenant(session, "acme")
    finally:
        session.close()
    client = TestClient(create_app(), raise_server_exceptions=False)
    headers = {"X-API-Key": raw_key}
    yield client, headers, tenant_id
    database.reset_engine()


def _files():
    gl = open("samples/gl_sample.csv", "rb").read()
    lhdn = open("samples/lhdn_sample.json", "rb").read()
    return {"gl_file": ("gl.csv", gl, "text/csv"),
            "lhdn_file": ("lhdn.json", lhdn, "application/json")}


def test_health_open(authed):
    client, _, _ = authed
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_site_root(authed):
    client, _, _ = authed
    response = client.get("/")
    assert response.status_code == 200
    assert "LHDN Reconciler" in response.text


def test_reconcile_requires_key(authed):
    client, _, _ = authed
    assert client.post("/api/v1/reconcile", files=_files()).status_code == 401
    bad = client.post("/api/v1/reconcile", files=_files(),
                      headers={"X-API-Key": "wrong"})
    assert bad.status_code == 401


def test_reconcile_sample_population(authed):
    client, headers, _ = authed
    response = client.post("/api/v1/reconcile", files=_files(), headers=headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["summary"]["matched"] == 6
    assert body["buckets"] == {"Matched": 6, "Unsubmitted_Sales": 3,
                               "SST_Rate_Mismatch": 2, "Missing_UUID": 2}
    assert len(body["preview"]["Matched"]) == 5
    assert body["workbook_url"].startswith("/api/v1/runs/")


def test_runs_list_detail_workbook_events(authed):
    client, headers, _ = authed
    run_id = client.post("/api/v1/reconcile", files=_files(), headers=headers).json()["run_id"]

    listing = client.get("/api/v1/runs", headers=headers)
    assert listing.status_code == 200
    assert any(item["run_id"] == run_id for item in listing.json())

    detail = client.get(f"/api/v1/runs/{run_id}", headers=headers)
    assert detail.status_code == 200

    book = client.get(f"/api/v1/runs/{run_id}/workbook", headers=headers)
    assert book.status_code == 200
    assert "spreadsheetml.sheet" in book.headers["content-type"]
    with zipfile.ZipFile(io.BytesIO(book.content)) as zf:
        assert any("workbook.xml" in n for n in zf.namelist())

    events = client.get(f"/api/v1/runs/{run_id}/events", headers=headers)
    assert events.status_code == 200
    kinds = {e["event_type"] for e in events.json()}
    assert {"run_completed", "workbook_downloaded"} <= kinds


def test_tenant_isolation(authed):
    client, headers, _ = authed
    run_id = client.post("/api/v1/reconcile", files=_files(), headers=headers).json()["run_id"]

    factory = database.get_session_factory()
    session = factory()
    try:
        _, other_key = create_tenant(session, "other-co")
    finally:
        session.close()
    other = {"X-API-Key": other_key}
    assert client.get(f"/api/v1/runs/{run_id}", headers=other).status_code == 404
    assert client.get("/api/v1/runs", headers=other).json() == []


def test_reconcile_bad_csv_rejected(authed):
    client, headers, _ = authed
    gl = b"Transaction Date,TIN\n2026-08-01,C1\n"
    lhdn = open("samples/lhdn_sample.json", "rb").read()
    response = client.post("/api/v1/reconcile", files={
        "gl_file": ("gl.csv", gl, "text/csv"),
        "lhdn_file": ("lhdn.json", lhdn, "application/json")}, headers=headers)
    assert response.status_code == 422


def test_reconcile_wrong_extension_rejected(authed):
    client, headers, _ = authed
    gl = open("samples/gl_sample.csv", "rb").read()
    response = client.post("/api/v1/reconcile", files={
        "gl_file": ("gl.txt", gl, "text/plain"),
        "lhdn_file": ("lhdn.json", b"[]", "application/json")}, headers=headers)
    assert response.status_code == 422


def test_reconcile_bad_tolerance_rejected(authed):
    client, headers, _ = authed
    response = client.post("/api/v1/reconcile", files=_files(),
                           data={"date_tolerance": "99"}, headers=headers)
    assert response.status_code == 422


def test_unknown_run_404(authed):
    client, headers, _ = authed
    assert client.get("/api/v1/runs/doesnotexist", headers=headers).status_code == 404
    assert client.get("/api/v1/runs/doesnotexist/workbook", headers=headers).status_code == 404
    assert client.get("/api/v1/runs/doesnotexist/buckets", headers=headers).status_code == 404
    assert client.get("/api/v1/runs/doesnotexist/events", headers=headers).status_code == 404


def test_run_buckets_full_rows(authed):
    client, headers, _ = authed
    run_id = client.post("/api/v1/reconcile", files=_files(), headers=headers).json()["run_id"]
    response = client.get(f"/api/v1/runs/{run_id}/buckets", headers=headers)
    assert response.status_code == 200
    buckets = response.json()["buckets"]
    assert set(buckets) == {"Matched", "Unsubmitted_Sales", "SST_Rate_Mismatch", "Missing_UUID"}
    assert len(buckets["Matched"]) == 6
    assert len(buckets["Unsubmitted_Sales"]) == 3
    assert "anomaly_score" in buckets["Matched"][0]
    assert "ai_narrative" in buckets["Matched"][0]


def test_admin_provisioning_guarded(authed, monkeypatch):
    client, headers, _ = authed
    # No ADMIN_KEY configured -> always 403.
    denied = client.post("/api/v1/admin/tenants", json={"name": "new-co"}, headers=headers)
    assert denied.status_code == 403

    monkeypatch.setenv("ADMIN_KEY", "s3cret-admin")
    created = client.post("/api/v1/admin/tenants", json={"name": "new-co"},
                          headers={**headers, "X-Admin-Key": "s3cret-admin"})
    assert created.status_code == 201
    new_key = created.json()["api_key"]
    assert client.get("/api/v1/runs", headers={"X-API-Key": new_key}).status_code == 200

    wrong_admin = client.post("/api/v1/admin/tenants", json={"name": "evil"},
                              headers={**headers, "X-Admin-Key": "nope"})
    assert wrong_admin.status_code == 403


def test_rate_limit():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient as _TestClient

    from src.api.middleware import RateLimitMiddleware

    small = FastAPI()
    small.add_middleware(RateLimitMiddleware, per_minute=2)

    @small.get("/api/v1/ping")
    def ping():
        return JSONResponse({"pong": True})

    probe = _TestClient(small, raise_server_exceptions=False)
    headers = {"X-API-Key": "probe-key"}
    assert probe.get("/api/v1/ping", headers=headers).status_code == 200
    assert probe.get("/api/v1/ping", headers=headers).status_code == 200
    limited = probe.get("/api/v1/ping", headers=headers)
    assert limited.status_code == 429
    assert limited.json()["code"] == "RATE_LIMIT"
    # Health/docs stay exempt.
    assert probe.get("/health").status_code in (200, 404)


def test_repository_eviction():
    from datetime import datetime, timezone

    from src.api.repository import InMemoryRunRepository, RunRecord

    repo = InMemoryRunRepository(max_runs=2)
    for i in range(3):
        repo.save(RunRecord(run_id=f"r{i}", created_at=datetime.now(timezone.utc),
                            summary={}, bucket_counts={}, workbook_bytes=b"x"))
    assert len(repo) == 2
    assert repo.get("r0") is None
    assert repo.get("r2") is not None
