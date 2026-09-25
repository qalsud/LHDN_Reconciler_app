"""Run repository: storage seam for completed reconciliations.

Phase 1 ships an in-memory implementation (bounded, oldest-first eviction).
Phase 3 (Postgres persistence) replaces it behind the same interface —
service code must only depend on :class:`RunRepository`, never the impl.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol


@dataclass
class RunRecord:
    run_id: str
    created_at: datetime
    summary: dict
    bucket_counts: dict[str, int]
    workbook_bytes: bytes
    buckets_preview: dict[str, list[dict]] = field(default_factory=dict)


class RunRepository(Protocol):
    """Storage contract for reconciliation runs."""

    def save(self, record: RunRecord) -> None: ...
    def get(self, run_id: str) -> RunRecord | None: ...
    def __len__(self) -> int: ...


class InMemoryRunRepository:
    """Thread-safe bounded in-memory store (dev/phase-1 only)."""

    def __init__(self, max_runs: int = 100) -> None:
        self._max_runs = max(1, max_runs)
        self._runs: OrderedDict[str, RunRecord] = OrderedDict()
        self._lock = Lock()

    def save(self, record: RunRecord) -> None:
        with self._lock:
            self._runs[record.run_id] = record
            self._runs.move_to_end(record.run_id)
            while len(self._runs) > self._max_runs:
                self._runs.popitem(last=False)

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._runs)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
