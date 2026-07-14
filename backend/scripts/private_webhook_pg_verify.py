#!/usr/bin/env python
"""Phase 3A PostgreSQL 16 concurrency verification for private webhooks.

Runs the ingestion + worker paths against a real PostgreSQL 16 database using
INDEPENDENT OS processes (one engine/connection pool each) to prove the
durable unique constraints — not in-process locks — are what prevent duplicate
jobs and duplicate orders.

Usage:
    python scripts/private_webhook_pg_verify.py --database-url postgresql://...

Requires: PostgreSQL 16 reachable at --database-url. Paper mode only.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE_CSV = ROOT / "scripts" / "fixtures" / "phase3a_scrip_master.csv"


def _base_env(database_url: str, runtime_dir: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": database_url,
            "APP_ENV": "test",
            "AUTH_REQUIRED": "true",
            "APP_SECRET_KEY": "phase3a-verify-secret-" + "x" * 32,
            "ENABLE_TEST_MARKET_DATA_PROVIDER": "true",
            "DHAN_MODE": "MOCK",
            "ENABLE_LIVE_ORDERS": "false",
            "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED": "true",
            "STRATEGY_JOB_WORKER_ENABLED": "true",
            "POSITION_DB_TYPED_WRITES_ENABLED": "true",
            "POSITION_DB_SHADOW_WRITE_ENABLED": "true",
            "RUNTIME_STATE_DIR": str(Path(runtime_dir) / "state"),
            "RUNTIME_LOG_DIR": str(Path(runtime_dir) / "logs"),
            "DHAN_SCRIP_MASTER_PATH": str(FIXTURE_CSV),
            "LAYMAN_ENV_FILE": str(Path(runtime_dir) / "nonexistent.env"),
        }
    )
    return env


def write_fixture_csv() -> None:
    today = datetime.now(timezone.utc).astimezone().date()
    near = today + timedelta(days=2)
    far = today + timedelta(days=9)
    rows = [
        "SEM_INSTRUMENT_NAME,SEM_SEGMENT,EXCH_ID,UNDERLYING_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_LOT_UNITS,SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL"
    ]
    security = 21000
    for expiry in (near, far):
        for strike in (50, 100, 150):
            for side in ("CE", "PE"):
                security += 1
                rows.append(
                    f"OPTIDX,D,NSE,NIFTY,{expiry.isoformat()},{strike},{side},65,{security},NIFTY P3A {expiry.isoformat()} {strike} {side}"
                )
    FIXTURE_CSV.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_CSV.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _patch_market_open() -> None:
    import importlib

    for module_name in (
        "app.services.risk_manager",
        "app.services.dhan_client",
        "app.services.execution_router",
        "app.services.atm_ltp_service",
        "app.services.paper_broker",
    ):
        module = importlib.import_module(module_name)
        if hasattr(module, "_market_is_open"):
            module._market_is_open = lambda: True


# ---------------------------------------------------------------------------
# Subprocess worker entrypoints
# ---------------------------------------------------------------------------

def worker_ingest(args) -> None:
    """Independent process: authenticate + ingest one payload."""
    from app.services import private_webhook_service as service

    payload_data = json.loads(args.payload)
    try:
        auth = service.authenticate_credential(args.token)
        service.validate_instance_lifecycle(auth)
        payload = service.parse_payload(payload_data)
        result = service.ingest(auth, payload)
        print(json.dumps({"ok": True, "result": result}))
    except service.PrivateWebhookError as exc:
        print(json.dumps({"ok": False, "reason": exc.reason, "status_code": exc.status_code}))


def worker_run_jobs(args) -> None:
    """Independent process: claim + execute queued jobs (paper)."""
    _patch_market_open()
    from app.workers.strategy_job_worker import process_queued_jobs_once, recover_stale_jobs

    recovered = recover_stale_jobs()
    total = 0
    for _ in range(int(args.rounds)):
        total += process_queued_jobs_once(limit=10)
    print(json.dumps({"ok": True, "processed": total, "recovered": recovered}))


# ---------------------------------------------------------------------------
# Parent-side helpers
# ---------------------------------------------------------------------------

def _setup_schema() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


def _make_user_instance(email: str, *, lots: int = 1) -> tuple[str, str, str]:
    from app.db import crud, models
    from app.db.engine import session_scope
    from app.services import strategy_instance_service

    with session_scope() as db:
        user = crud.upsert_google_user(
            db, google_sub=f"sub-{email}", email=email, name="Verify", picture_url=None, is_admin=False
        )
        strategy = models.StrategyCatalog(
            code=f"pine-{uuid.uuid4().hex[:10]}", display_name="Verify Pine",
            owner_type="personal", owner_user_id=user.id, status="active",
        )
        db.add(strategy)
        db.flush()
        version = models.StrategyVersion(
            strategy_id=strategy.id, version="1.0", payload_spec_version="nova.v1",
            source_journey="personal_tradingview", status="approved", execution_kind="external_webhook",
        )
        db.add(version)
        db.flush()
        instance = models.StrategyInstance(
            user_id=user.id, strategy_id=strategy.id, strategy_version_id=version.id,
            source_journey="PERSONAL_TRADINGVIEW", label=f"verify-{uuid.uuid4().hex[:6]}",
            status="active", execution_mode="paper_live_data", current_lots=lots,
        )
        db.add(instance)
        db.flush()
        user_id, instance_id = str(user.id), str(instance.id)
    token = strategy_instance_service.generate_webhook_credential(uuid.UUID(user_id), instance_id)["token"]
    return user_id, instance_id, token


def _start_paper_engine(user_id: str) -> None:
    from app.db import crud
    from app.db.engine import session_scope
    from app.services import state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    with session_scope() as db:
        user = crud.get_user_by_id(db, user_id)
        current = current_user_from_model(user)
    with bind_user_execution_context(current):
        state_store.init_runtime_files()
        state_store.update_app_state(engine_mode="paper", engine_started=True, webhook_trading_enabled=True)
        state_store.update_runtime_settings(
            allow_entry=True, allow_exit=True, marketfeed_ws_enabled=False,
            option_ltp_source="REST", emergency_stop=False, global_kill_switch=False,
        )


def _position(user_id: str) -> dict:
    from app.db import crud
    from app.db.engine import session_scope
    from app.services import state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    with session_scope() as db:
        user = crud.get_user_by_id(db, user_id)
        current = current_user_from_model(user)
    with bind_user_execution_context(current):
        return state_store.get_open_position()


def _job_stats(instance_id: str) -> dict:
    from sqlalchemy import select

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        jobs = db.scalars(
            select(models.StrategyExecutionJob).where(
                models.StrategyExecutionJob.strategy_name == f"instance:{instance_id}"
            )
        ).all()
        return {
            "count": len(jobs),
            "by_status": sorted((job.signal_id, job.status) for job in jobs),
            "results": {job.signal_id: job.result_summary for job in jobs},
        }


def _spawn_ingest(token: str, payload: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(Path(__file__)), "--worker", "ingest", "--token", token,
         "--payload", json.dumps(payload)],
        cwd=str(ROOT), env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _collect(procs: list[subprocess.Popen]) -> list[dict]:
    outputs = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=180)
        line = stdout.strip().splitlines()[-1] if stdout.strip() else "{}"
        try:
            outputs.append(json.loads(line))
        except json.JSONDecodeError:
            outputs.append({"ok": False, "raw": stdout[-500:], "stderr": stderr[-500:]})
    return outputs


def _run_worker_subprocess(rounds: int = 3) -> dict:
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__)), "--worker", "run-jobs", "--rounds", str(rounds)],
        cwd=str(ROOT), env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return _collect([proc])[0]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _payload(action: str, signal_id: str) -> dict:
    return {"action": action, "signal_id": signal_id, "signal_time": _now_iso()}


CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def scenario_concurrent_identical() -> None:
    print("\n[1] Same signal delivered concurrently (6 independent processes)")
    _, instance_id, token = _make_user_instance("pgv-identical@example.test")
    payload = _payload("BUY_CE", "race-identical")
    procs = [_spawn_ingest(token, payload) for _ in range(6)]
    outputs = _collect(procs)
    queued = [o for o in outputs if o.get("ok") and o["result"].get("status") == "QUEUED"]
    duplicates = [o for o in outputs if o.get("ok") and o["result"].get("duplicate")]
    stats = _job_stats(instance_id)
    check("exactly one QUEUED acceptance", len(queued) == 1, f"queued={len(queued)} dup={len(duplicates)}")
    check("exactly one execution job", stats["count"] == 1, str(stats["by_status"]))


def scenario_concurrent_conflicting() -> None:
    print("\n[2] Concurrent conflicting payloads (same signal id, different action)")
    _, instance_id, token = _make_user_instance("pgv-conflict@example.test")
    signal_time = _now_iso()
    procs = []
    for index in range(6):
        payload = {"action": "BUY_CE" if index % 2 == 0 else "BUY_PE",
                   "signal_id": "race-conflict", "signal_time": signal_time}
        procs.append(_spawn_ingest(token, payload))
    outputs = _collect(procs)
    accepted = [o for o in outputs if o.get("ok") and o["result"].get("status") == "QUEUED"]
    stats = _job_stats(instance_id)
    check("exactly one job despite conflicts", stats["count"] == 1, str(stats["by_status"]))
    check("one acceptance, rest duplicate/conflict", len(accepted) == 1,
          f"accepted={len(accepted)} outcomes={[o.get('reason') or o.get('result', {}).get('status') for o in outputs]}")


def scenario_opposite_and_exit_race() -> None:
    print("\n[3] Opposite signals + EXIT racing BUY_PE (per-user serialized worker)")
    user_id, instance_id, token = _make_user_instance("pgv-opposite@example.test")
    _start_paper_engine(user_id)

    # Open CE first.
    procs = [_spawn_ingest(token, _payload("BUY_CE", "opp-1"))]
    _collect(procs)
    worker = _run_worker_subprocess()
    check("initial CE entry executed", worker.get("ok") is True, json.dumps(worker))
    position = _position(user_id)
    check("CE open", position.get("option_side") == "CE" and position.get("has_open_position") is True)

    # EXIT and BUY_PE delivered concurrently (independent processes).
    procs = [
        _spawn_ingest(token, _payload("EXIT", "opp-exit")),
        _spawn_ingest(token, _payload("BUY_PE", "opp-2")),
    ]
    _collect(procs)
    _run_worker_subprocess(rounds=4)
    stats = _job_stats(instance_id)
    position = _position(user_id)
    valid_end = (position.get("has_open_position") is False) or (position.get("option_side") == "PE")
    check("valid final state (flat or PE), never double-open", valid_end,
          f"open={position.get('has_open_position')} side={position.get('option_side')}")
    check("all jobs reached terminal state", all(s in {"completed", "failed"} for _, s in stats["by_status"]),
          str(stats["by_status"]))

    from sqlalchemy import select, func
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        active = db.scalar(
            select(func.count(models.StrategyInstancePosition.id)).where(
                models.StrategyInstancePosition.user_id == uuid.UUID(user_id),
                models.StrategyInstancePosition.position_state.in_(models.ACTIVE_POSITION_STATES),
            )
        )
    check("at most one active PG position row (partial unique index)", int(active or 0) <= 1, f"active={active}")


def scenario_revoked_mid_flight() -> None:
    print("\n[4] Credential revoked between ingestion and execution")
    user_id, instance_id, token = _make_user_instance("pgv-revoked@example.test")
    _start_paper_engine(user_id)
    _collect([_spawn_ingest(token, _payload("BUY_CE", "revoke-1"))])
    from app.services import strategy_instance_service

    strategy_instance_service.revoke_webhook_credential(uuid.UUID(user_id), instance_id)
    rejected = _collect([_spawn_ingest(token, _payload("BUY_CE", "revoke-2"))])[0]
    check("post-revocation webhook rejected", rejected.get("reason") == "INVALID_CREDENTIAL", json.dumps(rejected))
    _run_worker_subprocess()
    stats = _job_stats(instance_id)
    check("pre-revocation job still executed exactly once",
          stats["by_status"] == [("revoke-1", "completed")], str(stats["by_status"]))


def scenario_stale_lease_recovery() -> None:
    print("\n[5] Worker terminated while holding a job lease")
    user_id, instance_id, token = _make_user_instance("pgv-lease@example.test")
    _start_paper_engine(user_id)
    _collect([_spawn_ingest(token, _payload("BUY_CE", "lease-1"))])

    from datetime import timedelta as _td

    from sqlalchemy import update
    from app.db import models
    from app.db.engine import session_scope

    # Simulate a worker crash mid-lease: job left "running" with a stale lock.
    with session_scope() as db:
        db.execute(
            update(models.StrategyExecutionJob)
            .where(models.StrategyExecutionJob.strategy_name == f"instance:{instance_id}")
            .values(status="running", attempts=1,
                    locked_at=datetime.now(timezone.utc) - _td(seconds=3600))
        )
    result = _run_worker_subprocess()
    stats = _job_stats(instance_id)
    status = stats["by_status"][0][1]
    # Money-mode retry after a possible broker call must fail safe, never re-run.
    check("recovered stale money-mode job fails safe (no blind broker retry)",
          status == "failed", f"status={status} worker={json.dumps(result)}")
    position = _position(user_id)
    check("no position from the failed retry", position.get("has_open_position") is False)


def scenario_two_users_concurrent() -> None:
    print("\n[6] Two tenants executing concurrently")
    user_a, instance_a, token_a = _make_user_instance("pgv-user-a@example.test", lots=1)
    user_b, instance_b, token_b = _make_user_instance("pgv-user-b@example.test", lots=2)
    _start_paper_engine(user_a)
    _start_paper_engine(user_b)
    procs = [
        _spawn_ingest(token_a, _payload("BUY_CE", "two-a")),
        _spawn_ingest(token_b, _payload("BUY_PE", "two-b")),
    ]
    _collect(procs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: _run_worker_subprocess(rounds=2), range(2)))
    position_a = _position(user_a)
    position_b = _position(user_b)
    check("tenant A holds its own CE with its own lots",
          position_a.get("option_side") == "CE" and position_a.get("qty") == 65,
          f"side={position_a.get('option_side')} qty={position_a.get('qty')}")
    check("tenant B holds its own PE with its own lots",
          position_b.get("option_side") == "PE" and position_b.get("qty") == 130,
          f"side={position_b.get('option_side')} qty={position_b.get('qty')}")
    stats_a, stats_b = _job_stats(instance_a), _job_stats(instance_b)
    check("no cross-tenant jobs", stats_a["count"] == 1 and stats_b["count"] == 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("PHASE3A_PG_URL", ""))
    parser.add_argument("--worker", choices=["ingest", "run-jobs"], default=None)
    parser.add_argument("--token", default="")
    parser.add_argument("--payload", default="{}")
    parser.add_argument("--rounds", default="3")
    args = parser.parse_args()

    if args.worker == "ingest":
        worker_ingest(args)
        return
    if args.worker == "run-jobs":
        worker_run_jobs(args)
        return

    if not args.database_url:
        raise SystemExit("--database-url (or PHASE3A_PG_URL) is required.")

    runtime_dir = tempfile.mkdtemp(prefix="phase3a-pgverify-")
    os.environ.update(_base_env(args.database_url, runtime_dir))
    write_fixture_csv()
    _setup_schema()
    _patch_market_open()

    from app.db.engine import get_engine

    dialect = get_engine().dialect
    with get_engine().connect() as connection:
        server_version = connection.exec_driver_sql("show server_version").scalar()
    print(f"PostgreSQL server version: {server_version} (dialect={dialect.name})")

    scenario_concurrent_identical()
    scenario_concurrent_conflicting()
    scenario_opposite_and_exit_race()
    scenario_revoked_mid_flight()
    scenario_stale_lease_recovery()
    scenario_two_users_concurrent()

    failed = [name for name, passed, _ in CHECKS if not passed]
    print(f"\n{'=' * 60}\nChecks: {len(CHECKS)}  Passed: {len(CHECKS) - len(failed)}  Failed: {len(failed)}")
    if failed:
        for name in failed:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("ALL POSTGRESQL CONCURRENCY CHECKS PASSED")


if __name__ == "__main__":
    main()
