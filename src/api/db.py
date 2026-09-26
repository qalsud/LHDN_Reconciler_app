"""Database layer: SQLAlchemy models, engine and sessions.

``DATABASE_URL`` selects the backend (default: local SQLite file at
``./data/app.db``; production: Postgres, e.g.
``postgresql+psycopg://user:pass@db:5432/reconciler``).
Tables are created via :func:`init_db` (documented Alembic path in README).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, ForeignKey, LargeBinary, String,
                        Text, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
    return url


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="completed", nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    counts_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    preview_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    full_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    workbook: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False)


_engine = None
_SessionFactory = None


def get_engine():
    global _engine
    if _engine is None:
        url = database_url()
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (tests switching DATABASE_URL)."""
    global _engine, _SessionFactory
    _engine = None
    _SessionFactory = None


def get_session_factory():
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


def init_db() -> None:
    """Create tables (and the sqlite directory) if missing."""
    url = database_url()
    if url.startswith("sqlite"):
        path = url.split("sqlite:///")[-1].split("?")[0]
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
    Base.metadata.create_all(get_engine())
