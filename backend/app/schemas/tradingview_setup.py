from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


SetupType = Literal["USER_MANAGED_TRADINGVIEW", "NOVA_MANAGED_TRADINGVIEW"]


class CreateSetupPayload(BaseModel):
    setup_type: SetupType
    requested_timeframe: str | None = Field(default=None, max_length=20)


class ResetSetupPayload(CreateSetupPayload):
    reason: str = Field(min_length=3, max_length=500)


class CompilationPayload(BaseModel):
    compiled: Literal[True]


class InstallationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installed_version_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    # Descriptive admin notes only -- the real identity check is
    # installed_version_hash against the approved immutable version's
    # source_sha256. Optional: the installation is already identified
    # without them, so record_installation fills in a deterministic default
    # when left blank instead of blocking the admin on two free-text boxes.
    workspace_reference: str | None = Field(default=None, max_length=120)
    alert_reference: str | None = Field(default=None, max_length=120)
    symbol: str = Field(min_length=1, max_length=30)
    timeframe: str = Field(min_length=1, max_length=20)
    installed_at: datetime


class SetupStatePayload(BaseModel):
    status: Literal["SETUP_PENDING", "INSTALLATION_IN_PROGRESS", "ALERT_TEST_PENDING", "PAPER_VERIFICATION_PENDING", "BLOCKED", "RETIRED"]
    reason: str | None = Field(default=None, max_length=1000)
    admin_notes: str | None = Field(default=None, max_length=2000)


class VerificationPayload(BaseModel):
    kind: Literal["HOLD", "PAPER_ENTRY", "PAPER_EXIT", "REVERSAL"]
    signal_id: str = Field(min_length=1, max_length=255)


class UserAcceptancePayload(BaseModel):
    original_version_id: UUID
    prompt_version_id: str = Field(min_length=1, max_length=40)
    setup_type: SetupType
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    reviewed_strategy: Literal[True]
    understands_static_validation: Literal[True]
    understands_performance_risk: Literal[True]
    accepts_paper_only: Literal[True]


EvidenceResult = Literal["PASS", "FAIL", "BLOCKED", "NOT_APPLICABLE"]


class QualificationEvidence(BaseModel):
    tradingview_compile_result: EvidenceResult
    static_validation_result: EvidenceResult
    hold_connectivity_result: EvidenceResult
    paper_entry_result: EvidenceResult
    paper_exit_result: EvidenceResult
    reversal_result: EvidenceResult
    duplicate_alert_result: EvidenceResult
    repainting_review_result: EvidenceResult
    market_state: str = Field(min_length=1, max_length=80)
    data_classification: Literal[
        "STATIC_VALIDATION_ONLY", "TRADINGVIEW_COMPILE_TEST",
        "REAL_TRADINGVIEW_ALERT_PAPER_TEST", "RECORDED_WEBHOOK_TEST",
        "SYNTHETIC_PAYLOAD_TEST", "BLOCKED",
    ]


class QualificationTrialPayload(BaseModel):
    prompt_version_id: str = Field(min_length=1, max_length=40)
    original_version_id: UUID
    candidate_version_id: UUID
    strategy_type: str = Field(min_length=1, max_length=80)
    test_classification: Literal[
        "STATIC_VALIDATION_ONLY", "TRADINGVIEW_COMPILE_TEST",
        "REAL_TRADINGVIEW_ALERT_PAPER_TEST", "RECORDED_WEBHOOK_TEST",
        "SYNTHETIC_PAYLOAD_TEST", "BLOCKED",
    ]
    evidence: QualificationEvidence
    outcome: Literal["PASS", "FAIL", "BLOCKED"]
    notes: str | None = Field(default=None, max_length=4000)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PromptDecisionPayload(BaseModel):
    change_notes: str = Field(min_length=3, max_length=2000)
