"""Durable executor for per-user strategy fan-out jobs."""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db import backoff as db_backoff
from app.db import models
from app.db.engine import database_configured, get_engine, session_scope
from app.schemas.signal import NormalizedSignal
from app.services import (
    private_webhook_execution,
    setup_configuration,
    strategy_fanout,
    webhook_replay_store,
)

logger = logging.getLogger("nova_signal_router.strategy_jobs")
_STOP_EVENT = threading.Event()
_WAKE_EVENT = threading.Event()
_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.RLock()
_USER_LOCKS: dict[uuid.UUID, threading.Lock] = {}
_USER_LOCKS_GUARD = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _job_snapshot(job: models.StrategyExecutionJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "strategy_signal_id": job.strategy_signal_id,
        "user_id": job.user_id,
        "strategy_name": job.strategy_name,
        "signal_id": job.signal_id,
        "signal_payload": job.signal_payload,
        "lots": job.lots,
        "execution_mode": job.execution_mode,
        "configuration_revision_id": job.configuration_revision_id,
        "configuration_revision": job.configuration_revision,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
    }


def _job_configuration(job: dict[str, Any]) -> dict[str, Any] | None:
    revision_id = job.get("configuration_revision_id")
    required_mode = setup_configuration.configuration_mode_for_execution(
        job["execution_mode"]
    )
    if revision_id is None:
        return None
    with session_scope() as db:
        row = db.get(models.StrategyConfigurationRevision, revision_id)
        if (
            row is None
            or row.user_id != job["user_id"]
            or row.mode != required_mode
            or int(row.revision) != int(job.get("configuration_revision") or 0)
        ):
            return None
        return {
            "id": str(row.id),
            "strategy_instance_id": str(row.strategy_instance_id),
            "strategy_version_id": str(row.strategy_version_id),
            "mode": row.mode,
            "revision": int(row.revision),
            "configuration": dict(row.configuration_json or {}),
            "risk": dict(row.risk_json or {}),
        }


def recover_stale_jobs() -> int:
    if not database_configured():
        return 0
    try:
        webhook_replay_store.prune_webhook_replay_records()
    except Exception:
        logger.exception("Webhook replay store pruning failed")
    cutoff = _now() - timedelta(seconds=max(settings.STRATEGY_JOB_STALE_SECONDS, 30))
    recovered = 0
    with session_scope() as db:
        jobs = db.scalars(
            select(models.StrategyExecutionJob).where(
                models.StrategyExecutionJob.status == "running",
                models.StrategyExecutionJob.locked_at < cutoff,
            )
        ).all()
        for job in jobs:
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.completed_at = _now()
                job.last_error = "Execution worker stopped before completion; manual review required."
            else:
                job.status = "queued"
                job.available_at = _now()
                job.locked_at = None
            job.updated_at = _now()
            recovered += 1
    return recovered


def _claim_jobs(limit: int) -> list[dict[str, Any]]:
    now = _now()
    with session_scope() as db:
        statement = (
            select(models.StrategyExecutionJob)
            .where(
                models.StrategyExecutionJob.status == "queued",
                models.StrategyExecutionJob.available_at <= now,
            )
            .order_by(models.StrategyExecutionJob.created_at.asc())
            .limit(limit)
        )
        if get_engine().dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        jobs = list(db.scalars(statement).all())
        snapshots: list[dict[str, Any]] = []
        for job in jobs:
            job.status = "running"
            job.attempts += 1
            job.locked_at = now
            job.updated_at = now
            snapshots.append(_job_snapshot(job))
        db.flush()
        return snapshots


