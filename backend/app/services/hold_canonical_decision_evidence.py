"""Best-effort post-commit canonical decision evidence for fresh HOLD signals."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import select

from app.config import settings
from app.db import models
from app.db.engine import session_scope
from app.domain.legacy_signal_adapter import adapt_legacy_action
from app.services.canonical_signal_decision_persistence import (
    DECISION_WRITER_VERSION,
    persist_canonical_signal_decision,
)
from app.services.r1b_evidence_safety import CanonicalDecisionPersistenceError


logger = logging.getLogger("nova_signal_router.evidence")

_SAFE_FAILURE_CODES = frozenset(
    {
        "PERSISTENCE_DISABLED",
        "INVALID_INPUT",
        "OWNER_INSTANCE_MISMATCH",
        "FOREIGN_REFERENCE",
        "CANONICAL_DECISION_CONFLICT",
        "TAINT_DETECTED",
        "PERSISTENCE_UNAVAILABLE",
    }
)


def record_hold_decision_failure(code: str) -> None:
    """Emit only closed evidence diagnostics; logging failure is non-authoritative."""
    safe_code = code if code in _SAFE_FAILURE_CODES else "PERSISTENCE_UNAVAILABLE"
    try:
        logger.warning(
            "event=r1b_hold_decision_evidence_failed writer_version=%s error_code=%s",
            DECISION_WRITER_VERSION,
            safe_code,
        )
    except Exception:
        pass


def _closed_error(code: str) -> CanonicalDecisionPersistenceError:
    return CanonicalDecisionPersistenceError(code)


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise _closed_error("INVALID_INPUT")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _closed_error("INVALID_INPUT") from None
    if parsed.tzinfo is None:
        raise _closed_error("INVALID_INPUT")
    return parsed


def persist_hold_decision_best_effort(
    *,
    webhook_event_id: uuid.UUID | str | None,
    strategy_instance_id: uuid.UUID | str,
    owner_user_id: uuid.UUID | str,
) -> None:
    """Persist optional HOLD evidence from committed rows in an independent session."""
    if settings.R1B_CANONICAL_DECISION_PERSISTENCE is not True:
        return

    try:
        event_uuid = uuid.UUID(str(webhook_event_id))
        instance_uuid = uuid.UUID(str(strategy_instance_id))
        owner_uuid = uuid.UUID(str(owner_user_id))
        with session_scope() as db:
            webhook_event = db.get(models.WebhookEvent, event_uuid)
            strategy_instance = db.get(models.StrategyInstance, instance_uuid)
            if webhook_event is None or strategy_instance is None:
                raise _closed_error("FOREIGN_REFERENCE")
            if strategy_instance.user_id != owner_uuid or webhook_event.user_id != owner_uuid:
                raise _closed_error("OWNER_INSTANCE_MISMATCH")
            if webhook_event.provider != f"instance-webhook:{strategy_instance.id}":
                raise _closed_error("FOREIGN_REFERENCE")

            metadata = (
                webhook_event.event_metadata
                if isinstance(webhook_event.event_metadata, dict)
                else {}
            )
            if (
                metadata.get("action") != "HOLD"
                or metadata.get("strategy_instance_id") != str(strategy_instance.id)
            ):
                raise _closed_error("FOREIGN_REFERENCE")

            strategy_signal = db.scalar(
                select(models.StrategySignal).where(
                    models.StrategySignal.strategy_name
                    == f"instance:{strategy_instance.id}",
                    models.StrategySignal.signal_id == webhook_event.event_id,
                )
            )
            summary = (
                strategy_signal.result_summary
                if strategy_signal is not None
                and isinstance(strategy_signal.result_summary, dict)
                else {}
            )
            if (
                strategy_signal is None
                or strategy_signal.status != "completed"
                or summary.get("action") != "HOLD"
                or summary.get("reason") != "HOLD"
            ):
                raise _closed_error("FOREIGN_REFERENCE")

            canonical_event = adapt_legacy_action(
                "HOLD",
                signal_id=strategy_signal.signal_id,
                signal_time=_aware_datetime(metadata.get("signal_time")),
            )
            persist_canonical_signal_decision(
                db,
                strategy_signal=strategy_signal,
                strategy_instance=strategy_instance,
                owner_user_id=owner_uuid,
                canonical_event=canonical_event,
                payload_fingerprint=metadata.get("payload_fingerprint"),
                received_at=_aware_datetime(metadata.get("received_at")),
                webhook_event=webhook_event,
            )
    except CanonicalDecisionPersistenceError as exc:
        if exc.code != "PERSISTENCE_DISABLED":
            record_hold_decision_failure(exc.code)
    except Exception:
        record_hold_decision_failure("PERSISTENCE_UNAVAILABLE")
