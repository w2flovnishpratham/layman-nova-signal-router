"""Request schemas for strategy-instance control endpoints (Phase 1)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.services.strategy_instance_service import MAX_LOTS, MIN_LOTS


class SelectStrategyPayload(BaseModel):
    strategy_instance_id: uuid.UUID


class CreateInstancePayload(BaseModel):
    strategy_code: str = Field(min_length=1, max_length=80)
    source_journey: str = Field(default="NOVA_SHARED", max_length=30)
    label: str | None = Field(default=None, max_length=120)
    lots: int = Field(default=1, ge=MIN_LOTS, le=MAX_LOTS)
    execution_mode: str = Field(default="signal_only", max_length=30)


class CloneInstancePayload(BaseModel):
    label: str | None = Field(default=None, max_length=120)


class UpdateLotsPayload(BaseModel):
    lots: int = Field(ge=MIN_LOTS, le=MAX_LOTS)


class StatusReasonPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=200)
