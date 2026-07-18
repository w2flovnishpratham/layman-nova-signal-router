"""Best-effort post-commit canonical decision evidence for fresh trading actions.

EVIDENCE ONLY — NOT EXECUTION AUTHORITY. Persists the immutable canonical
intent decision for one freshly accepted and committed BUY_CE, BUY_PE or EXIT
signal, strictly after the authoritative StrategySignal + StrategyExecutionJob
transaction has committed. The reviewed HOLD helper
(hold_canonical_decision_evidence.py) is deliberately left untouched; this is
its sibling for trading actions.

Worker-race rule (R1B-2B2 design §8): the durable job worker is woken before
ingress regains control, so the signal and job may already be claimed,
completed, failed or no-op'ed when this helper reloads them. This helper
therefore validates only immutable identity and accepted-intent facts —
NEVER transient signal/job status, attempt counts or execution results. A
decision records accepted intent, not execution outcome.

All failures are suppressed into one sanitized closed-code diagnostic; nothing
here can alter the webhook response, replay identity, the committed signal or
job, worker scheduling, routing or position state.
"""
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

TRADING_DECISION_ACTIONS = frozenset({"BUY_CE", "BUY_PE", "EXIT"})
_ACTION_CLASS = {"BUY_CE": "ENTRY", "BUY_PE": "ENTRY", "EXIT": "EXIT"}
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


def record_trading_decision_failure(code: str, action_class: str | None = None) -> None:
    """Emit only closed evidence diagnostics; logging failure is non-authoritative."""
    safe_code = code if code in _SAFE_FAILURE_CODES else "PERSISTENCE_UNAVAILABLE"
    safe_class = action_class if action_class in ("ENTRY", "EXIT") else None
    try:
        if safe_class is not None:
            logger.warning(
                "event=r1b_trading_decision_evidence_failed writer_version=%s "
                "action_class=%s error_code=%s",
                DECISION_WRITER_VERSION,
                safe_class,
                safe_code,
            )
        else:
            logger.warning(
                "event=r1b_trading_decision_evidence_failed writer_version=%s "
                "error_code=%s",
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


def persist_trading_decision_best_effort(
    *,
    webhook_event_id: uuid.UUID | str | None,
    strategy_instance_id: uuid.UUID | str,
    owner_user_id: uuid.UUID | str,
) -> None:
    """Persist optional trading-intent evidence from committed rows only.

    The committed signal and execution job are reloaded by their committed
    unique keys (instance-scoped strategy name + event id) rather than passed
    in: the authoritative response body is the return value of
    _persist_execution_job and must not grow identifier fields, and the
    lookups bind to exactly the same unique constraints that made the commit
    authoritative.
    """
    # Gate 1 of the dual fail-closed contract. The writer independently
    # evaluates the same flag immediately before persistence.
    enabled = settings.R1B_CANONICAL_DECISION_PERSISTENCE is True
    if not enabled:
        return

    action_class: str | None = None
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
            action = metadata.get("action")
            if (
                action not in TRADING_DECISION_ACTIONS
                or metadata.get("strategy_instance_id") != str(strategy_instance.id)
            ):
                raise _closed_error("FOREIGN_REFERENCE")
            action_class = _ACTION_CLASS[action]

            strategy_name = f"instance:{strategy_instance.id}"
            strategy_signal = db.scalar(
                select(models.StrategySignal).where(
                    models.StrategySignal.strategy_name == strategy_name,
                    models.StrategySignal.signal_id == webhook_event.event_id,
                )
            )
            # The worker may already have advanced signal.status and replaced
            # result_summary. Neither mutable field is evidence authority.
            if strategy_signal is None:
                raise _closed_error("FOREIGN_REFERENCE")

            execution_job = db.scalar(
                select(models.StrategyExecutionJob).where(
                    models.StrategyExecutionJob.strategy_signal_id
                    == strategy_signal.id,
                    models.StrategyExecutionJob.strategy_name == strategy_name,
                    models.StrategyExecutionJob.signal_id == webhook_event.event_id,
                    models.StrategyExecutionJob.user_id == owner_uuid,
                )
            )
            # Proof of acceptance: exactly one committed job per accepted
            # trading signal. Job status/attempts are deliberately ignored.
            if execution_job is None:
                raise _closed_error("FOREIGN_REFERENCE")

            # The job payload is the committed immutable ingress snapshot.
            # Workers update status/results, but never rewrite signal_payload.
            signal_payload = (
                execution_job.signal_payload
                if isinstance(execution_job.signal_payload, dict)
                else {}
            )
            raw_payload = (
                signal_payload.get("raw_payload")
                if isinstance(signal_payload.get("raw_payload"), dict)
                else {}
            )
            signal_action = raw_payload.get("normalized_action")
            if (
                signal_action not in TRADING_DECISION_ACTIONS
                or signal_action != action
                or signal_payload.get("strategy_code") != strategy_name
                or raw_payload.get("private_instance_webhook") is not True
                or raw_payload.get("strategy_instance_id")
                != str(strategy_instance.id)
                or raw_payload.get("signal_id") != webhook_event.event_id
            ):
                raise _closed_error("FOREIGN_REFERENCE")

            canonical_event = adapt_legacy_action(
                action,
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
        record_trading_decision_failure(exc.code, action_class)
    except Exception:
        record_trading_decision_failure("PERSISTENCE_UNAVAILABLE", action_class)
