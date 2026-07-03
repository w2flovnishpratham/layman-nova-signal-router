"""Phase 2C-0 queue contract helpers for v2 strategy execution jobs."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import models
from app.services.strategy_fanout_v2 import FanoutPlanResult


V2_PENDING = "v2_pending"
V2_LOCKED = "v2_locked"
V2_COMPLETED_DRY_RUN = "v2_completed_dry_run"
V2_FAILED_DRY_RUN = "v2_failed_dry_run"
V2_DEAD_LETTER = "v2_dead_letter"

MISSING_PERSISTED_SIGNAL = "MISSING_PERSISTED_SIGNAL"
MISSING_NORMALIZED_SIGNAL = "MISSING_NORMALIZED_SIGNAL"
LEGACY_UNIQUE_CONFLICT = "LEGACY_UNIQUE_CONFLICT"


@dataclass
class V2JobRef:
    job_id: uuid.UUID
    user_id: uuid.UUID
    instance_id: uuid.UUID
    strategy_version_id: uuid.UUID | None
    normalized_signal_id: uuid.UUID
    execution_mode: str
    status: str
    signal_id: str

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class V2JobIssue:
    reason: str
    user_id: uuid.UUID | None = None
    instance_id: uuid.UUID | None = None
    normalized_signal_id: uuid.UUID | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class JobCreationResult:
    ok: bool
    strategy_code: str
    signal_id: str | None
    strategy_signal_id: uuid.UUID | None
    requested_count: int
    created_count: int
    duplicate_count: int
    skipped_count: int
    error_count: int
    jobs: list[V2JobRef] = field(default_factory=list)
    duplicates: list[V2JobIssue] = field(default_factory=list)
    skips: list[V2JobIssue] = field(default_factory=list)
    errors: list[V2JobIssue] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def create_v2_jobs_from_plan(db: Any, plan_result: FanoutPlanResult) -> JobCreationResult:
    """Create non-executable v2 queue rows from persisted fanout plans."""
    if plan_result.signal_row_id is None:
        return _creation_error(
            plan_result,
            MISSING_PERSISTED_SIGNAL,
            "Fanout plan must be persisted before v2 jobs can be created.",
        )

    strategy_signal = db.get(models.StrategySignal, plan_result.signal_row_id)
    if strategy_signal is None:
        return _creation_error(
            plan_result,
            MISSING_PERSISTED_SIGNAL,
            "Persisted strategy signal row was not found.",
        )

    jobs: list[V2JobRef] = []
    duplicates: list[V2JobIssue] = []
    skips: list[V2JobIssue] = [
        V2JobIssue(reason=skip.reason, user_id=skip.user_id, instance_id=skip.instance_id)
        for skip in plan_result.skips
    ]
    errors: list[V2JobIssue] = [
        V2JobIssue(reason=error.error_code, user_id=error.user_id, instance_id=error.instance_id)
        for error in plan_result.errors
    ]

    for plan in plan_result.plans:
        if plan.normalized_signal_id is None:
            errors.append(
                V2JobIssue(
                    reason=MISSING_NORMALIZED_SIGNAL,
                    user_id=plan.user_id,
                    instance_id=plan.instance_id,
                    details={"message": "Plan does not reference a persisted normalized signal."},
                )
            )
            continue

        existing = _find_existing_v2_job(
            db,
            strategy_signal_id=strategy_signal.id,
            instance_id=plan.instance_id,
            normalized_signal_id=plan.normalized_signal_id,
        )
        if existing is not None:
            duplicates.append(_issue_from_job(existing, reason="DUPLICATE_V2_JOB"))
            continue

        job_signal_id = _v2_job_signal_id(
            source_signal_id=strategy_signal.signal_id,
            instance_id=plan.instance_id,
            normalized_signal_id=plan.normalized_signal_id,
        )
        conflicting_job = _find_job_by_legacy_unique_key(
            db,
            strategy_name=plan_result.strategy_code,
            signal_id=job_signal_id,
            user_id=plan.user_id,
        )
        if conflicting_job is not None:
            errors.append(_issue_from_job(conflicting_job, reason=LEGACY_UNIQUE_CONFLICT))
            continue

        job = models.StrategyExecutionJob(
            strategy_signal_id=strategy_signal.id,
            instance_id=plan.instance_id,
            strategy_version_id=plan_result.strategy_version_id,
            normalized_signal_id=plan.normalized_signal_id,
            user_id=plan.user_id,
            strategy_name=plan_result.strategy_code,
            signal_id=job_signal_id,
            signal_payload=_job_payload(plan_result, plan, job_signal_id),
            lots=plan.normalized_draft.lots,
            execution_mode=plan.execution_mode,
            status=V2_PENDING,
            max_attempts=1,
        )
        db.add(job)
        db.flush()
        jobs.append(_job_ref(job))

    return JobCreationResult(
        ok=not errors,
        strategy_code=plan_result.strategy_code,
        signal_id=plan_result.signal_id,
        strategy_signal_id=strategy_signal.id,
        requested_count=len(plan_result.plans),
        created_count=len(jobs),
        duplicate_count=len(duplicates),
        skipped_count=len(skips),
        error_count=len(errors),
        jobs=jobs,
        duplicates=duplicates,
        skips=skips,
        errors=errors,
    )


def claim_v2_job_for_test_or_future_worker(
    db: Any,
    worker_id: str,
) -> models.StrategyExecutionJob | None:
    """Claim the oldest v2 queue row without executing it."""
    now = _now()
    statement = (
        select(models.StrategyExecutionJob)
        .where(models.StrategyExecutionJob.status == V2_PENDING)
        .order_by(
            models.StrategyExecutionJob.created_at.asc(),
            models.StrategyExecutionJob.id.asc(),
        )
    )
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)

    candidates = list(db.scalars(statement).all())
    for job in candidates:
        if _has_locked_v2_job_for_instance(db, user_id=job.user_id, instance_id=job.instance_id):
            continue
        job.status = V2_LOCKED
        job.locked_at = now
        job.locked_by = worker_id
        job.attempts += 1
        job.updated_at = now
        db.flush()
        return job
    return None


def mark_v2_job_completed_dry_run(
    db: Any,
    job_id: uuid.UUID | str,
    details: dict[str, Any] | None = None,
) -> models.StrategyExecutionJob | None:
    job = db.get(models.StrategyExecutionJob, _coerce_uuid(job_id))
    if job is None:
        return None
    now = _now()
    job.status = V2_COMPLETED_DRY_RUN
    job.completed_at = now
    job.result_summary = {
        "status": V2_COMPLETED_DRY_RUN,
        "details": _jsonable(details or {}),
    }
    job.updated_at = now
    db.flush()
    return job


def mark_v2_job_failed_dry_run(
    db: Any,
    job_id: uuid.UUID | str,
    reason: str,
    category: str | None = None,
) -> models.StrategyExecutionJob | None:
    job = db.get(models.StrategyExecutionJob, _coerce_uuid(job_id))
    if job is None:
        return None
    now = _now()
    job.status = V2_FAILED_DRY_RUN
    job.completed_at = now
    job.last_error = reason
    job.dead_letter_reason = category
    job.result_summary = {
        "status": V2_FAILED_DRY_RUN,
        "reason": reason,
        "category": category,
    }
    job.updated_at = now
    db.flush()
    return job


def _creation_error(
    plan_result: FanoutPlanResult,
    reason: str,
    message: str,
) -> JobCreationResult:
    return JobCreationResult(
        ok=False,
        strategy_code=plan_result.strategy_code,
        signal_id=plan_result.signal_id,
        strategy_signal_id=plan_result.signal_row_id,
        requested_count=len(plan_result.plans),
        created_count=0,
        duplicate_count=0,
        skipped_count=len(plan_result.skips),
        error_count=1,
        errors=[V2JobIssue(reason=reason, details={"message": message})],
    )


def _find_existing_v2_job(
    db: Any,
    *,
    strategy_signal_id: uuid.UUID,
    instance_id: uuid.UUID,
    normalized_signal_id: uuid.UUID,
) -> models.StrategyExecutionJob | None:
    return db.scalar(
        select(models.StrategyExecutionJob).where(
            models.StrategyExecutionJob.strategy_signal_id == strategy_signal_id,
            models.StrategyExecutionJob.instance_id == instance_id,
            models.StrategyExecutionJob.normalized_signal_id == normalized_signal_id,
        )
    )


def _find_job_by_legacy_unique_key(
    db: Any,
    *,
    strategy_name: str,
    signal_id: str,
    user_id: uuid.UUID,
) -> models.StrategyExecutionJob | None:
    return db.scalar(
        select(models.StrategyExecutionJob).where(
            models.StrategyExecutionJob.strategy_name == strategy_name,
            models.StrategyExecutionJob.signal_id == signal_id,
            models.StrategyExecutionJob.user_id == user_id,
        )
    )


def _has_locked_v2_job_for_instance(
    db: Any,
    *,
    user_id: uuid.UUID,
    instance_id: uuid.UUID | None,
) -> bool:
    if instance_id is None:
        return False
    row = db.scalar(
        select(models.StrategyExecutionJob.id).where(
            models.StrategyExecutionJob.status == V2_LOCKED,
            models.StrategyExecutionJob.user_id == user_id,
            models.StrategyExecutionJob.instance_id == instance_id,
        )
    )
    return row is not None


def _job_payload(plan_result: FanoutPlanResult, plan: Any, job_signal_id: str) -> dict[str, Any]:
    return {
        "payload_format": "NOVA_V2_EXECUTION_JOB",
        "planner": "strategy_fanout_v2",
        "queue_contract": "phase2c_0",
        "strategy_code": plan_result.strategy_code,
        "source_signal_id": plan_result.signal_id,
        "job_signal_id": job_signal_id,
        "strategy_signal_id": str(plan_result.signal_row_id),
        "strategy_version_id": str(plan_result.strategy_version_id) if plan_result.strategy_version_id else None,
        "instance_id": str(plan.instance_id),
        "normalized_signal_id": str(plan.normalized_signal_id),
        "execution_mode": plan.execution_mode,
        "normalized_draft": _jsonable(plan.normalized_draft),
        "needs_resolution": bool(plan.needs_resolution),
    }


def _job_ref(job: models.StrategyExecutionJob) -> V2JobRef:
    if job.instance_id is None or job.normalized_signal_id is None:
        raise ValueError("V2 job rows must include instance_id and normalized_signal_id.")
    return V2JobRef(
        job_id=job.id,
        user_id=job.user_id,
        instance_id=job.instance_id,
        strategy_version_id=job.strategy_version_id,
        normalized_signal_id=job.normalized_signal_id,
        execution_mode=job.execution_mode,
        status=job.status,
        signal_id=job.signal_id,
    )


def _issue_from_job(job: models.StrategyExecutionJob, *, reason: str) -> V2JobIssue:
    return V2JobIssue(
        reason=reason,
        user_id=job.user_id,
        instance_id=job.instance_id,
        normalized_signal_id=job.normalized_signal_id,
        details={"job_id": str(job.id), "status": job.status},
    )


def _v2_job_signal_id(
    *,
    source_signal_id: str,
    instance_id: uuid.UUID,
    normalized_signal_id: uuid.UUID,
) -> str:
    digest = hashlib.sha256(
        f"{source_signal_id}:{instance_id}:{normalized_signal_id}".encode("utf-8")
    ).hexdigest()[:20]
    prefix = str(source_signal_id or "signal")[:220]
    return f"{prefix}:v2:{digest}"


def _coerce_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
