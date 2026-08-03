from __future__ import annotations

from pydantic import BaseModel, Field


class CreatePineStrategyPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1)
    filename: str = Field(default="strategy.pine", min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class CreatePineVersionPayload(BaseModel):
    source: str = Field(min_length=1)
    filename: str = Field(default="strategy.pine", min_length=1, max_length=120)
    changelog: str | None = Field(default=None, max_length=1000)


class ReviewNotePayload(BaseModel):
    note: str | None = Field(default=None, max_length=2000)
    acknowledge_warnings: bool = False


class LinkPineVersionPayload(BaseModel):
    strategy_id: str
    version_id: str


class UpdateStrategyMetadataPayload(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
