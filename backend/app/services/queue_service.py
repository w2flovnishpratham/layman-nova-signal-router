from __future__ import annotations

from datetime import timedelta
import re
from typing import Any

from sqlalchemy import case, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.auth.db import engine, session_scope
from app.auth.models import WorkerJob, utc_now_dt
from app.config import settings


JOB_STRATEGY_SIGNAL_FANOUT = "strategy_signal_fanout"
JOB_PAPER_SIGNAL_EXECUTION = "paper_signal_execution"
JOB_USER_NOTIFICATION = "user_notification"
JOB_LIVE_ORDER_EXECUTION = "live_order_execution"

JOB_PAYLOAD_SCHEMAS: dict[str, str] = {
    JOB_STRATEGY_SIGNAL_FANOUT: "strategy_signal_id",
    JOB_PAPER_SIGNAL_EXECUTION: "user_signal_job_id",
    JOB_USER_NOTIFICATION: "user_notification_id",
    JOB_LIVE_ORDER_EXECUTION: "live_order_job_id",
}


class WorkerQueueError(ValueError):
    pass


def enqueue_job(
    *,
    job_type: str,
    payload: dict[str, Any],
    dedupe_key: str,
    priority: int = 100,
    max_attempts: int = 3,
    created_by_user_id: str | None = None,
) -> WorkerJob:
    with session_scope() as session:
        record = enqueue_job_in_session(
            session,
            job_type=job_type,
            payload=payload,
            dedupe_key=dedupe_key,
            priority=priority,
            max_attempts=max_attempts,
            created_by_user_id=created_by_user_id,
        )
        session.commit()
        session.refresh(record)
        return _detach(session, record)


def enqueue_job_in_session(
    session,
    *,
    job_type: str,
    payload: dict[str, Any],
    dedupe_key: str,
    priority: int = 100,
    max_attempts: int = 3,
    created_by_user_id: str | None = None,
) -> WorkerJob:
    normalized_payload = validate_job_payload(job_type, payload)
    normalized_dedupe = dedupe_key.strip()
    if not normalized_dedupe or len(normalized_dedupe) > 240:
        raise WorkerQueueError("A bounded dedupe_key is required.")
    if priority < 0:
        raise WorkerQueueError("Job priority must be non-negative.")
    if max_attempts < 1 or max_attempts > 20:
        raise WorkerQueueError("max_attempts must be between 1 and 20.")

    record = WorkerJob(
        job_type=job_type,
        dedupe_key=normalized_dedupe,
        payload_json=normalized_payload,
        priority=priority,
        max_attempts=max_attempts,
        created_by_user_id=created_by_user_id,
    )
    try:
        with session.begin_nested():
            session.add(record)
            session.flush()
    except IntegrityError:
        existing = session.exec(
            select(WorkerJob).where(WorkerJob.dedupe_key == normalized_dedupe)
        ).first()
        if existing is None:
            raise
        return existing
    return record


def validate_job_payload(job_type: str, payload: dict[str, Any]) -> dict[str, int]:
    field = JOB_PAYLOAD_SCHEMAS.get(job_type)
    if field is None:
        raise WorkerQueueError(f"Unsupported worker job type: {job_type}.")
    if set(payload) != {field}:
        raise WorkerQueueError(f"{job_type} payload must contain only {field}.")
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WorkerQueueError(f"{field} must be a positive integer.")
    return {field: value}


def claim_next_job(worker_id: str, allowed_job_types: set[str] | None = None) -> WorkerJob | None:
    normalized_worker_id = worker_id.strip()
    if not normalized_worker_id:
        raise WorkerQueueError("worker_id is required.")
    now = utc_now_dt()
    lock_until = now + timedelta(seconds=max(5, settings.WORKER_JOB_LOCK_SECONDS))

    with session_scope() as session:
        _release_expired_locks(session, now)
        candidate = (
            select(WorkerJob.id)
            .where(
                WorkerJob.status.in_(("queued", "failed")),
                WorkerJob.available_at <= now,
                (WorkerJob.locked_until.is_(None)) | (WorkerJob.locked_until < now),
                WorkerJob.attempt_count < WorkerJob.max_attempts,
            )
            .order_by(WorkerJob.priority.asc(), WorkerJob.available_at.asc(), WorkerJob.id.asc())
            .limit(1)
        )
        if allowed_job_types is not None:
            candidate = candidate.where(WorkerJob.job_type.in_(sorted(allowed_job_types)))
        if engine.dialect.name == "postgresql":
            candidate = candidate.with_for_update(skip_locked=True)

        claim = (
            update(WorkerJob)
            .where(WorkerJob.id == candidate.scalar_subquery())
            .values(
                status="processing",
                attempt_count=WorkerJob.attempt_count + 1,
                locked_by=normalized_worker_id,
                locked_until=lock_until,
                started_at=now,
                finished_at=None,
                last_error=None,
            )
            .returning(WorkerJob.id)
        )
        job_id = session.execute(claim).scalar_one_or_none()
        session.commit()
        if job_id is None:
            return None
        job = session.get(WorkerJob, int(job_id))
        if job is None:
            return None
        return _detach(session, job)


