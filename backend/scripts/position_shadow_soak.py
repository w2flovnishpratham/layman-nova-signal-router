#!/usr/bin/env python
"""Shadow soak harness (Phase 2A): exercise a representative position
lifecycle through the REAL state_store choke point with the shadow flag on,
measure shadow-write latency, and report the reviewer's gate metrics.

Usage (from backend/, DATABASE_URL set, staging or disposable PG):

    python -m scripts.position_shadow_soak --user <uuid> [--monitor-updates 20]

The harness only writes JSON position state the way the execution paths do
(entry -> monitor updates -> partial exit -> exit -> re-entry -> reversal ->
manual exit -> EOD exit -> paper position) and lets the normal shadow hook
mirror it. It never touches the broker, never reads the shadow for execution,
and is safe to run in staging against a throwaway soak user. Combine with:

    python -m scripts.import_positions_to_shadow      (before)
    python -m scripts.position_shadow_parity          (before/after)
    a server restart + startup reconciliation         (staging step)

Report fields map 1:1 to the Phase 2B gate list.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.db.engine import database_configured, session_scope  # noqa: E402


def _percentiles(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(samples)

    def pct(p: float) -> float:
        index = min(int(len(ordered) * p), len(ordered) - 1)
        return round(ordered[index], 2)

    return {"p50_ms": pct(0.50), "p95_ms": pct(0.95), "p99_ms": pct(0.99), "max_ms": round(ordered[-1], 2)}


def entry_dict(security_id: str, side: str, price: float) -> dict:
    return {
        "has_open_position": True, "strategy_code": "supertrend", "symbol": "NIFTY",
        "security_id": security_id, "trading_symbol": f"NIFTY SOAK {side}",
        "option_side": side, "strike": 25500, "expiry": "2026-07-16",
        "qty": 75, "requested_qty": 75, "filled_qty": 75, "partial_fill": False,
        "entry_order_id": f"SOAK-{security_id}", "entry_price": price,
        "exit_management": "DHAN_SUPER", "broker_sl_price": price * 0.8,
        "broker_tp_price": price * 1.2, "product_type": "INTRADAY",
        "live_pnl": {"status": "tracking_pending", "qty": 75},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="Soak user id (UUID, must exist in users).")
    parser.add_argument("--monitor-updates", type=int, default=20)
    args = parser.parse_args()

    if not database_configured():
        print("DATABASE_URL is not configured.", file=sys.stderr)
        return 2
    if not settings.POSITION_DB_SHADOW_WRITE_ENABLED:
        print("POSITION_DB_SHADOW_WRITE_ENABLED is off — enable it for the soak.", file=sys.stderr)
        return 2

    from sqlalchemy import select, text

    from app.db import crud, models
    from app.services import position_store, state_store
    from app.services.execution_context import bind_execution_context
    from app.services.user_context import current_user_from_model

    with session_scope() as db:
        user_model = crud.get_user_by_id(db, args.user)
        if user_model is None:
            print("Soak user not found.", file=sys.stderr)
            return 2
        db.expunge(user_model)
    user = current_user_from_model(user_model)

    latencies: list[float] = []
    json_latencies: list[float] = []

    def timed_write(fn, payload) -> None:
        json_started = time.perf_counter()
        fn(payload if payload is not None else None) if payload is not None else fn()
        total = (time.perf_counter() - json_started) * 1000
        latencies.append(total)

    def write_live(position) -> None:
        timed_write(state_store.set_live_open_position, position)

    with bind_execution_context(user):
        # Pure-JSON baseline (flag briefly off) for max JSON delay comparison.
        settings.POSITION_DB_SHADOW_WRITE_ENABLED = False
        for _ in range(10):
            started = time.perf_counter()
            state_store.set_live_open_position(entry_dict("90001", "CE", 100.0))
            json_latencies.append((time.perf_counter() - started) * 1000)
            state_store.clear_live_open_position()
        settings.POSITION_DB_SHADOW_WRITE_ENABLED = True

        # 1. Entry
        write_live(entry_dict("10001", "CE", 120.0))
        # 2. Monitor updates
        for tick in range(args.monitor_updates):
            snapshot = entry_dict("10001", "CE", 120.0)
            snapshot["live_pnl"] = {"status": "tracking", "qty": 75, "ltp": 120.0 + tick * 0.05}
            write_live(snapshot)
        # 3. Partial exit
        partial = entry_dict("10001", "CE", 120.0)
        partial["qty"] = 25
        partial["partial_exit"] = {"order_id": "SOAK-X1", "filled_qty": 50, "remaining_qty": 25, "status": "PARTIAL"}
        write_live(partial)
        # 4. Full exit
        timed_write(lambda _=None: state_store.clear_live_open_position(), None)
        # 5. Re-entry then reversal marker then reversal completion
        write_live(entry_dict("10002", "PE", 95.0))
        reversing = entry_dict("10002", "PE", 95.0)
        reversing["reversal_exit"] = {"status": "TRANSIT", "order_id": "SOAK-REV"}
        write_live(reversing)
        timed_write(lambda _=None: state_store.clear_live_open_position(), None)
        write_live(entry_dict("10003", "CE", 88.0))
        # 6. Manual exit
        timed_write(lambda _=None: state_store.clear_live_open_position(), None)
        # 7. EOD-style entry+square-off
        write_live(entry_dict("10004", "PE", 77.0))
        timed_write(lambda _=None: state_store.clear_live_open_position(), None)
        # 8. Paper position lifecycle
        timed_write(state_store.set_paper_position, entry_dict("20001", "CE", 55.0))
        timed_write(lambda _=None: state_store.clear_paper_position(), None)

        json_live = state_store.get_live_open_position()
        json_paper = state_store.get_paper_position()

    findings = position_store.compare_user_positions(
        uuid.UUID(user.id_str), json_live=json_live, json_paper=json_paper
    )
    with session_scope() as db:
        duplicate_events = db.execute(text(
            "SELECT COUNT(*) FROM (SELECT idempotency_key FROM position_events "
            "WHERE idempotency_key IS NOT NULL GROUP BY idempotency_key HAVING COUNT(*) > 1) AS d"
        )).scalar()
        active_dupes = db.execute(text(
            "SELECT COUNT(*) FROM (SELECT user_id, execution_mode FROM strategy_instance_positions "
            "WHERE position_state IN ('entering','open','scaling_in','exiting','reversing','error_reconciling') "
            "GROUP BY user_id, execution_mode HAVING COUNT(*) > 1) AS d"
        )).scalar()
        lifecycles = db.scalar(
            select(models.StrategyInstancePosition).where(
                models.StrategyInstancePosition.user_id == uuid.UUID(user.id_str)
            ).limit(1)
        )

    health = position_store.shadow_health()
    report = {
        "shadow_writes_attempted": health["writes_attempted"],
        "shadow_writes_succeeded": health["writes_succeeded"],
        "shadow_failures_by_operation": health["failures_by_operation"],
        "writes_skipped_breaker_open": health["writes_skipped_breaker_open"],
        "noop_identical_snapshot": health["writes_noop_identical_snapshot"],
        "breaker": health["breaker"],
        "parity_findings": findings,
        "unexplained_mismatches": len(findings),
        "duplicate_event_keys": duplicate_events,
        "duplicate_active_positions": active_dupes,
        "lifecycle_rows_present": bool(lifecycles),
        "combined_write_latency": _percentiles(latencies),
        "json_only_baseline_latency": _percentiles(json_latencies),
    }
    print(json.dumps(report, indent=2, default=str))
    gate_ok = (
        not findings and duplicate_events == 0 and active_dupes == 0
        and health["failures_by_operation"] == {k: 0 for k in health["failures_by_operation"]}
    )
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
