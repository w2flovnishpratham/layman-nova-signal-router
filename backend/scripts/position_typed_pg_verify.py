#!/usr/bin/env python
"""Independent-process PostgreSQL verification for Phase 2B1 typed positions.

Run from backend/ against a disposable PostgreSQL 16 database:
  python -m scripts.position_typed_pg_verify
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def snapshot(order="ENTRY-1", qty=75, security="10001"):
    return {
        "has_open_position": True, "strategy_code": "VERIFY", "symbol": "NIFTY",
        "security_id": security, "trading_symbol": f"NIFTY {security}",
        "option_side": "CE", "strike": 25000, "expiry": "2026-07-30",
        "qty": qty, "requested_qty": 75, "filled_qty": 75,
        "entry_order_id": order, "entry_price": 100.0, "partial_fill": False,
    }


def worker(args):
    os.environ["POSITION_DB_TYPED_WRITES_ENABLED"] = "true"
    from app.config import settings
    settings.POSITION_DB_TYPED_WRITES_ENABLED = True
    from app.services import position_operations as ops

    while time.time() < args.at:
        time.sleep(0.001)
    ctx = ops.OperationContext(uuid.UUID(args.user), "live")
    kw = json.loads(args.payload or "{}")
    if args.operation == "entry":
        result = ops.record_entry(ctx, source=ops.STRATEGY_ENTRY, **kw)
    elif args.operation == "partial":
        result = ops.apply_partial_exit_fill(ctx, source=ops.STRATEGY_EXIT, **kw)
    elif args.operation == "close":
        result = ops.close_position(ctx, **kw)
    elif args.operation == "reversal":
        result = ops.mark_reversal_requested(ctx, **kw)
    elif args.operation == "reconcile":
        result = ops.reconcile_position(ctx, source=ops.BROKER_RECONCILIATION, **kw)
    elif args.operation == "event-failure":
        original = ops._emit_event
        ops._emit_event = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("forced event failure"))
        try:
            result = ops.record_entry(ctx, source=ops.STRATEGY_ENTRY, **kw)
        finally:
            ops._emit_event = original
    else:
        raise ValueError(args.operation)
    print(json.dumps({"result": result}, default=str), flush=True)
    return 0


def lock_worker(args):
    from sqlalchemy import text
    from app.db.engine import session_scope
    from app.services.position_store import _advisory_lock_key
    with session_scope() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _advisory_lock_key(uuid.UUID(args.user), "live")})
        print("LOCKED", flush=True)
        time.sleep(300)


def run_pair(user, left, right):
    at = time.time() + 0.8
    procs = []
    for operation, payload in (left, right):
        cmd = [sys.executable, "-m", "scripts.position_typed_pg_verify", "--worker",
               "--user", str(user), "--operation", operation, "--payload", json.dumps(payload), "--at", str(at)]
        procs.append(subprocess.Popen(cmd, cwd=ROOT, env=os.environ.copy(), text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE))
    return [{"exit_code": p.wait(), "stdout": p.stdout.read().strip(), "stderr": p.stderr.read().strip()} for p in procs]


def entry_payload(order="ENTRY-1", qty=75, security="10001"):
    return {"signal_id": "SIG-1", "order_id": order, "requested_qty": qty,
            "filled_qty": qty, "fill_price": 100.0, "partial_fill": False,
            "position_snapshot": snapshot(order, qty, security)}


def main():
    from sqlalchemy import delete, func, select
    from app.config import settings
    from app.db import models
    from app.db.engine import get_engine, session_scope

    engine = get_engine()
    with engine.connect():
        pass
    if engine.dialect.name != "postgresql" or engine.dialect.server_version_info[0] != 16:
        raise SystemExit("PostgreSQL 16 is required")
    settings.POSITION_DB_TYPED_WRITES_ENABLED = True
    reports = []

    def new_user(name):
        user = models.User(email=f"typed-pg-{name}-{uuid.uuid4().hex}@example.test")
        with session_scope() as db:
            db.add(user); db.flush(); user_id = user.id
        return user_id

    def state(user):
        with session_scope() as db:
            rows = db.scalars(select(models.StrategyInstancePosition).where(models.StrategyInstancePosition.user_id == user)).all()
            events = db.scalars(select(models.PositionEvent).where(models.PositionEvent.user_id == user).order_by(models.PositionEvent.event_at)).all()
            active = sum(r.position_state != "closed" for r in rows)
            duplicate_keys = db.scalar(select(func.count()).select_from(
                select(models.PositionEvent.idempotency_key).where(models.PositionEvent.user_id == user)
                .group_by(models.PositionEvent.idempotency_key).having(func.count() > 1).subquery()))
            return {"positions": [{"state": r.position_state, "qty": r.open_quantity,
                                    "security_id": r.security_id, "entry_order_id": r.entry_order_id} for r in rows],
                    "events": [{"type": e.event_type, "source": e.source, "qty": e.quantity,
                                "order_id": e.broker_order_id} for e in events],
                    "active_positions": active, "duplicate_event_keys": duplicate_keys}

    def scenario(name, left, right, check):
        user = new_user(name)
        processes = run_pair(user, left, right)
        final = state(user)
        ok, detail = check(processes, final)
        reports.append({"scenario": name, "passed": ok, "processes": processes,
                        "final": final, "idempotency_result": detail})
        return user

    scenario("duplicate_entry", ("entry", entry_payload()), ("entry", entry_payload()),
             lambda p, s: (s["active_positions"] == 1 and [e["type"] for e in s["events"]] == ["entry_submitted", "entry_filled"] and not s["duplicate_event_keys"], "one lifecycle"))
    scenario("conflicting_entry_payload", ("entry", entry_payload(qty=75)), ("entry", entry_payload(qty=150, security="10002")),
             lambda p, s: (s["active_positions"] == 1 and any("conflict" in (x["stderr"] + x["stdout"]).lower() for x in p), "conflict visible"))

    for name, source in (("manual_vs_strategy", "MANUAL_EXIT"), ("eod_vs_strategy", "EOD_EXIT")):
        user = new_user(name); run_pair(user, ("entry", entry_payload()), ("entry", entry_payload()))
        processes = run_pair(user, ("close", {"source": source, "exit_order_id": f"{name}-1"}),
                             ("close", {"source": "STRATEGY_EXIT", "exit_order_id": f"{name}-2"}))
        final = state(user); closes = [e for e in final["events"] if "exit" in e["type"]]
        reports.append({"scenario": name, "passed": final["active_positions"] == 0 and len(closes) == 1,
                        "processes": processes, "final": final,
                        "idempotency_result": f"winning source={closes[0]['source'] if closes else None}"})

    user = new_user("reversal_vs_exit"); run_pair(user, ("entry", entry_payload()), ("entry", entry_payload()))
    processes = run_pair(user, ("reversal", {"signal_id": "REV", "from_option_side": "CE", "to_option_side": "PE"}),
                         ("close", {"source": "STRATEGY_EXIT", "exit_order_id": "EXIT"}))
    final = state(user)
    reports.append({"scenario": "reversal_vs_full_exit", "passed": len(final["positions"]) == 1 and final["positions"][0]["state"] in {"closed", "reversing"},
                    "processes": processes, "final": final, "idempotency_result": "serialized final state"})

    user = new_user("partial_reconcile"); run_pair(user, ("entry", entry_payload()), ("entry", entry_payload()))
    partial = snapshot(qty=50); partial["partial_exit"] = {"order_id": "PX-1", "filled_qty": 25}
    processes = run_pair(user, ("partial", {"signal_id": "PX", "exit_order_id": "PX-1", "filled_qty": 25, "remaining_qty": 50, "position_snapshot": partial}),
                         ("reconcile", {"status": "verified", "checked_at": "R1", "position_snapshot": snapshot(qty=75)}))
    final = state(user)
    reports.append({"scenario": "partial_exit_vs_reconciliation", "passed": final["positions"][0]["qty"] == 50,
                    "processes": processes, "final": final, "idempotency_result": "no quantity restoration"})

    for name, second_order in (("distinct_partial_fills", "PX-2"), ("identical_partial_retry", "PX-1")):
        user = new_user(name); run_pair(user, ("entry", entry_payload()), ("entry", entry_payload()))
        first = snapshot(qty=50); first["partial_exit"] = {"order_id": "PX-1"}
        second = snapshot(qty=25 if second_order != "PX-1" else 50); second["partial_exit"] = {"order_id": second_order}
        processes = run_pair(user,
            ("partial", {"signal_id": "PX", "exit_order_id": "PX-1", "filled_qty": 25, "remaining_qty": 50, "position_snapshot": first}),
            ("partial", {"signal_id": "PX", "exit_order_id": second_order, "filled_qty": 25, "remaining_qty": second["qty"], "position_snapshot": second}))
        final = state(user); partial_events = [e for e in final["events"] if e["type"] == "exit_partial_fill"]
        expected = 2 if second_order != "PX-1" else 1
        reports.append({"scenario": name, "passed": len(partial_events) == expected and final["positions"][0]["qty"] == second["qty"],
                        "processes": processes, "final": final, "idempotency_result": f"{len(partial_events)} fill events"})

    user = new_user("killed_lock")
    lock = subprocess.Popen([sys.executable, "-m", "scripts.position_typed_pg_verify", "--lock-worker", "--user", str(user)],
                            cwd=ROOT, env=os.environ.copy(), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    acquired = lock.stdout.readline().strip(); lock.kill(); killed_code = lock.wait()
    processes = run_pair(user, ("entry", entry_payload()), ("entry", entry_payload()))
    final = state(user)
    reports.append({"scenario": "process_termination_advisory_lock", "passed": acquired == "LOCKED" and final["active_positions"] == 1,
                    "processes": [{"exit_code": killed_code, "stdout": acquired}, *processes], "final": final,
                    "idempotency_result": "lock released after termination"})

    user = new_user("event_failure")
    processes = run_pair(user, ("event-failure", entry_payload()), ("event-failure", entry_payload()))
    final = state(user)
    reports.append({"scenario": "event_insert_failure", "passed": not final["positions"] and not final["events"],
                    "processes": processes, "final": final, "idempotency_result": "transaction rolled back"})

    report = {"postgres_version": engine.dialect.server_version_info, "scenarios": reports,
              "passed": all(r["passed"] for r in reports)}
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--lock-worker", action="store_true")
    parser.add_argument("--user")
    parser.add_argument("--operation")
    parser.add_argument("--payload")
    parser.add_argument("--at", type=float, default=0)
    args = parser.parse_args()
    if args.worker:
        raise SystemExit(worker(args))
    if args.lock_worker:
        raise SystemExit(lock_worker(args))
    raise SystemExit(main())
