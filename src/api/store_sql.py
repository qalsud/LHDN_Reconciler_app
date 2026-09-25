"""SQL run repository: Postgres/SQLite persistence behind RunRepository.

Supersedes InMemoryRunRepository for server deployments; the in-memory
impl remains for unit tests. Adds audit helpers (events, tenant listing).
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from src.api import db as database
from src.api.db import Run, RunEvent, utcnow
from src.api.repository import RunRecord


def record_to_row(record: RunRecord, tenant_id: str) -> Run:
    return Run(
        id=record.run_id,
        tenant_id=tenant_id,
        created_at=record.created_at,
        status="completed",
        summary_json=json.dumps(record.summary),
        counts_json=json.dumps(record.bucket_counts),
        preview_json=json.dumps(record.buckets_preview),
        workbook=record.workbook_bytes,
    )


def row_to_record(row: Run) -> RunRecord:
    created = row.created_at
    if created is not None and created.tzinfo is None:
        from datetime import timezone
        created = created.replace(tzinfo=timezone.utc)
    return RunRecord(
        run_id=row.id,
        created_at=created or utcnow(),
        summary=json.loads(row.summary_json or "{}"),
        bucket_counts=json.loads(row.counts_json or "{}"),
        workbook_bytes=row.workbook or b"",
        buckets_preview=json.loads(row.preview_json or "{}"),
    )


class SqlRunRepository:
    """Persistent runs + audit events, scoped per tenant."""

    def __init__(self, session_factory=None) -> None:
        self._factory = session_factory or database.get_session_factory()

    def _session(self) -> Session:
        return self._factory()

    # -- RunRepository protocol -------------------------------------------------
    def save(self, record: RunRecord, tenant_id: str = "") -> None:
        session = self._session()
        try:
            session.merge(record_to_row(record, tenant_id))
            session.commit()
        finally:
            session.close()

    def get(self, run_id: str, tenant_id: str | None = None) -> RunRecord | None:
        session = self._session()
        try:
            query = session.query(Run).filter_by(id=run_id)
            if tenant_id is not None:
                query = query.filter_by(tenant_id=tenant_id)
            row = query.one_or_none()
            return row_to_record(row) if row is not None else None
        finally:
            session.close()

    def __len__(self) -> int:
        session = self._session()
        try:
            return session.query(Run).count()
        finally:
            session.close()

    # -- Audit ------------------------------------------------------------------
    def log_event(self, run_id: str, tenant_id: str,
                  event_type: str, detail: str = "") -> None:
        session = self._session()
        try:
            session.add(RunEvent(run_id=run_id, tenant_id=tenant_id,
                                 event_type=event_type, detail=detail,
                                 created_at=utcnow()))
            session.commit()
        finally:
            session.close()

    def events(self, run_id: str, tenant_id: str) -> list[dict]:
        session = self._session()
        try:
            rows = (session.query(RunEvent)
                    .filter_by(run_id=run_id, tenant_id=tenant_id)
                    .order_by(RunEvent.id).all())
            return [{"event_type": r.event_type, "detail": r.detail,
                     "created_at": (r.created_at.isoformat()
                                    if isinstance(r.created_at, datetime) else None)}
                    for r in rows]
        finally:
            session.close()

    def list_runs(self, tenant_id: str, limit: int = 50) -> list[dict]:
        session = self._session()
        try:
            rows = (session.query(Run)
                    .filter_by(tenant_id=tenant_id)
                    .order_by(Run.created_at.desc()).limit(max(1, min(limit, 200))).all())
            return [{"run_id": r.id,
                     "created_at": r.created_at.isoformat() if r.created_at else None,
                     "status": r.status,
                     "buckets": json.loads(r.counts_json or "{}")} for r in rows]
        finally:
            session.close()
