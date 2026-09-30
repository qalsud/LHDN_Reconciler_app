"""Tests for the MyInvois API client (httpx.MockTransport — no network)."""

import httpx
import pytest

from src.parsers.lhdn_api import (MyInvoisClient, MyInvoisError, _pick_canonical,
                                  normalize_tin, records_to_lhdn_df)


def _transport(routes: dict[tuple[str, str], object]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key not in routes:
            return httpx.Response(404, json={"error": "not found"})
        status, payload = routes[key]
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


def _client(routes, **overrides) -> MyInvoisClient:
    return MyInvoisClient(base_url="https://test.invalid", client_id="id",
                          client_secret="secret", transport=_transport(routes),
                          **overrides)


def test_login_caches_token():
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": "tok-1",
                                         "token_type": "Bearer", "expires_in": 3600})

    client = MyInvoisClient(base_url="https://test.invalid", client_id="i",
                            client_secret="s",
                            transport=httpx.MockTransport(handler))
    assert client.login() == "tok-1"
    assert client.login() == "tok-1"  # cached: exactly one HTTP call
    assert len(calls) == 1


def test_login_missing_credentials():
    client = MyInvoisClient(base_url="https://test.invalid", transport=_transport({}))
    with pytest.raises(MyInvoisError, match="not set"):
        client.login()


def test_recent_documents_paging():
    page1 = [{"uuid": "u-1", "internalId": "A", "issuerTin": "C1",
              "dateTimeIssued": "2026-08-01T10:00:00Z", "total": 106.0}]
    page2 = [{"uuid": "u-2", "internalId": "B", "issuerTin": "C1",
              "dateTimeIssued": "2026-08-02T10:00:00Z", "total": 206.0}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/connect/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        page = int(request.url.params.get("pageNo", "1"))
        batch = page1 if page == 1 else page2
        return httpx.Response(200, json={"result": batch,
                                         "metadata": {"totalPages": 2, "totalCount": 2}})

    client = MyInvoisClient(base_url="https://test.invalid", client_id="i",
                            client_secret="s", transport=httpx.MockTransport(handler))
    docs = client.get_recent_documents()
    assert [d["uuid"] for d in docs] == ["u-1", "u-2"]


def test_401_triggers_relogin_once():
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/connect/token":
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 60})
        if len([c for c in calls if c.startswith("/api")]) == 1:
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json={"result": [], "metadata": {"totalPages": 1}})

    client = MyInvoisClient(base_url="https://test.invalid", client_id="i",
                            client_secret="s", transport=httpx.MockTransport(handler))
    assert client.get_recent_documents() == []
    assert calls.count("/connect/token") == 2  # initial + one refresh


def test_pick_canonical_both_shapes():
    recent = {"uuid": "u-1", "internalId": "PZ-1", "supplierTin": "C2584563200",
              "dateTimeIssued": "2026-08-01T10:00:00Z",
              "netAmount": 100.70, "total": 124.09, "status": "Valid"}
    row = _pick_canonical(recent)
    assert row["tin"] == "C2584563200"
    assert row["invoice_reference"] == "PZ-1"
    assert row["total_amount"] == pytest.approx(124.09)

    details = {"uuid": "u-2", "internalId": "PZ-2", "issuerTin": "C01234567890",
               "dateTimeIssued": "2026-08-02T10:00:00Z",
               "totalExcludingTax": 100.0, "totalPayableAmount": 106.0}
    row = _pick_canonical(details)
    assert row["tin"] == "C1234567890"  # leading zero stripped per FAQ
    assert row["sst_amount"] == pytest.approx(6.0)


def test_records_to_lhdn_df_validation():
    df = records_to_lhdn_df([_pick_canonical({
        "uuid": "u-1", "internalId": "A", "issuerTin": "C1",
        "dateTimeIssued": "2026-08-01T10:00:00Z",
        "totalExcludingTax": 100.0, "totalPayableAmount": 106.0})])
    assert len(df) == 1
    assert df.loc[0, "sst_amount"] == pytest.approx(6.0)
    with pytest.raises(MyInvoisError, match="no documents"):
        records_to_lhdn_df([])


def test_normalize_tin_rules():
    assert normalize_tin("C01234567890") == "C1234567890"  # strip leading zero
    assert normalize_tin("C123456789") == "C1234567890"    # pad trailing zero
    assert normalize_tin("C1234567890") == "C1234567890"   # already canonical
    assert normalize_tin("IG12345678901") == "IG12345678901"  # individuals untouched
    assert normalize_tin("  c98765432010 ") == "C98765432010"
    assert normalize_tin(None) == ""
    assert normalize_tin("") == ""
