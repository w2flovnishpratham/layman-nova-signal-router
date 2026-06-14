from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.auth.db import init_database, session_scope
from app.auth.models import WorkerHeartbeat, WorkerJob, utc_now_dt
from app.config import settings
from app.services import signal_fanout_service
from app.services.strategy_catalog_service import seed_strategy_catalog
from app.services.user_notification_service import deliver_user_notification
from app.services.worker_policy import validate_paper_worker_configuration
from app.services.queue_service import (
    JOB_PAPER_SIGNAL_EXECUTION,
    JOB_LIVE_ORDER_EXECUTION,
    JOB_STRATEGY_SIGNAL_FANOUT,
    JOB_USER_NOTIFICATION,
    claim_next_job,
    complete_job,
    fail_job,
    safe_error_message,
    validate_job_payload,
)


logger = logging.getLogger("paper_worker")


PAPER_JOB_TYPES = {JOB_STRATEGY_SIGNAL_FANOUT, JOB_PAPER_SIGNAL_EXECUTION, JOB_USER_NOTIFICATION}


def run_worker_once(worker_id: str, allowed_job_types: set[str] | None = None) -> WorkerJob | None:
    job = claim_next_job(worker_id, allowed_job_types)
    if job is None:
        return None
    try:
        _dispatch_job(job)
    except Exception as exc:
        safe_error = safe_error_message(exc)
        if job.job_type == JOB_PAPER_SIGNAL_EXECUTION:
            user_signal_job_id = int(job.payload_json.get("user_signal_job_id") or 0)
            if user_signal_job_id > 0:
                try:
                    signal_fanout_service.fail_paper_job(user_signal_job_id, safe_error)
                except Exception:
                    logger.exception(
                        "Failed to persist Paper job failure: user_signal_job_id=%s",
                        user_signal_job_id,
                    )
        logger.error(
            "Worker job failed: id=%s type=%s error=%s",
            job.id,
            job.job_type,
            safe_error,
        )
        return fail_job(int(job.id), worker_id, exc)
    return complete_job(int(job.id), worker_id)


def drain_worker_jobs(*, max_jobs: int = 1000, allowed_job_types: set[str] | None = None) -> int:
    worker_id = f"inline:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    processed = 0
    while processed < max_jobs:
        job = run_worker_once(worker_id, allowed_job_types)
        if job is None:
            break
        processed += 1
    return processed


def run_forever() -> None:
    from app.main import validate_production_configuration

    validate_production_configuration()
    validate_paper_worker_configuration()
    init_database()
    seed_strategy_catalog()
    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    concurrency = settings.PAPER_WORKER_CONCURRENCY
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="paper-worker") as pool:
        futures = [
            pool.submit(_worker_loop, _worker_id(index), stop_event)
            for index in range(concurrency)
        ]
        for future in futures:
            future.result()


def _worker_loop(worker_id: str, stop_event: threading.Event) -> None:
    _heartbeat(worker_id, "starting")
    last_heartbeat = 0.0
    try:
        while not stop_event.is_set():
            now = time.monotonic()
            if now - last_heartbeat >= settings.WORKER_HEARTBEAT_SECONDS:
                _heartbeat(worker_id, "running")
                last_heartbeat = now
            job = run_worker_once(worker_id, PAPER_JOB_TYPES)
            if job is None:
                stop_event.wait(settings.WORKER_POLL_INTERVAL_SECONDS)
    except Exception:
        _heartbeat(worker_id, "failed")
        raise
    finally:
        _heartbeat(worker_id, "stopped")


def _dispatch_job(job: WorkerJob) -> None:
    payload = validate_job_payload(job.job_type, dict(job.payload_json))
    if job.job_type == JOB_STRATEGY_SIGNAL_FANOUT:
        signal_fanout_service.fanout_strategy_signal(payload["strategy_signal_id"])
        return
    if job.job_type == JOB_PAPER_SIGNAL_EXECUTION:
        signal_fanout_service.execute_paper_job(payload["user_signal_job_id"])
        return
    if job.job_type == JOB_USER_NOTIFICATION:
        deliver_user_notification(payload["user_notification_id"])
        return
    if job.job_type == JOB_LIVE_ORDER_EXECUTION:
        from app.services.live_execution_service import execute_live_order_job

        execute_live_order_job(payload["live_order_job_id"])
        return
    raise RuntimeError(f"Unsupported worker job type: {job.job_type}.")


def _heartbeat(worker_id: str, status: str) -> None:
    now = utc_now_dt()
    with session_scope() as session:
        heartbeat = session.get(WorkerHeartbeat, worker_id)
        if heartbeat is None:
            heartbeat = WorkerHeartbeat(
                worker_id=worker_id,
                worker_role="paper-worker",
                hostname=socket.gethostname(),
                pid=os.getpid(),
                started_at=now,
                last_seen_at=now,
                status=status,
                metadata_json={"concurrency": settings.PAPER_WORKER_CONCURRENCY},
            )
        else:
            heartbeat.last_seen_at = now
            heartbeat.status = status
        session.add(heartbeat)
        session.commit()


def _worker_id(index: int) -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{index}:{uuid.uuid4().hex[:8]}"


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
