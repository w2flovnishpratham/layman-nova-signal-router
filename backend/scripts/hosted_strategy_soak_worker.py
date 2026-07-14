from __future__ import annotations

import time

from app.services.hosted_strategy_runtime import process_queued_jobs_once, recover_stale_jobs


def main() -> None:
    print(f"[hosted-worker] started (recovered={recover_stale_jobs()})", flush=True)
    while True:
        if not process_queued_jobs_once(limit=10): time.sleep(.2)


if __name__ == "__main__": main()
