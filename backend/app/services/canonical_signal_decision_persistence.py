"""Insert-only writer for canonical_signal_decisions (R1B-2A).

EVIDENCE ONLY — NOT EXECUTION AUTHORITY. Persists the immutable canonical
interpretation of one accepted StrategySignal, derived exclusively from
trusted, already-persisted server objects plus the immutable R1A
CanonicalSignalEvent.

R1B-2B boundary: the one production call path is fresh committed HOLD through
the post-commit hold_canonical_decision_evidence helper. Trading actions,
outcomes and rejections remain disconnected. This writer never authenticates,
validates replay, creates signals or jobs, routes, reads or writes JSON
position state, or affects an HTTP response.

Rows are never updated: a conflicting reinterpretation of the same
StrategySignal raises CANONICAL_DECISION_CONFLICT and leaves the original
row untouched.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.config import settings
from app.db import models
from app.domain.canonical_signal import (
    CanonicalEventType,
    CanonicalSignalEvent,
    DesiredPositionState,
)
from app.domain.legacy_signal_adapter import LEGACY_ADAPTER_VERSION
from app.domain.secret_taint import build_safe_metadata
from app.services.r1b_evidence_safety import (
    CanonicalDecisionPersistenceError,
    IntegrityError,
    SQLAlchemyError,
    require_aware_utc,
    require_closed,
    require_hex64,
    translate_db_error,
)

DECISION_WRITER_VERSION = "nova.canonical-decision-writer.v1"

# The 0015 schema's closed provenance values ("REALTIME" from the draft prompt
# does not exist in ck_canonical_decision_provenance_kind).
_PROVENANCE_KINDS = frozenset({"LIVE", "BACKFILL"})
_SOURCES = frozenset({"private_tradingview_webhook"})

# Closed inverse of legacy_signal_adapter._LEGACY_ACTIONS.
_WIRE_ACTION_BY_EVENT = {
    (CanonicalEventType.STRATEGY_SIGNAL, DesiredPositionState.BULLISH): "BUY_CE",
    (CanonicalEventType.STRATEGY_SIGNAL, DesiredPositionState.BEARISH): "BUY_PE",
    (CanonicalEventType.STRATEGY_SIGNAL, DesiredPositionState.FLAT): "EXIT",
    (CanonicalEventType.CONNECTIVITY_TEST, DesiredPositionState.NONE): "HOLD",
}
_COMPATIBILITY_BY_WIRE_ACTION = {
    "BUY_CE": ("ENTRY", "BUY", "CE"),
    "BUY_PE": ("ENTRY", "BUY", "PE"),
    "EXIT": ("EXIT", "SELL", None),
    "HOLD": (None, None, None),
}
# Fields that define one canonical interpretation; any difference on the same
# StrategySignal is a conflict, never an update.
_INTERPRETATION_FIELDS = (
    "wire_action",
    "event_type",
    "desired_state",
    "intent_reason",
    "compatibility_action",
    "compatibility_side",
    "compatibility_option_side",
    "contract_version",
    "adapter_version",
    "payload_fingerprint",
    "provenance_kind",
)


def _error(code: str) -> CanonicalDecisionPersistenceError:
    return CanonicalDecisionPersistenceError(code)


def _interpretation(row: models.CanonicalSignalDecision) -> tuple:
    return tuple(getattr(row, field) for field in _INTERPRETATION_FIELDS)


def persist_canonical_signal_decision(
    session,
    *,
    strategy_signal: models.StrategySignal,
    strategy_instance: models.StrategyInstance,
    owner_user_id: uuid.UUID,
    canonical_event: CanonicalSignalEvent,
    payload_fingerprint: str,
    received_at,
    webhook_event: models.WebhookEvent | None = None,
    adapter_version: str = LEGACY_ADAPTER_VERSION,
    source: str = "private_tradingview_webhook",
    provenance_kind: str = "LIVE",
) -> models.CanonicalSignalDecision:
    """Insert or return the one immutable decision for this StrategySignal."""
    if not settings.R1B_CANONICAL_DECISION_PERSISTENCE:
        raise _error("PERSISTENCE_DISABLED")

    # ---- trusted-input validation (no DB access) --------------------------
    if not isinstance(canonical_event, CanonicalSignalEvent):
        raise _error("INVALID_INPUT")
    if not isinstance(strategy_signal, models.StrategySignal) or strategy_signal.id is None:
        raise _error("INVALID_INPUT")
    if not isinstance(strategy_instance, models.StrategyInstance) or strategy_instance.id is None:
        raise _error("INVALID_INPUT")
    if adapter_version != LEGACY_ADAPTER_VERSION:
        raise _error("INVALID_INPUT")
    require_closed(provenance_kind, _PROVENANCE_KINDS, _error("INVALID_INPUT"))
    require_closed(source, _SOURCES, _error("INVALID_INPUT"))
    require_hex64(payload_fingerprint, _error("INVALID_INPUT"))
    received_at_utc = require_aware_utc(received_at, _error("INVALID_INPUT"))

    if strategy_instance.user_id != owner_user_id:
        raise _error("OWNER_INSTANCE_MISMATCH")
    # The signal must belong to this instance (instance-scoped strategy name)
    # and to this canonical event.
    if strategy_signal.strategy_name != f"instance:{strategy_instance.id}":
        raise _error("FOREIGN_REFERENCE")
    if strategy_signal.signal_id != canonical_event.signal_id:
        raise _error("INVALID_INPUT")
    if webhook_event is not None:
        if not isinstance(webhook_event, models.WebhookEvent) or webhook_event.id is None:
            raise _error("INVALID_INPUT")
        if (
            webhook_event.provider != f"instance-webhook:{strategy_instance.id}"
            or webhook_event.event_id != canonical_event.signal_id
        ):
            raise _error("FOREIGN_REFERENCE")

    key = (canonical_event.event_type, canonical_event.desired_state)
    wire_action = _WIRE_ACTION_BY_EVENT.get(key)
    if wire_action is None:
        raise _error("INVALID_INPUT")
    compatibility_action, compatibility_side, compatibility_option_side = (
        _COMPATIBILITY_BY_WIRE_ACTION[wire_action]
    )

    # Closed audit metadata only — the R1B-0 builder drops everything else,
    # including credential-shaped or oversized values.
    safe_metadata = dict(
        build_safe_metadata(
            {
                "strategy_version": canonical_event.strategy_version,
                "timeframe": canonical_event.timeframe,
                "reference_price": canonical_event.metadata.get("reference_price"),
            }
        )
    ) or None

    row_values = dict(
        strategy_signal_id=strategy_signal.id,
        webhook_event_id=webhook_event.id if webhook_event is not None else None,
        user_id=owner_user_id,
        strategy_instance_id=strategy_instance.id,
        contract_version=canonical_event.contract_version.value,
        adapter_version=adapter_version,
        wire_action=wire_action,
        event_type=canonical_event.event_type.value,
        desired_state=canonical_event.desired_state.value,
        intent_reason=canonical_event.intent_reason.value,
        signal_time=canonical_event.signal_time,
        received_at=received_at_utc,
        compatibility_action=compatibility_action,
        compatibility_side=compatibility_side,
        compatibility_option_side=compatibility_option_side,
        source=source,
        safe_metadata=safe_metadata,
        payload_fingerprint=payload_fingerprint,
        provenance_kind=provenance_kind,
    )
    candidate = models.CanonicalSignalDecision(**row_values)

    # ---- closed database envelope ----------------------------------------
    try:
        existing = session.scalar(
            select(models.CanonicalSignalDecision).where(
                models.CanonicalSignalDecision.strategy_signal_id == strategy_signal.id
            )
        )
        if existing is not None:
            if _interpretation(existing) == _interpretation(candidate):
                return existing
            raise _error("CANONICAL_DECISION_CONFLICT")
        try:
            with session.begin_nested():
                session.add(candidate)
        except IntegrityError:
            # Concurrent identical insert lost the unique race — or an
            # unrelated integrity failure, which must not masquerade as
            # idempotency.
            existing = session.scalar(
                select(models.CanonicalSignalDecision).where(
                    models.CanonicalSignalDecision.strategy_signal_id == strategy_signal.id
                )
            )
            if existing is None:
                raise _error("PERSISTENCE_UNAVAILABLE") from None
            if _interpretation(existing) == _interpretation(candidate):
                return existing
            raise _error("CANONICAL_DECISION_CONFLICT") from None
        return candidate
    except CanonicalDecisionPersistenceError:
        raise
    except SQLAlchemyError as exc:
        raise translate_db_error(exc, _error("PERSISTENCE_UNAVAILABLE")) from None
