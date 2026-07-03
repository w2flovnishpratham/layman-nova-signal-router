from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.db import models
from app.schemas.nova_signal_v1 import NovaSignalV1
from app.services.option_intent_mapper import OptionIntentMapper


NORMALIZATION_FAILED = "NORMALIZATION_FAILED"


@dataclass(frozen=True)
class NormalizedOptionSignalDraft:
    action: str
    intent: str
    symbol: str
    instrument_type: str
    option_side: str
    strike_mode: str
    resolved_strike: float | None
    expiry_mode: str
    resolved_expiry: date | None
    qty_mode: str
    lots: int
    quantity: int | None
    order_type: str
    product_type: str
    strategy_code: str
    strategy_version_id: uuid.UUID | None = None
    instance_id: uuid.UUID | None = None
    needs_resolution: bool = False
    resolution_reasons: list[str] = field(default_factory=list)
    raw_mapping_details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalNormalizationResult:
    ok: bool
    draft: NormalizedOptionSignalDraft | None = None
    error_code: str | None = None
    user_message: str | None = None


class SignalNormalizationService:
    def __init__(self, option_mapper: OptionIntentMapper | None = None) -> None:
        self.option_mapper = option_mapper or OptionIntentMapper()

    def normalize(
        self,
        payload: NovaSignalV1,
        *,
        instance: models.UserStrategyInstance | None = None,
    ) -> SignalNormalizationResult:
        mapped = self.option_mapper.map_payload(payload, instance=instance)
        if not mapped.ok:
            return SignalNormalizationResult(
                ok=False,
                error_code=mapped.error_code or NORMALIZATION_FAILED,
                user_message=mapped.user_message or "The signal could not be normalized.",
            )

        resolution_reasons: list[str] = []
        resolved_strike = _resolve_strike(payload, resolution_reasons)
        resolved_expiry = _resolve_expiry(payload, resolution_reasons)
        instance_id = getattr(instance, "id", None)
        strategy_version_id = getattr(instance, "strategy_version_id", None)

        draft = NormalizedOptionSignalDraft(
            action=payload.action,
            intent=payload.intent,
            symbol=payload.symbol,
            instrument_type=payload.instrument_type,
            option_side=mapped.option_side or "NONE",
            strike_mode=payload.strike_mode,
            resolved_strike=resolved_strike,
            expiry_mode=payload.expiry_mode,
            resolved_expiry=resolved_expiry,
            qty_mode=payload.qty_mode,
            lots=payload.lots,
            quantity=None,
            order_type=payload.order_type,
            product_type=payload.product_type,
            strategy_code=payload.strategy_code,
            strategy_version_id=strategy_version_id,
            instance_id=instance_id,
            needs_resolution=bool(resolution_reasons),
            resolution_reasons=resolution_reasons,
            raw_mapping_details={
                "payload_version": payload.version,
                "source": payload.source,
                "signal_id": payload.signal_id,
                "option_side_source": "intent_mapper",
                "timestamp": payload.timestamp.isoformat(),
                "needs_resolution": bool(resolution_reasons),
                "resolution_reasons": list(resolution_reasons),
            },
        )
        return SignalNormalizationResult(ok=True, draft=draft)


def create_normalized_option_signal(
    db: Any,
    *,
    strategy_signal_id: uuid.UUID,
    draft: NormalizedOptionSignalDraft,
) -> models.NormalizedOptionSignal:
    row = models.NormalizedOptionSignal(
        strategy_signal_id=strategy_signal_id,
        instance_id=draft.instance_id,
        action=draft.action,
        intent=draft.intent,
        symbol=draft.symbol,
        instrument_type=draft.instrument_type,
        option_side=draft.option_side,
        strike_mode=draft.strike_mode,
        resolved_strike=draft.resolved_strike,
        expiry_mode=draft.expiry_mode,
        resolved_expiry=draft.resolved_expiry,
        qty_mode=draft.qty_mode,
        lots=draft.lots,
        quantity=draft.quantity,
        order_type=draft.order_type,
        product_type=draft.product_type,
        raw_mapping_details=draft.raw_mapping_details,
    )
    db.add(row)
    db.flush()
    return row


def _resolve_strike(payload: NovaSignalV1, resolution_reasons: list[str]) -> float | None:
    if payload.strike_mode == "MANUAL":
        return payload.strike
    resolution_reasons.append(f"strike_mode {payload.strike_mode} needs market-data resolution")
    return None


def _resolve_expiry(payload: NovaSignalV1, resolution_reasons: list[str]) -> date | None:
    if payload.expiry_mode == "MANUAL":
        return payload.expiry
    resolution_reasons.append(f"expiry_mode {payload.expiry_mode} needs contract-calendar resolution")
    return None