def _complete_job(
    job: dict[str, Any],
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with session_scope() as db:
        row = db.get(models.StrategyExecutionJob, job["id"])
        if row is None:
            return
        row.status = status
        row.result_summary = result
        row.last_error = error
        row.completed_at = _now()
        row.updated_at = _now()
    if status == "completed" and private_webhook_execution.is_private_job(
        str(job.get("strategy_name") or "")
    ):
        try:
            from app.services import c2_tradingview_service

            c2_tradingview_service.record_paper_execution_evidence_from_job(
                job["id"]
            )
        except Exception:
            # The order result remains authoritative and completed. C2
            # graduation fails closed because missing evidence prevents READY.
            logger.exception(
                "Could not persist C2 Paper evidence for job %s",
                job["id"],
            )
    _refresh_signal_summary(job["strategy_signal_id"])


def _refresh_signal_summary(strategy_signal_id: uuid.UUID) -> None:
    with session_scope() as db:
        signal = db.get(models.StrategySignal, strategy_signal_id)
        if signal is None:
            return
        jobs = db.scalars(
            select(models.StrategyExecutionJob)
            .where(models.StrategyExecutionJob.strategy_signal_id == strategy_signal_id)
            .order_by(models.StrategyExecutionJob.created_at.asc())
        ).all()
        if any(job.status in {"queued", "running"} for job in jobs):
            signal.status = "processing"
            signal.updated_at = _now()
            return
        results = [
            job.result_summary
            or {
                "user_id": str(job.user_id),
                "status": job.status,
                "reason": job.last_error,
            }
            for job in jobs
        ]
        has_errors = any(
            job.status == "failed"
            or (job.result_summary or {}).get("status") == "error"
            for job in jobs
        )
        signal.status = "completed_with_errors" if has_errors else "completed"
        signal.result_summary = {
            "strategy_name": signal.strategy_name,
            "signal_id": signal.signal_id,
            "subscriber_count": len(jobs),
            "results": results,
        }
        signal.updated_at = _now()
        webhook_event_provider = signal.webhook_event_provider
        signal_id = signal.signal_id
        terminal_status = signal.status

    # Closes the webhook_event this signal's inbound webhook was claimed
    # under -- without this, every event on this (async intake) path stays
    # at "queued"/"accepted" forever, success or failure, with no way to
    # tell a completed signal from a lost one. Non-fatal: a failure here
    # just leaves the event undetected-stuck, same as before this existed,
    # never blocks the signal's own already-committed terminal state above.
    if webhook_event_provider:
        try:
            webhook_replay_store.update_webhook_event(
                provider=webhook_event_provider,
                event_id=signal_id,
                processed_status="completed" if terminal_status == "completed" else "failed",
                error=None if terminal_status == "completed" else "one_or_more_jobs_failed",
            )
        except Exception:
            logger.exception("Could not close webhook_event status for signal %s", strategy_signal_id)


def _execute_job(job: dict[str, Any]) -> None:
    if _retry_would_risk_duplicate_execution(job):
        _complete_job(
            job,
            status="failed",
            error=(
                "Recovered execution job was not re-run because its previous "
                "attempt may have reached the broker. Manual review required."
            ),
        )
        return
    with _USER_LOCKS_GUARD:
        user_lock = _USER_LOCKS.setdefault(job["user_id"], threading.Lock())
    with user_lock:
        try:
            signal = NormalizedSignal.model_validate(job["signal_payload"])
            configuration = _job_configuration(job)
            if (
                setup_configuration.configuration_mode_for_execution(
                    job["execution_mode"]
                )
                is not None
                and configuration is None
            ):
                _complete_job(
                    job,
                    status="failed",
                    error="CONFIGURATION_REVISION_UNAVAILABLE",
                )
                return
            if private_webhook_execution.is_private_job(job["strategy_name"]):
                # Private instance webhook job: revalidates instance state at
                # processing time, then routes through the same execution path.
                result = private_webhook_execution.execute_private_job(
                    job,
                    signal,
                    configuration=configuration,
                )
            else:
                # Shared/NOVA_SHARED broadcast jobs (strategy_fanout.enqueue_strategy_signal)
                # carry a placeholder signal (no security_id/strike/expiry --
                # see build_broadcast_signal) resolved here per-subscriber,
                # same as the private-webhook path's own enricher. A signal
                # that already has a contract (none do today, but future
                # sources might) is left untouched.
                result = strategy_fanout.dispatch_signal_job(
                    user_id=job["user_id"],
                    strategy_name=job["strategy_name"],
                    lots=job["lots"],
                    execution_mode=job["execution_mode"],
                    signal=signal,
                    configuration=configuration,
                    signal_enricher=(
                        private_webhook_execution._make_enricher(job["lots"])
                        if signal.security_id is None
                        else None
                    ),
                )
        except Exception as exc:
            logger.exception("Strategy job %s failed", job["id"])
            _complete_job(job, status="failed", error=str(exc))
            return
        _complete_job(job, status="completed", result=result)


def _retry_would_risk_duplicate_execution(job: dict[str, Any]) -> bool:
    mode = str(job.get("execution_mode") or "")
    attempts = int(job.get("attempts") or 0)
    return attempts > 1 and mode in {"paper_live_data", "real_orders"}


def process_queued_jobs_once(*, limit: int | None = None) -> int:
    if not database_configured() or not settings.STRATEGY_JOB_WORKER_ENABLED:
        return 0
    concurrency = max(int(settings.STRATEGY_JOB_WORKER_CONCURRENCY), 1)
    jobs = _claim_jobs(max(limit or concurrency, 1))
    if not jobs:
        return 0
    with ThreadPoolExecutor(
        max_workers=min(concurrency, len(jobs)),
        thread_name_prefix="nova-strategy-user",
    ) as executor:
        list(executor.map(_execute_job, jobs))
    for strategy_signal_id in {job["strategy_signal_id"] for job in jobs}:
        _refresh_signal_summary(strategy_signal_id)
    return len(jobs)


def _loop() -> None:
    logger.info("Durable strategy job worker started.")
    # Recovery used to run here, before the loop and outside its except, so a
    # database outage at startup raised straight out of the thread and killed
    # it for good -- the loop below is built to ride an outage out with
    # backoff, but never got to run, and queued jobs sat unprocessed until
    # someone restarted the process. Observed during a Neon quota outage.
    # Retrying inside the loop keeps the recovery and drops the fragility.
    stale_jobs_recovered = False
    poll_seconds = max(float(settings.STRATEGY_JOB_WORKER_POLL_SECONDS), 0.1)
    while not _STOP_EVENT.is_set():
        try:
            if not stale_jobs_recovered:
                recover_stale_jobs()
                stale_jobs_recovered = True
            processed = process_queued_jobs_once()
        except Exception:
            logger.exception("Durable strategy job worker loop failed")
            processed = 0
        if processed:
            continue
        # Backs off automatically while the database is unreachable, so a
        # 0.5s claim poll does not become a connection storm during an outage.
        _WAKE_EVENT.wait(db_backoff.poll_delay(poll_seconds))
        _WAKE_EVENT.clear()
    logger.info("Durable strategy job worker stopped.")


def wake_strategy_job_worker() -> None:
    _WAKE_EVENT.set()


def start_strategy_job_worker() -> None:
    global _THREAD
    if (
        not database_configured()
        or not settings.STRATEGY_JOB_WORKER_ENABLED
    ):
        return
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP_EVENT.clear()
        _THREAD = threading.Thread(
            target=_loop,
            name="nova-strategy-jobs",
            daemon=True,
        )
        _THREAD.start()


def stop_strategy_job_worker() -> None:
    global _THREAD
    with _THREAD_LOCK:
        _STOP_EVENT.set()
        _WAKE_EVENT.set()
        if _THREAD is not None and _THREAD.is_alive():
            _THREAD.join(timeout=3.0)
        _THREAD = None


def strategy_job_worker_status() -> dict[str, Any]:
    return {
        "enabled": bool(settings.STRATEGY_JOB_WORKER_ENABLED),
        "running": bool(_THREAD is not None and _THREAD.is_alive()),
        "database_configured": database_configured(),
    }
