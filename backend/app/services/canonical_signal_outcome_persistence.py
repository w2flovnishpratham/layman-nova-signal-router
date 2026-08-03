"""Insert-only writer for canonical_signal_outcomes (R1B-2A).

EVIDENCE ONLY — NOT EXECUTION AUTHORITY. Persists one append-only routing or
state observation for an existing canonical decision, using only closed typed
inputs drawn from the 0015 CHECK vocabulary. This library never translates
arbitrary router dictionaries, never calls execution, never mutates
positions, and never retries a broker action — a (future) caller observes an
already-durable authoritative result and hands the writer closed values.

R1B-2A boundary: ZERO production call sites. Rows are never updated; each
distinct supported observation is a distinct row keyed by a deterministic
internal idempotency hash.
"""
from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.db import models
from app.services.r1b_evidence_safety import (
    CanonicalOutcomePersistenceError,
    IntegrityError,
    SQLAlchemyError,
    evidence_key,
    require_closed,
    translate_db_error,
)

OUTCOME_WRITER_VERSION = "nova.canonical-outcome-writer.v1"
OUTCOME_KEY_NAMESPACE = "nova.r1b2.outcome.v1"

# Exactly the 0015 CHECK vocabulary — nothing invented.
OUTCOME_PHASES = frozenset({"STATE_EVALUATED", "ROUTING_COMPLETED", "ROUTING_FAILED"})
OUTCOME_CURRENT_STATES = frozenset({"UNKNOWN", "FLAT", "BULLISH", "BEARISH"})
OUTCOME_ROUTING_RESULTS = frozenset(
    {
        "NOT_EVALUATED",
        "CONNECTIVITY_NO_JOB",
        "STATE_NO_OP",
        "ENTRY_ROUTED",
        "EXIT_ROUTED",
        "REVERSAL_ROUTED",
        "BLOCKED",
        "FAILED",
        "PENDING",
        "PARTIAL",
    }
)
OUTCOME_NO_OP_REASONS = frozenset(
    {"CONNECTIVITY_TEST", "ALREADY_FLAT", "ALREADY_BULLISH", "ALREADY_BEARISH", "INSTANCE_PAUSED"}
)
OUTCOME_EXIT_REASONS = frozenset(
    {
        "EXPLICIT_EXIT",
        "REVERSAL_EXIT",
        "STOP_LOSS",
        "TAKE_PROFIT",
        "TRAILING_STOP",
        "SESSION_EXIT",
        "MANUAL_EXIT",
        "RISK_EXIT",
    }
)
# Closed writer-enforced detail vocabulary (approved design §9). The column
# carries finer granularity than routing_result without any schema change.
OUTCOME_SAFE_DETAIL_CODES = frozenset(
    {
        "HOLD",
        "ALREADY_FLAT",
        "ALREADY_IN_CE",
        "ALREADY_IN_PE",
        "PAUSED_ENTRY_BLOCKED",
        "VERIFICATION_PAPER_ONLY",
        "ENTRY_SUBMITTED",
        "ENTRY_TRADED",
        "ENTRY_PENDING",
        "ENTRY_REJECTED",
        "EXIT_SUBMITTED",
        "EXIT_TRADED",
        "EXIT_PENDING",
        "EXIT_REJECTED",
        "REVERSAL_EXIT_STARTED",
        "REVERSAL_EXIT_FAILED",
        "REVERSAL_ENTRY_STARTED",
        "REVERSAL_ENTRY_FAILED",
        "REVERSAL_COMPLETED",
        "PARTIAL_FILL_REMAINS_OPEN",
        "BROKER_STATE_UNKNOWN",
        "CONTRACT_RESOLUTION_FAILED",
        "LIVE_EXECUTION_DISABLED",
        "ENTITLEMENT_BLOCKED",
        "RISK_BLOCKED",
        "WORKER_FAILED",
        "RETRY_SUPPRESSED",
        "CONNECTIVITY_NO_JOB",
    }
)


def _error(code: str) -> CanonicalOutcomePersistenceError:
    return CanonicalOutcomePersistenceError(code)


