from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid

from app.auth.db import init_database, session_scope
from app.auth.models import WorkerHeartbeat, utc_now_dt
from app.config import settings
from app.services.queue_service import JOB_LIVE_ORDER_EXECUTION
from app.services.strategy_catalog_service import seed_strategy_catalog
from app.services.worker_policy import validate_live_pilot_worker_configuration
from app.services.worker_runtime import run_worker_once


logger = logging.getLogger("live_pilot_worker")


def run_forever() -> None:
    from app.main import validate_production_configuration

    validate_production_configuration()
    validate_live_pilot_worker_configuration()
    init_database()
    seed_strategy_catalog()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:live:{uuid.uuid4().hex[:8]}"
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop_event.set())
    signal.signal(signal.SIGINT, lambda *_args: stop_event.set())
    _heartbeat(worker_id, "starting")
    try:
        while not stop_event.is_set():
            _heartbeat(worker_id, "running")
            job = run_worker_once(worker_id, {JOB_LIVE_ORDER_EXECUTION})
            if job is None:
                stop_event.wait(settings.WORKER_POLL_INTERVAL_SECONDS)
    except Exception:
        _heartbeat(worker_id, "failed")
        raise
    finally:
        _heartbeat(worker_id, "stopped")


def _heartbeat(worker_id: str, status: str) -> None:
    now = utc_now_dt()
    with session_scope() as session:
        heartbeat = session.get(WorkerHeartbeat, worker_id)
        if heartbeat is None:
            heartbeat = WorkerHeartbeat(
                worker_id=worker_id,
                worker_role="live-pilot-worker",
                hostname=socket.gethostname(),
                pid=os.getpid(),
                started_at=now,
                last_seen_at=now,
                status=status,
                metadata_json={"dry_run_only": settings.LIVE_ORDER_DRY_RUN_ONLY},
            )
        else:
            heartbeat.last_seen_at = now
            heartbeat.status = status
        session.add(heartbeat)
        session.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
