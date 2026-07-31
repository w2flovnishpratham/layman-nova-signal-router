from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preserve_visuals: bool = True
    preserve_input_names: bool = True
    preserve_alerts: bool = True
    prefer_bar_close: bool = True
    add_explanatory_comments: bool = False
    upgrade_v5_to_v6: bool = True
    instructions: str | None = Field(default=None, max_length=1000)


class CreateConversionPayload(BaseModel):
    consent: Literal[True]
    options: ConversionOptions = Field(default_factory=ConversionOptions)


class PineConversionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[1]
    converted_source: str = Field(min_length=1)
    conversion_summary: str = Field(min_length=1, max_length=2000)
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    unsupported_features: list[str] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    action_mapping: dict[str, str] = Field(default_factory=dict)


class RetryConversionPayload(BaseModel):
    consent: Literal[True]


class RejectConversionPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class AdminConversionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_setup_type: Literal["USER_MANAGED_TRADINGVIEW", "NOVA_MANAGED_TRADINGVIEW"] = "USER_MANAGED_TRADINGVIEW"
    intended_symbol: str = Field(default="NIFTY", min_length=1, max_length=30)
    intended_timeframe: str = Field(default="5", min_length=1, max_length=20)


class OwnerClaudeConversionPayload(BaseModel):
    """Explicit owner consent and deterministic C1 installation intent."""

    model_config = ConfigDict(extra="forbid")

    consent: Literal[True]
    options: AdminConversionOptions = Field(default_factory=AdminConversionOptions)


class AdminPineSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_name: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1)
    original_filename: str = Field(default="strategy.pine", min_length=1, max_length=120)
    internal_notes: str | None = Field(default=None, max_length=2000)
    options: AdminConversionOptions = Field(default_factory=AdminConversionOptions)


class ClaudeSignalMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buy_ce_source: str = Field(min_length=1, max_length=2000)
    buy_pe_source: str = Field(min_length=1, max_length=2000)
    exit_source: str = Field(min_length=1, max_length=2000)


class ClaudeBehaviorPreservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logic_changed: bool
    change_summary: list[str] = Field(default_factory=list, max_length=50)


class ClaudeCapabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handled: list[str] = Field(default_factory=list, max_length=100)
    unsupported: list[str] = Field(default_factory=list, max_length=100)
    manual_review: list[str] = Field(default_factory=list, max_length=100)


class ClaudePineConversionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["nova.claude-pine-conversion.v1"]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["CONVERTED", "MANUAL_REVIEW_REQUIRED", "BLOCKED"]
    strategy_layer: str = Field(min_length=1)
    # Admin-only TradingView backtest preview for INDICATOR-mode CONVERTED
    # candidates. Never wired to the frozen transport, never sent to NOVA's
    # execution path -- see prompt v4.1's BACKTEST LAYER section.
    backtest_layer: str | None = Field(default=None, max_length=200_000)
    signal_mapping: ClaudeSignalMapping
    behavior_preservation: ClaudeBehaviorPreservation
    capabilities: ClaudeCapabilityResult
    user_summary: str = Field(min_length=1, max_length=2000)
    admin_review_points: list[str] = Field(default_factory=list, max_length=100)


class AdminManualResponsePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_json: str = Field(min_length=2)


class PublishStrategyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_code: str = Field(min_length=2, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str | None = Field(default=None, max_length=160)


class AdminPineDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)
