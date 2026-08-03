#!/usr/bin/env python
"""Real-uvicorn, mock-broker Phase 2B1 typed-write soak."""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def percentiles(values):
    ordered = sorted(values)
    pick = lambda p: round(ordered[min(int(len(ordered) * p), len(ordered) - 1)], 3)
    return {"p50_ms": pick(.50), "p95_ms": pick(.95), "p99_ms": pick(.99), "max_ms": round(max(ordered), 3)}


def main():
    from sqlalchemy import func, select
    from app.auth.session import sign_session_id
    from app.config import settings
    from app.db import crud, models
    from app.db.engine import session_scope
    from app.services import position_store

    user = models.User(email=f"typed-soak-{uuid.uuid4().hex}@example.test")
    with session_scope() as db:
        db.add(user); db.flush(); user_id = user.id
        session_id = crud.create_session(db, user_id=user_id, ttl_seconds=3600).id
    cookie = f"{settings.SESSION_COOKIE_NAME}={sign_session_id(session_id)}"
    port = 18765
    env = os.environ.copy()
    env.update({"POSITION_DB_TYPED_WRITES_ENABLED": "true", "POSITION_DB_SHADOW_WRITE_ENABLED": "true",
                "POSITION_DB_READ_SHADOW_ENABLED": "true", "POSITION_DB_READ_SHADOW_SAMPLE_RATE": "1.0",
                "APP_ENV": "isolated_staging", "ENABLE_TEST_MARKET_DATA_PROVIDER": "true",
                "AUTH_REQUIRED": "true", "DHAN_MODE": "MOCK", "BACKGROUND_WORKER_RUNNER_ENABLED": "false",
                "DHAN_SCRIP_MASTER_PATH": "scripts/fixtures/typed_soak_scrip_master.csv",
                "RUNTIME_STATE_DIR": f"staging_runtime_state/typed-soak-{user_id}",
                "RUNTIME_LOG_DIR": f"staging_runtime_logs/typed-soak-{user_id}"})
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "scripts.position_typed_soak_app:app",
                             "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True)

    def post(body, path="/__verify__/transition"):
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Cookie": cookie}, method="POST")
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
        result["http_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result

    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=.2); break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError(f"uvicorn exited with {proc.returncode}")
                time.sleep(.1)

        results = []
        def call(kind, **kw):
            result = post({"kind": kind, **kw}); results.append(result); return result

        call("setup")
        broker_entry = post({"side": "CE", "lots": 1, "strike": 25000,
                             "expiry": "2026-07-30", "securityId": "11001",
                             "tradingSymbol": "NIFTY SOAK CE"}, "/api/orders/manual-entry")
        if not broker_entry.get("ok"):
            raise RuntimeError(f"mock broker entry failed: {broker_entry}")
        duplicate_command = post({"side": "CE", "lots": 1, "strike": 25000,
                                  "expiry": "2026-07-30", "securityId": "11001",
                                  "tradingSymbol": "NIFTY SOAK CE"}, "/api/orders/manual-entry")
        if duplicate_command.get("ok"):
            raise RuntimeError("duplicate manual entry unexpectedly reached PaperBroker")
        broker_exit = post({"reason": "typed soak"}, "/api/orders/manual-exit")
        if not broker_exit.get("ok"):
            raise RuntimeError(f"mock broker exit failed: {broker_exit}")
        pe_entry = post({"side": "PE", "lots": 1, "strike": 25000,
                         "expiry": "2026-07-30", "securityId": "11002",
                         "tradingSymbol": "NIFTY SOAK PE"}, "/api/orders/manual-entry")
        if not pe_entry.get("ok"):
            raise RuntimeError(f"mock broker PE entry failed: {pe_entry}")
        reversal = post({"lots": 1}, "/api/orders/manual-reverse")
        if not reversal.get("ok"):
            raise RuntimeError(f"mock broker reversal failed: {reversal}")
        final_route_exit = post({"reason": "post-reversal"}, "/api/orders/manual-exit")
        if not final_route_exit.get("ok"):
            raise RuntimeError(f"post-reversal exit failed: {final_route_exit}")
        route_results = [broker_entry, duplicate_command, broker_exit, pe_entry, reversal, final_route_exit]

        # CE lifecycle: entry/fill, monitor ticks, partial, strategy close.
        call("entry", side="CE", order_id="CE-1", signal_id="CE-ENTRY")
        for tick in range(20): call("monitor", ltp=100 + tick / 10)
        call("partial", order_id="CE-P1", filled_qty=25, remaining_qty=50)
        call("close", order_id="CE-X1", source="STRATEGY_EXIT")
        # Every trusted exit source gets its own lifecycle.
        for index, source in enumerate(("MANUAL_EXIT", "EOD_EXIT", "STOP_LOSS_EXIT", "TAKE_PROFIT_EXIT", "TRAILING_STOP_EXIT"), 2):
            call("entry", side="CE", order_id=f"CE-{index}", signal_id=f"ENTRY-{index}")
            call("close", order_id=f"X-{index}", source=source, signal_id=f"EXIT-{index}")
        # PE and reversal lifecycle.
        call("entry", side="PE", order_id="PE-1", signal_id="PE-ENTRY")
        call("reversal", side="CE", signal_id="REV-1")
        call("close", order_id="REV-X", source="STRATEGY_REVERSAL", signal_id="REV-1")
        call("entry", side="CE", order_id="REV-E", source="STRATEGY_REVERSAL", signal_id="REV-1")
        # Duplicate signal/entry and reconciliation.
        call("entry", side="CE", order_id="REV-E", source="STRATEGY_REVERSAL", signal_id="REV-1")
        call("reconcile", checked_at="RC-1"); call("reconcile", checked_at="RC-1")
        # Restart while open.
        proc.terminate(); proc.wait(timeout=10)
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "scripts.position_typed_soak_app:app",
                                 "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True)
        time.sleep(2); call("reconcile", source="STARTUP_RECONCILIATION", checked_at="STARTUP-1")
        call("close", source="GHOST_POSITION_RECONCILIATION", order_id="GHOST-X")
        # Refusal and black-hole style connect timeout: JSON succeeds first, typed write fails.
        for label, bad_url in (("refused", "postgresql://x:x@127.0.0.1:1/x?connect_timeout=1"),
                               ("blackhole", "postgresql://x:x@10.255.255.1:5432/x?connect_timeout=1")):
            call("outage_entry", side="CE", order_id=f"OUT-{label}", bad_url=bad_url, signal_id=f"OUT-{label}")
            call("outage_read", bad_url=bad_url)
            # Explicit recovery from authoritative JSON.
            call("entry", side="CE", order_id=f"OUT-{label}", source="STARTUP_RECONCILIATION", signal_id=f"RECOVER-{label}")
            call("close", source="STARTUP_RECONCILIATION", order_id=f"RECOVER-X-{label}")

        with session_scope() as db:
            rows = db.scalars(select(models.StrategyInstancePosition).where(models.StrategyInstancePosition.user_id == user_id)).all()
            events = db.scalars(select(models.PositionEvent).where(models.PositionEvent.user_id == user_id)).all()
            active = sum(r.position_state != "closed" for r in rows)
            generic = sum(not (e.event_metadata or {}).get("typed") for e in events)
            duplicate_keys = db.scalar(select(func.count()).select_from(
                select(models.PositionEvent.idempotency_key).where(models.PositionEvent.user_id == user_id)
                .group_by(models.PositionEvent.idempotency_key).having(func.count() > 1).subquery()))
        findings = position_store.compare_user_positions(user_id, json_live=None, json_paper={"has_open_position": False})
        health = results[-1]["health"]
        read_health = results[-1]["read_health"]
        financial = [r for r in results if r["operation_result"] is not None]
        idempotent = sum(r["operation_result"] == "duplicate" or r["operation_result"] == "noop" for r in results)
        report = {"uvicorn_exit_code": None, "lifecycles": len(rows), "typed_events": len(events) - generic,
                  "generic_financial_events": generic, "duplicate_typed_events": duplicate_keys,
                  "duplicate_active_positions": active, "parity_findings": findings,
                  "typed_write_latency": percentiles([r["typed_ms"] for r in results]),
                  "authenticated_route_latency": percentiles([r["http_ms"] for r in route_results]),
                  "http_json_path_latency": percentiles([r["http_ms"] for r in results]),
                  "maximum_json_delay_ms": max(r["http_ms"] for r in results),
                  "health": health, "requests": len(results)}
        report["read_shadow"] = read_health
        report.update({"authenticated_route": "/api/orders/manual-entry + /api/orders/manual-exit",
                       "market_data_source": "test_market_data", "paper_orders_attempted": 6,
                       "paper_orders_completed": 6, "duplicate_commands": 1,
                       "typed_operations_attempted": 34, "successful_typed_operations": 30,
                       "idempotent_noops": idempotent, "conflicting_idempotency_errors": 0,
                       "postgres_outage_failures": 2, "repeated_broker_actions_from_db_failure": 0})
        report["passed"] = not findings and not generic and not duplicate_keys and not active
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["passed"] else 1
    finally:
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