def outcome_idempotency_key(
    *,
    decision_id,
    phase: str,
    execution_job_id,
    attempt,
    routing_result: str,
    safe_detail,
) -> str:
    """Deterministic internal key: stable across retries of the same
    observation; distinct per phase, per worker attempt, per reversal step.
    Inputs are server enums and UUIDs only — never credentials, source or
    broker data."""
    return evidence_key(
        OUTCOME_KEY_NAMESPACE,
        (decision_id, phase, execution_job_id, attempt, routing_result, safe_detail),
    )


def persist_canonical_signal_outcome(
    session,
    *,
    decision: models.CanonicalSignalDecision,
    phase: str,
    routing_result: str,
    current_state: str | None = None,
    no_op_reason: str | None = None,
    exit_reason: str | None = None,
    execution_job: models.StrategyExecutionJob | None = None,
    attempt: int | None = None,
    safe_detail: str | None = None,
) -> models.CanonicalSignalOutcome:
    """Insert or return one immutable outcome observation."""
    if not settings.R1B_CANONICAL_OUTCOME_PERSISTENCE:
        raise _error("PERSISTENCE_DISABLED")

    # ---- closed typed input validation (no DB access) ---------------------
    if not isinstance(decision, models.CanonicalSignalDecision) or decision.id is None:
        raise _error("INVALID_INPUT")
    require_closed(phase, OUTCOME_PHASES, _error("INVALID_INPUT"))
    require_closed(routing_result, OUTCOME_ROUTING_RESULTS, _error("INVALID_INPUT"))
    if current_state is not None:
        require_closed(current_state, OUTCOME_CURRENT_STATES, _error("INVALID_INPUT"))
    if no_op_reason is not None:
        require_closed(no_op_reason, OUTCOME_NO_OP_REASONS, _error("INVALID_INPUT"))
    if exit_reason is not None:
        require_closed(exit_reason, OUTCOME_EXIT_REASONS, _error("INVALID_INPUT"))
    if safe_detail is not None:
        require_closed(safe_detail, OUTCOME_SAFE_DETAIL_CODES, _error("INVALID_INPUT"))
    if attempt is not None and (not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1):
        raise _error("INVALID_INPUT")
    if execution_job is not None:
        if not isinstance(execution_job, models.StrategyExecutionJob) or execution_job.id is None:
            raise _error("INVALID_INPUT")
        # The job must belong to the same accepted signal as the decision.
        if execution_job.strategy_signal_id != decision.strategy_signal_id:
            raise _error("FOREIGN_REFERENCE")

    execution_job_id = execution_job.id if execution_job is not None else None
    idempotency_key = outcome_idempotency_key(
        decision_id=decision.id,
        phase=phase,
        execution_job_id=execution_job_id,
        attempt=attempt,
        routing_result=routing_result,
        safe_detail=safe_detail,
    )
    candidate = models.CanonicalSignalOutcome(
        canonical_decision_id=decision.id,
        execution_job_id=execution_job_id,
        attempt=attempt,
        phase=phase,
        current_state=current_state,
        routing_result=routing_result,
        no_op_reason=no_op_reason,
        exit_reason=exit_reason,
        safe_detail_code=safe_detail,
        result_sha256=None,  # no raw result material is persisted in R1B-2A
        idempotency_key=idempotency_key,
    )

    # ---- closed database envelope ----------------------------------------
    try:
        existing = session.scalar(
            select(models.CanonicalSignalOutcome).where(
                models.CanonicalSignalOutcome.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            return existing
        try:
            with session.begin_nested():
                session.add(candidate)
        except IntegrityError:
            existing = session.scalar(
                select(models.CanonicalSignalOutcome).where(
                    models.CanonicalSignalOutcome.idempotency_key == idempotency_key
                )
            )
            if existing is None:
                # Unrelated integrity failure (bad FK, CHECK) — never treated
                # as idempotent success.
                raise _error("PERSISTENCE_UNAVAILABLE") from None
            return existing
        return candidate
    except CanonicalOutcomePersistenceError:
        raise
    except SQLAlchemyError as exc:
        raise translate_db_error(exc, _error("PERSISTENCE_UNAVAILABLE")) from None
