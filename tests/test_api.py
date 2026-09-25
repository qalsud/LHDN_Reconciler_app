"""Tests for the FastAPI service layer (no network use)."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app, set_repository
from src.api.repository import InMemoryRunRepository


@pytest.fixture
def client():
    set_repository(InMemoryRunRepository(max_runs=10))
    return TestClient(create_app(), raise_server_exceptions=False)


def _files(tmp_path=None):
    gl = open("samples/gl_sample.csv", "rb").read()
    lhdn = open("samples/lhdn_sample.json", "rb").read()
    return {"gl_file": ("gl.csv", gl, "text/csv"),
            "lhdn_file": ("lhdn.json", lhdn, "application/json")}


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_reconcile_sample_population(client):
    response = client.post("/api/v1/reconcile", files=_files())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["summary"]["matched"] == 6
    assert body["buckets"] == {"Matched": 6, "Unsubmitted_Sales": 3,
                               "SST_Rate_Mismatch": 2, "Missing_UUID": 2}
    assert len(body["preview"]["Matched"]) == 5
    assert body["workbook_url"].startswith("/api/v1/runs/")


def test_reconcile_then_download_and_detail(client):
    run_id = client.post("/api/v1/reconcile", files=_files()).json()["run_id"]

    detail = client.get(f"/api/v1/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["run_id"] == run_id

    book = client.get(f"/api/v1/runs/{run_id}/workbook")
    assert book.status_code == 200
    assert "spreadsheetml.sheet" in book.headers["content-type"]
    with zipfile.ZipFile(io.BytesIO(book.content)) as zf:
        names = zf.namelist()
    assert any("workbook.xml" in n for n in names)


def test_reconcile_bad_csv_rejected(client):
    gl = b"Transaction Date,TIN\n2026-08-01,C1\n"
    lhdn = open("samples/lhdn_sample.json", "rb").read()
    response = client.post("/api/v1/reconcile", files={
        "gl_file": ("gl.csv", gl, "text/csv"),
        "lhdn_file": ("lhdn.json", lhdn, "application/json")})
    assert response.status_code == 422


def test_reconcile_wrong_extension_rejected(client):
    gl = open("samples/gl_sample.csv", "rb").read()
    response = client.post("/api/v1/reconcile", files={
        "gl_file": ("gl.txt", gl, "text/plain"),
        "lhdn_file": ("lhdn.json", b"[]", "application/json")})
    assert response.status_code == 422


def test_reconcile_bad_tolerance_rejected(client):
    response = client.post("/api/v1/reconcile", files=_files(),
                           data={"date_tolerance": "99"})
    assert response.status_code == 422


def test_unknown_run_404(client):
    assert client.get("/api/v1/runs/doesnotexist").status_code == 404
    assert client.get("/api/v1/runs/doesnotexist/workbook").status_code == 404


def test_repository_eviction():
    from src.api.repository import RunRecord, utcnow

    repo = InMemoryRunRepository(max_runs=2)
    for i in range(3):
        repo.save(RunRecord(run_id=f"r{i}", created_at=utcnow(),
                            summary={}, bucket_counts={}, workbook_bytes=b"x"))
    assert len(repo) == 2
    assert repo.get("r0") is None
    assert repo.get("r2") is not None
