"""Phase 0 strategy registry service.

Seed/backfill of the existing hardcoded supertrend strategy plus the
service-level immutability guard for approved versions. Nothing in the
execution path reads the registry yet.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import models
from app.db.engine import session_scope

# Matches the canonical name used by strategy_fanout/strategy_risk today so a
# later phase can join registry rows to existing strategy_name columns 1:1.
SUPERTREND_CODE = "supertrend"
SUPERTREND_VERSION = "1.0.0"

APPROVED = "approved"
# The only lifecycle move allowed out of "approved". Content stays frozen.
_ALLOWED_STATUS_FROM_APPROVED = {"deprecated"}
_MUTABLE_FIELDS = {"payload_spec_version", "runtime_definition", "changelog", "execution_kind", "source_journey"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def backfill_supertrend() -> dict[str, Any]:
    """Idempotently register the pre-registry supertrend strategy as the first
    approved Nova-owned catalog entry. Safe to run repeatedly."""
    with session_scope() as db:
        catalog = db.scalar(
            select(models.StrategyCatalog).where(
                models.StrategyCatalog.code == SUPERTREND_CODE,
                models.StrategyCatalog.owner_user_id.is_(None),
            )
        )
        if catalog is None:
            catalog = models.StrategyCatalog(
                code=SUPERTREND_CODE,
                display_name="Supertrend (NIFTY)",
                owner_type="nova",
                visibility="nova_shared",
                status="active",
                description="Nova-operated NIFTY Supertrend strategy (backfilled from the pre-registry hardcoded strategy).",
            )
            db.add(catalog)
            db.flush()
        version = db.scalar(
            select(models.StrategyVersion).where(
                models.StrategyVersion.strategy_id == catalog.id,
                models.StrategyVersion.version == SUPERTREND_VERSION,
            )
        )
        if version is None:
            version = models.StrategyVersion(
                strategy_id=catalog.id,
                version=SUPERTREND_VERSION,
                payload_spec_version="nova.v1",
                source_journey="nova_shared",
                status=APPROVED,
                execution_kind="external_webhook",
                changelog="Backfilled from the pre-registry hardcoded supertrend strategy.",
                approved_at=_now(),
            )
            db.add(version)
            db.flush()
        return {"strategy_id": catalog.id, "version_id": version.id, "version_status": version.status}


def approve_version(version_id: uuid.UUID, *, approved_by_user_id: uuid.UUID | None = None) -> None:
    with session_scope() as db:
        version = db.get(models.StrategyVersion, version_id)
        if version is None:
            raise ValueError("Strategy version not found.")
        if version.status == APPROVED:
            return
        version.status = APPROVED
        version.approved_by_user_id = approved_by_user_id
        version.approved_at = _now()
        version.updated_at = _now()


def update_version(version_id: uuid.UUID, *, status: str | None = None, **fields: Any) -> None:
    """Update a strategy version. Approved versions are immutable: the only
    permitted change is a lifecycle move to a status in _ALLOWED_STATUS_FROM_APPROVED."""
    unknown = set(fields) - _MUTABLE_FIELDS
    if unknown:
        raise ValueError(f"Fields not updatable via registry service: {sorted(unknown)}")
    with session_scope() as db:
        version = db.get(models.StrategyVersion, version_id)
        if version is None:
            raise ValueError("Strategy version not found.")
        if version.status == APPROVED:
            if fields or status not in _ALLOWED_STATUS_FROM_APPROVED:
                raise ValueError("Approved strategy versions are immutable (only deprecation is allowed).")
            version.status = status
            version.updated_at = _now()
            return
        for key, value in fields.items():
            setattr(version, key, value)
        if status is not None:
            version.status = status
        version.updated_at = _now()
