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
