"""API-key authentication + tenant provisioning (SHA-256 hashed keys)."""

from __future__ import annotations

import hashlib
import secrets
import uuid

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from src.api import db as database

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_api_key(prefix: str = "lhdn") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def create_tenant(session: Session, name: str, raw_key: str | None = None) -> tuple[str, str]:
    """Create a tenant; returns (tenant_id, raw_api_key). Store only the hash."""
    from src.api.db import Tenant

    raw = raw_key or new_api_key()
    tenant = Tenant(id=uuid.uuid4().hex, name=name,
                    key_hash=hash_key(raw), key_prefix=raw[:12])
    session.add(tenant)
    session.commit()
    return tenant.id, raw


def ensure_seed_tenant(session: Session, name: str, raw_key: str) -> str:
    """Idempotent bootstrap from TENANT_SEED (name:key); returns tenant id."""
    from src.api.db import Tenant

    existing = session.query(Tenant).filter_by(name=name).one_or_none()
    if existing is not None:
        return existing.id
    tenant_id, _ = create_tenant(session, name, raw_key)
    return tenant_id


def get_session():
    factory = database.get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_current_tenant(api_key: str | None = Depends(api_key_header),
                       session: Session = Depends(get_session)):
    """Dependency: valid active tenant or HTTP 401 (never 403 Oracles)."""
    from src.api.db import Tenant

    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header.")
    tenant = session.query(Tenant).filter_by(
        key_hash=hash_key(api_key), is_active=True).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key.")
    return tenant
