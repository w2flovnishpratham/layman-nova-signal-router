"""Strict C2 control-plane request contracts.

Execution authority is deliberately absent. Unknown fields are rejected so a
caller cannot smuggle owner-independent mode, quantity, broker, or instrument
choices into installation operations.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompileSuccessPayload(_StrictPayload):
    setup_notes: str | None = Field(default=None, max_length=1000)


class CompileFailurePayload(_StrictPayload):
    compiler_error_summary: str = Field(min_length=1, max_length=1000)


class CreateC2InstallationPayload(_StrictPayload):
    conversion_id: UUID
    owner_user_id: UUID
    mode: Literal["MANAGED", "SELF"]
    instance_label: str = Field(min_length=1, max_length=120)


class SuspendC2InstallationPayload(_StrictPayload):
    reason: str = Field(min_length=3, max_length=500)
