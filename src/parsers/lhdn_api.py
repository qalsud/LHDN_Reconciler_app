"""MyInvois API client (LHDN sandbox + production).

Covers the read path the reconciler needs:

  1. ``POST {identity}/connect/token`` — OAuth2 client-credentials login
     (token cached, refreshed with a 60 s buffer; 1 h lifetime per SDK).
  2. ``GET {api}/api/v1.0/documents/recent`` — paged Sent/Received search
     (31-day window, 12 RPM budget respected).
  3. ``GET {api}/api/v1.0/documents/{uuid}/details`` — per-document tax
     figures (the list view has no SST field).

Environment (defaults target the sandbox)::

    MYINVOIS_ENV=preprod            # or "production"
    MYINVOIS_CLIENT_ID=...
    MYINVOIS_CLIENT_SECRET=...

Credentials self-provision via the MyInvois Portal ("View and Register
ERP"); sandbox and production credentials differ.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx
from loguru import logger
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

ENVIRONMENTS = {
    "preprod": "https://preprod-api.myinvois.hasil.gov.my",
    "sandbox": "https://preprod-api.myinvois.hasil.gov.my",
    "production": "https://api.myinvois.hasil.gov.my",
    "prod": "https://api.myinvois.hasil.gov.my",
}

TOKEN_SKEW_SECONDS = 60
REQUEST_TIMEOUT = 30.0
RECENT_PAGE_SIZE = 100
DETAILS_POLITE_DELAY = 0.5  # stays under the 125 RPM details budget
MAX_DOCS_DEFAULT = 200

INDIVIDUAL_PREFIX = "IG"
NON_INDIVIDUAL_PREFIXES = ("C", "CS", "D", "F", "FA", "PT", "TA", "TC",
                           "TN", "TR", "TP", "J", "LE")


class MyInvoisError(RuntimeError):
    """Raised for auth, transport and API errors (cause preserved)."""


def normalize_tin(tin: str | None) -> str:
    """Apply LHDN TIN formatting rules (SDK FAQ):

    - Individual (IG prefix): unchanged, upper-cased.
    - Non-individual (C/CS/D/...): strip leading zeros after the prefix
      and ensure exactly one trailing zero.
    """
    if tin is None:
        return ""
    value = str(tin).strip().upper().replace(" ", "")
    if not value:
        return ""
    if value.startswith(INDIVIDUAL_PREFIX):
        return value
    for prefix in sorted(NON_INDIVIDUAL_PREFIXES, key=len, reverse=True):
        if value.startswith(prefix):
            digits = value[len(prefix):].lstrip("0")
            digits = digits or "0"
            if not digits.endswith("0"):
                digits += "0"
            return f"{prefix}{digits}"
    return value


@dataclass
class MyInvoisClient:
    base_url: str = ENVIRONMENTS["preprod"]
    client_id: str = ""
    client_secret: str = ""
    timeout: float = REQUEST_TIMEOUT
    transport: httpx.BaseTransport | None = None
    _token: str | None = field(default=None, repr=False)
    _token_expires_at: float = field(default=0.0, repr=False)

    @classmethod
    def from_env(cls, **overrides) -> "MyInvoisClient":
        env = (os.getenv("MYINVOIS_ENV", "preprod") or "preprod").lower()
        base_url = ENVIRONMENTS.get(env, ENVIRONMENTS["preprod"])
        return cls(
            base_url=overrides.get("base_url", base_url),
            client_id=overrides.get("client_id", os.getenv("MYINVOIS_CLIENT_ID", "")),
            client_secret=overrides.get("client_secret",
                                        os.getenv("MYINVOIS_CLIENT_SECRET", "")),
            timeout=overrides.get("timeout", REQUEST_TIMEOUT),
            transport=overrides.get("transport"),
        )

    def _http(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url,
                            timeout=self.timeout, transport=self.transport)

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=1, max=10),
           retry=retry_if_exception_type((httpx.HTTPError, MyInvoisError)),
           reraise=True)
    def login(self) -> str:
        """Fetch (and cache) a bearer token via client-credentials flow."""
        if self._token and time.time() < self._token_expires_at - TOKEN_SKEW_SECONDS:
            return self._token
        if not self.client_id or not self.client_secret:
            raise MyInvoisError("MYINVOIS_CLIENT_ID / MYINVOIS_CLIENT_SECRET are not set.")
        try:
            with self._http() as http:
                response = http.post(
                    "/connect/token",
                    data={"client_id": self.client_id,
                          "client_secret": self.client_secret,
                          "grant_type": "client_credentials",
                          "scope": "InvoicingAPI"},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise MyInvoisError(f"Login request failed: {exc}") from exc
        if response.status_code == 429:
            self._sleep_retry_after(response)
            raise MyInvoisError("Login rate-limited (429); retrying.")
        if response.status_code != 200:
            raise MyInvoisError(
                f"Login failed ({response.status_code}): {response.text[:200]}")
        try:
            payload = response.json()
            token = payload["access_token"]
            lifetime = int(payload.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError) as exc:
            raise MyInvoisError("Login response is not valid JSON with access_token.") from exc
        self._token = token
        self._token_expires_at = time.time() + max(lifetime, 1)
        logger.info("MyInvois login ok (token valid ~{} s)", lifetime)
        return token

    @staticmethod
    def _sleep_retry_after(response: httpx.Response) -> None:
        try:
            delay = float(response.headers.get("Retry-After", "5"))
        except (TypeError, ValueError):
            delay = 5.0
        time.sleep(min(max(delay, 1.0), 60.0))

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.login()}"}

    def _get(self, path: str, params: dict | None = None,
             _retried: bool = False) -> httpx.Response:
        try:
            with self._http() as http:
                response = http.get(path, params=params or {}, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            raise MyInvoisError(f"GET {path} failed: {exc}") from exc
        if response.status_code == 401 and not _retried:
            logger.warning("MyInvois token rejected; re-logging in once.")
            self._token, self._token_expires_at = None, 0.0
            return self._get(path, params, _retried=True)
        if response.status_code == 429:
            self._sleep_retry_after(response)
            raise MyInvoisError(f"GET {path} rate-limited (429).")
        if response.status_code != 200:
            raise MyInvoisError(
                f"GET {path} failed ({response.status_code}): {response.text[:200]}")
        return response

    def get_recent_documents(self, direction: str = "Sent", status: str = "Valid",
                             issue_date_from: str | None = None,
                             issue_date_to: str | None = None,
                             issuer_tin: str | None = None,
                             max_docs: int = MAX_DOCS_DEFAULT) -> list[dict]:
        """Page through recent documents (31-day API window enforced server-side)."""
        documents: list[dict] = []
        page_no = 1
        while len(documents) < max_docs:
            params: dict = {"pageNo": page_no, "pageSize": min(RECENT_PAGE_SIZE, max_docs),
                            "InvoiceDirection": direction, "status": status}
            if issue_date_from:
                params["issueDateFrom"] = issue_date_from
            if issue_date_to:
                params["issueDateTo"] = issue_date_to
            if issuer_tin:
                params["issuerTin"] = issuer_tin
            payload = self._get("/api/v1.0/documents/recent", params).json()
            batch = payload.get("result") or []
            documents.extend(batch)
            metadata = payload.get("metadata") or {}
            total_pages = int(metadata.get("totalPages", 1))
            logger.info("MyInvois recent docs: page {}/{} ({} so far)",
                        page_no, total_pages, len(documents))
            if page_no >= total_pages or not batch:
                break
            page_no += 1
        return documents[:max_docs]

    def get_document_details(self, uuid: str) -> dict:
        """Full details for one document (includes tax figures)."""
        return self._get(f"/api/v1.0/documents/{uuid}/details").json()

    def pull_documents(self, max_docs: int = MAX_DOCS_DEFAULT, **filters) -> list[dict]:
        """Recent list + per-document details (bounded; polite spacing)."""
        recent = self.get_recent_documents(max_docs=max_docs, **filters)
        detailed: list[dict] = []
        for doc in recent:
            uuid = doc.get("uuid")
            if not uuid:
                continue
            try:
                detailed.append(self.get_document_details(uuid))
            except MyInvoisError as exc:
                logger.warning("Skipping document {}: {}", uuid, exc)
                continue
            time.sleep(DETAILS_POLITE_DELAY)
        logger.info("Pulled {} detailed document(s) from MyInvois", len(detailed))
        return detailed


def _pick_canonical(record: dict) -> dict:
    """Map one SDK response object (recent-list or details shape) to engine columns."""
    get = lambda *keys: next((record[k] for k in keys if record.get(k) not in (None, "")), None)
    uuid = get("uuid")
    tin = get("issuerTin", "supplierTin", "tin")
    date = get("dateTimeIssued", "invoiceDate", "date")
    ref = get("internalId", "invoiceNo", "invoiceReference", "id")
    total = get("totalPayableAmount", "total", "totalAmount", "netAmount")
    sst = get("sstAmount", "taxAmount")
    excluding = get("totalExcludingTax")
    try:
        total_f: float | None = float(total) if total is not None else None
    except (TypeError, ValueError):
        total_f = None
    try:
        sst_f: float | None = float(sst) if sst is not None else None
    except (TypeError, ValueError):
        sst_f = None
    if sst_f is None and total_f is not None and excluding is not None:
        try:
            sst_f = round(total_f - float(excluding), 2)
        except (TypeError, ValueError):
            sst_f = None
    return {
        "lhdn_uuid": str(uuid).strip() if uuid is not None else "",
        "tin": normalize_tin(tin),
        "invoice_date": date,
        "invoice_reference": str(ref).strip().upper() if ref is not None else "",
        "sst_amount": sst_f,
        "total_amount": total_f,
    }


def records_to_lhdn_df(records: list[dict]):
    """Validate + frame pulled records (same output contract as parse_lhdn_json)."""
    import pandas as pd

    df = pd.DataFrame(records)
    if df.empty:
        raise MyInvoisError("MyInvois returned no documents for the given filters.")
    missing_tin = int((df["tin"] == "").sum())
    if missing_tin:
        logger.warning("{} pulled document(s) have no usable TIN", missing_tin)
    df["invoice_date"] = pd.to_datetime(df["invoice_date"], errors="coerce", utc=True).dt.tz_convert(None)
    bad = int(df["tin"].eq("").sum() + df["invoice_date"].isna().sum()
              + df[["sst_amount", "total_amount"]].isna().sum().sum())
    if bad:
        raise MyInvoisError(
            f"Pulled documents failed validation: {bad} bad value(s) "
            f"(TIN/date/SST/total). Narrow the filters or inspect the payloads.")
    return df.reset_index(drop=True)


def pull_lhdn_df(client: MyInvoisClient | None = None, **filters):
    """End-to-end: pull from MyInvois -> canonical LHDN DataFrame."""
    client = client or MyInvoisClient.from_env()
    detailed = client.pull_documents(**filters)
    return records_to_lhdn_df([_pick_canonical(doc) for doc in detailed])
