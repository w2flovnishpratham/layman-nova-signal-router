"""Standalone durable strategy-job worker process for the Phase 3A soak.

Runs the SAME claim/execute code as production (recover_stale_jobs +
process_queued_jobs_once) in its own OS process, with market-hours guards
patched exactly like the soak app.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import atm_ltp_service, execution_router, paper_broker, risk_manager  # noqa: E402

execution_router._market_is_open = lambda: True
paper_broker._market_is_open = lambda: True
risk_manager._market_is_open = lambda: True
atm_ltp_service._market_is_open = lambda: True

from app.workers.strategy_job_worker import process_queued_jobs_once, recover_stale_jobs  # noqa: E402


def main() -> None:
    recovered = recover_stale_jobs()
    print(f"[soak-worker] started (recovered={recovered})", flush=True)
    while True:
        try:
            processed = process_queued_jobs_once(limit=10)
        except Exception as exc:  # keep the loop alive like the daemon thread
            print(f"[soak-worker] loop error: {exc}", flush=True)
            processed = 0
        if not processed:
            time.sleep(0.3)


if __name__ == "__main__":
    main()