def complete_job(job_id: int, worker_id: str) -> WorkerJob:
    return _finish_claimed_job(
        job_id=job_id,
        worker_id=worker_id,
        status="succeeded",
        error=None,
        retry_delay_seconds=0,
    )


def fail_job(job_id: int, worker_id: str, error: Exception | str) -> WorkerJob:
    with session_scope() as session:
        job = session.get(WorkerJob, job_id)
        if job is None:
            raise LookupError("Worker job not found.")
        _require_claim_owner(job, worker_id)
        exhausted = job.attempt_count >= job.max_attempts
        status = "dead" if exhausted else "failed"
        retry_delay = 0
        if not exhausted:
            retry_delay = max(1, settings.WORKER_RETRY_BASE_SECONDS) * (2 ** max(job.attempt_count - 1, 0))
        return _finish_in_session(
            session=session,
            job=job,
            status=status,
            error=safe_error_message(error),
            retry_delay_seconds=retry_delay,
        )


def retry_job(job_id: int) -> WorkerJob:
    with session_scope() as session:
        job = session.get(WorkerJob, job_id)
        if job is None:
            raise LookupError("Worker job not found.")
        if job.status not in {"failed", "dead"}:
            raise WorkerQueueError("Only failed or dead jobs can be retried.")
        job.status = "queued"
        job.attempt_count = 0
        job.available_at = utc_now_dt()
        job.locked_by = None
        job.locked_until = None
        job.started_at = None
        job.finished_at = None
        job.last_error = None
        session.add(job)
        session.commit()
        session.refresh(job)
        return _detach(session, job)


def queue_counts() -> dict[str, int]:
    with session_scope() as session:
        rows = session.exec(
            select(WorkerJob.status, func.count(WorkerJob.id)).group_by(WorkerJob.status)
        ).all()
    counts = {status: 0 for status in ("queued", "processing", "succeeded", "failed", "dead", "cancelled")}
    counts.update({str(status): int(count) for status, count in rows})
    return counts


def _finish_claimed_job(
    *,
    job_id: int,
    worker_id: str,
    status: str,
    error: str | None,
    retry_delay_seconds: int,
) -> WorkerJob:
    with session_scope() as session:
        job = session.get(WorkerJob, job_id)
        if job is None:
            raise LookupError("Worker job not found.")
        _require_claim_owner(job, worker_id)
        return _finish_in_session(
            session=session,
            job=job,
            status=status,
            error=error,
            retry_delay_seconds=retry_delay_seconds,
        )


def _finish_in_session(
    *,
    session,
    job: WorkerJob,
    status: str,
    error: str | None,
    retry_delay_seconds: int,
) -> WorkerJob:
    now = utc_now_dt()
    job.status = status
    job.available_at = now + timedelta(seconds=retry_delay_seconds)
    job.locked_by = None
    job.locked_until = None
    job.finished_at = now
    job.last_error = error
    session.add(job)
    session.commit()
    session.refresh(job)
    return _detach(session, job)


def _require_claim_owner(job: WorkerJob, worker_id: str) -> None:
    if job.status != "processing" or job.locked_by != worker_id:
        raise WorkerQueueError("Worker job is not claimed by this worker.")


def _release_expired_locks(session, now) -> None:
    status_value = case(
        (WorkerJob.attempt_count >= WorkerJob.max_attempts, "dead"),
        else_="failed",
    )
    session.execute(
        update(WorkerJob)
        .where(
            WorkerJob.status == "processing",
            WorkerJob.locked_until.is_not(None),
            WorkerJob.locked_until < now,
        )
        .values(
            status=status_value,
            available_at=now,
            locked_by=None,
            locked_until=None,
            finished_at=now,
            last_error="Worker lock expired before completion.",
        )
    )


def safe_error_message(error: Exception | str) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    message = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", message)
    message = re.sub(
        r"(?i)\b(access[_-]?token|authorization|cookie|password|secret|client[_-]?id)"
        r"\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        message,
    )
    return (message or "Worker job failed.")[:500]


def _detach(session, job: WorkerJob) -> WorkerJob:
    session.expunge(job)
    return job
