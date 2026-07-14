#!/usr/bin/env python
"""Phase 3A local end-to-end soak: private strategy webhooks.

Topology (all local, paper-only):
  * PostgreSQL 16 (Docker) — durable signals/jobs/positions/events
  * Real uvicorn process serving app.main:app (+ soak engine helper route)
  * Real separate durable worker process (scripts/private_webhook_soak_worker)
  * Two authenticated users, two PERSONAL_TRADINGVIEW instances, private
    webhook credentials, per-user paper engines, deterministic test market
    data (ENABLE_TEST_MARKET_DATA_PROVIDER), typed PG writes + read shadow on

Safety: ENABLE_LIVE_ORDERS=false, DHAN_MODE=MOCK, JSON position authority.

Usage:
    python scripts/private_webhook_soak.py --database-url postgresql://...
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PORT = 18801
BASE = f"http://127.0.0.1:{PORT}"
FIXTURE_CSV = ROOT / "scripts" / "fixtures" / "phase3a_scrip_master.csv"

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""), flush=True)


def http(method: str, path: str, *, cookie: str | None = None, body: dict | None = None,
         raw_body: bytes | None = None, content_type: str = "application/json") -> tuple[int, dict]:
    data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", content_type)
    if cookie:
        request.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode()
            return response.status, (json.loads(payload) if payload else {})
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, {"raw": payload[:300]}
    except (urllib.error.URLError, ConnectionError, OSError):
        return 0, {}


def now_iso(offset: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def alert(action: str, signal_id: str, **extra) -> dict:
    return {"action": action, "signal_id": signal_id, "signal_time": now_iso(), **extra}


def wait_for(predicate, *, timeout: float = 25.0, interval: float = 0.4, label: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(f"Timed out waiting for {label}")


def write_fixture_csv() -> None:
    today = datetime.now(timezone.utc).astimezone().date()
    near = today + timedelta(days=2)
    far = today + timedelta(days=9)
    rows = [
        "SEM_INSTRUMENT_NAME,SEM_SEGMENT,EXCH_ID,UNDERLYING_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_LOT_UNITS,SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL"
    ]
    security = 31000
    for expiry in (near, far):
        for strike in (50, 100, 150):
            for side in ("CE", "PE"):
                security += 1
                rows.append(
                    f"OPTIDX,D,NSE,NIFTY,{expiry.isoformat()},{strike},{side},65,{security},NIFTY P3A {expiry.isoformat()} {strike} {side}"
                )
    FIXTURE_CSV.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_CSV.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("PHASE3A_SOAK_PG_URL", ""))
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("--database-url is required (fresh PostgreSQL 16 database).")

    runtime_dir = tempfile.mkdtemp(prefix="phase3a-soak-")
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": args.database_url,
            "APP_ENV": "test",
            "AUTH_REQUIRED": "true",
            "APP_SECRET_KEY": "phase3a-soak-secret-" + "s" * 32,
            "ENABLE_TEST_MARKET_DATA_PROVIDER": "true",
            "DHAN_MODE": "MOCK",
            "ENABLE_LIVE_ORDERS": "false",
            "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED": "true",
            "PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED": "false",
            "STRATEGY_JOB_WORKER_ENABLED": "true",
            "BACKGROUND_WORKER_RUNNER_ENABLED": "false",  # worker runs as its own process
            "POSITION_DB_TYPED_WRITES_ENABLED": "true",
            "POSITION_DB_SHADOW_WRITE_ENABLED": "true",
            "POSITION_DB_READ_SHADOW_ENABLED": "true",  # observational only
            "RUNTIME_STATE_DIR": str(Path(runtime_dir) / "state"),
            "RUNTIME_LOG_DIR": str(Path(runtime_dir) / "logs"),
            "DHAN_SCRIP_MASTER_PATH": str(FIXTURE_CSV),
            "LAYMAN_ENV_FILE": str(Path(runtime_dir) / "nonexistent.env"),
            "WEBHOOK_TRADING_ENABLED": "false",
        }
    )
    os.environ.update(env)
    write_fixture_csv()

    print("== Migrations: alembic upgrade head on PostgreSQL 16 ==", flush=True)
    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )
    if migrate.returncode != 0:
        raise SystemExit(f"alembic upgrade head failed:\n{migrate.stdout}\n{migrate.stderr}")
    check("alembic upgrade head on PG16", True)

    # ------------------------------------------------------------------
    # Seed: two users + sessions + one approved external-webhook catalog row.
    # ------------------------------------------------------------------
    from app.auth.session import sign_session_id
    from app.config import settings
    from app.db import crud, models
    from app.db.engine import session_scope

    def make_user(email: str) -> tuple[str, str]:
        with session_scope() as db:
            user = crud.upsert_google_user(
                db, google_sub=f"sub-{email}", email=email, name="Soak", picture_url=None, is_admin=False
            )
            session = crud.create_session(db, user_id=user.id, ttl_seconds=7200)
            return str(user.id), f"{settings.SESSION_COOKIE_NAME}={sign_session_id(session.id)}"

    def make_catalog() -> str:
        with session_scope() as db:
            strategy = models.StrategyCatalog(
                code=f"pine-{uuid.uuid4().hex[:10]}", display_name="Personal TradingView",
                owner_type="nova", owner_user_id=None, status="active",
            )
            db.add(strategy)
            db.flush()
            version = models.StrategyVersion(
                strategy_id=strategy.id, version="1.0", payload_spec_version="nova.v1",
                source_journey="personal_tradingview", status="approved", execution_kind="external_webhook",
            )
            db.add(version)
            db.flush()
            return strategy.code

    user_a, cookie_a = make_user("soak-user-a@example.test")
    user_b, cookie_b = make_user("soak-user-b@example.test")
    strategy_code = make_catalog()
    print(f"Users: A={user_a} B={user_b}", flush=True)

    # ------------------------------------------------------------------
    # Launch real uvicorn + real worker process.
    # ------------------------------------------------------------------
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "scripts.private_webhook_soak_app:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning",
         "--no-access-log"],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    def start_worker() -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "scripts/private_webhook_soak_worker.py"],
            cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

    worker = start_worker()

    def stop(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=15)

    try:
        def _server_up():
            if server.poll() is not None:
                stdout = server.stdout.read() if server.stdout else ""
                raise SystemExit(f"uvicorn exited early ({server.returncode}). Output:\n{stdout[-4000:]}")
            return http("GET", "/api/health")[0] == 200

        wait_for(_server_up, timeout=90, label="uvicorn health")
        check("real uvicorn process healthy", True)

        def webhook(token: str, body: dict) -> tuple[int, dict]:
            return http("POST", "/api/webhooks/private", body={"credential": token, **body})

        def executions(cookie: str, instance_id: str) -> list[dict]:
            status_code, payload = http(
                "GET", f"/api/strategy-instances/{instance_id}/webhook-executions", cookie=cookie
            )
            assert status_code == 200, payload
            return payload["executions"]

        def execution_status(cookie: str, instance_id: str, signal_id: str) -> dict | None:
            return next((e for e in executions(cookie, instance_id) if e["signal_id"] == signal_id), None)

        def wait_terminal(cookie: str, instance_id: str, signal_id: str) -> dict:
            def probe():
                entry = execution_status(cookie, instance_id, signal_id)
                if entry and entry.get("job_status") in {"completed", "failed", None} and entry.get("status") in {"completed", "completed_with_errors"}:
                    return entry
                return None

            return wait_for(probe, label=f"terminal job {signal_id}")

        def position(cookie: str) -> dict:
            status_code, payload = http("GET", "/__soak__/position", cookie=cookie)
            assert status_code == 200, payload
            return payload["position"]

        # Step 1: create both personal paper strategies through the owner API.
        status_code, created_a = http("POST", "/api/strategy-instances", cookie=cookie_a, body={
            "strategy_code": strategy_code, "source_journey": "PERSONAL_TRADINGVIEW",
            "label": "Soak A", "lots": 1, "execution_mode": "paper_live_data",
        })
        instance_a = created_a["instance"]["id"]
        status_b, created_b = http("POST", "/api/strategy-instances", cookie=cookie_b, body={
            "strategy_code": strategy_code, "source_journey": "PERSONAL_TRADINGVIEW",
            "label": "Soak B", "lots": 3, "execution_mode": "paper_live_data",
        })
        instance_b = created_b["instance"]["id"]
        check("step 1: two owner-scoped personal strategies created", status_code == 200 and status_b == 200)

        # Step 2: create webhook credentials via API.
        status_code, cred_a = http("POST", f"/api/strategy-instances/{instance_a}/webhook-credential", cookie=cookie_a, body={})
        check("step 2: credential issued for A", status_code == 200 and cred_a.get("ok"), str(status_code))
        token_a = cred_a["credential"]["token"]
        status_code, cred_b = http("POST", f"/api/strategy-instances/{instance_b}/webhook-credential", cookie=cookie_b, body={})
        token_b = cred_b["credential"]["token"]
        check("step 2: credential issued for B", status_code == 200)

        # Step 3: start paper engines.
        check("step 3: engine A started", http("POST", "/__soak__/engine", cookie=cookie_a, body={})[0] == 200)
        check("step 3: engine B started", http("POST", "/__soak__/engine", cookie=cookie_b, body={})[0] == 200)

        # Step 3B: safe HOLD connectivity tests, then readiness-gated activation.
        test_a = http("POST", f"/api/strategy-instances/{instance_a}/webhook-test", cookie=cookie_a, body={})
        test_b = http("POST", f"/api/strategy-instances/{instance_b}/webhook-test", cookie=cookie_b, body={})
        check("step 3: owner HOLD connection tests passed", test_a[0] == 200 and test_b[0] == 200)
        active_a = http("POST", f"/api/strategy-instances/{instance_a}/activate", cookie=cookie_a, body={})
        active_b = http("POST", f"/api/strategy-instances/{instance_b}/activate", cookie=cookie_b, body={})
        check("step 3: readiness-gated activation passed", active_a[0] == 200 and active_b[0] == 200)

        # Step 4: set engine lots via control API (A: 2 lots).
        status_code, lots_payload = http("POST", f"/api/strategy-instances/{instance_a}/lots", cookie=cookie_a, body={"lots": 2})
        check("step 4: lots set to 2 for A", status_code == 200 and lots_payload["instance"]["current_lots"] == 2)

        # Step 5-7: BUY_CE -> durable signal/job -> PaperBroker entry.
        first_alert = alert("BUY_CE", "soak-a-1")
        status_code, response = webhook(token_a, first_alert)
        check("step 5: BUY_CE accepted 202", status_code == 202, str(response))
        entry = wait_terminal(cookie_a, instance_a, "soak-a-1")
        check("step 6: signal + durable job completed", entry["job_status"] == "completed", json.dumps(entry.get("result")))
        result = entry.get("result") or {}
        order_id = (result.get("order_id") or "")
        check("step 7: PaperBroker entry order placed", order_id.startswith("PAPER-"), order_id)
        position_a = position(cookie_a)
        check("step 7: CE open with server lots (2 x 65 = 130)",
              position_a.get("option_side") == "CE" and position_a.get("qty") == 130,
              f"side={position_a.get('option_side')} qty={position_a.get('qty')}")

        # Steps 8-9: identical duplicate (same bytes, like a TradingView retry)
        # -> no duplicate order/job.
        status_code, response = webhook(token_a, first_alert)
        check("step 8: identical duplicate returns existing status", status_code == 200 and response.get("duplicate") is True, str(response))
        entries = [e for e in executions(cookie_a, instance_a) if e["signal_id"] == "soak-a-1"]
        check("step 9: still exactly one signal/job, one order", len(entries) == 1)

        # Steps 10-11: BUY_PE -> exit-before-reverse.
        status_code, _ = webhook(token_a, alert("BUY_PE", "soak-a-2"))
        check("step 10: BUY_PE accepted", status_code == 202)
        entry = wait_terminal(cookie_a, instance_a, "soak-a-2")
        execution_result = (entry.get("result") or {})
        check("step 11: reversal executed exit-before-reverse",
              execution_result.get("execution_status") == "REVERSAL_ORDER_PLACED", json.dumps(execution_result))
        position_a = position(cookie_a)
        check("step 11: PE now open", position_a.get("option_side") == "PE", str(position_a.get("option_side")))

        # Steps 12-13: HOLD -> audit only.
        status_code, response = webhook(token_a, alert("HOLD", "soak-a-3"))
        check("step 12: HOLD accepted as NO_OP", status_code == 202 and response.get("status") == "NO_OP", str(response))
        hold_entry = wait_for(lambda: execution_status(cookie_a, instance_a, "soak-a-3"), label="hold visible")
        check("step 13: HOLD created no job / no order", hold_entry.get("job_status") is None, json.dumps(hold_entry))

        # Steps 14-15: EXIT -> position closed.
        status_code, _ = webhook(token_a, alert("EXIT", "soak-a-4"))
        check("step 14: EXIT accepted", status_code == 202)
        wait_terminal(cookie_a, instance_a, "soak-a-4")
        position_a = position(cookie_a)
        check("step 15: position closed", position_a.get("has_open_position") is False)

        # Steps 16-17: revoke -> rejected.
        status_code, _ = http("DELETE", f"/api/strategy-instances/{instance_a}/webhook-credential", cookie=cookie_a)
        check("step 16: credential revoked", status_code == 200)
        status_code, response = webhook(token_a, alert("BUY_CE", "soak-a-5"))
        check("step 17: revoked credential rejected 401", status_code == 401, str(response))

        # Steps 18-20: new credential + rotation.
        status_code, cred_new = http("POST", f"/api/strategy-instances/{instance_a}/webhook-credential", cookie=cookie_a, body={})
        token_pre_rotate = cred_new["credential"]["token"]
        status_code, cred_rotated = http("POST", f"/api/strategy-instances/{instance_a}/webhook-credential/rotate", cookie=cookie_a, body={})
        token_rotated = cred_rotated["credential"]["token"]
        status_code, _ = webhook(token_pre_rotate, alert("HOLD", "soak-a-6"))
        check("step 19: pre-rotation credential rejected", status_code == 401)
        status_code, _ = webhook(token_rotated, alert("HOLD", "soak-a-6"))
        check("step 20: rotated credential works", status_code == 202)

        # Step 21: stale signal rejected.
        stale = {"action": "BUY_CE", "signal_id": "soak-a-stale", "signal_time": now_iso(-3600)}
        status_code, response = webhook(token_rotated, stale)
        check("step 21: stale signal rejected", status_code == 422 and response.get("reason") == "STALE_SIGNAL", str(response))

        # Step 22: forged server-authority fields rejected.
        forged_results = []
        for field, value in (("user_id", user_b), ("quantity", 9750), ("strike", 26000),
                             ("lots", 500), ("security_id", "13"), ("mode", "live"),
                             ("expiry", "2026-12-31"), ("event_source", "EOD")):
            status_code, _ = webhook(token_rotated, alert("BUY_CE", f"forge-{field}", **{field: value}))
            forged_results.append(status_code == 422)
        check("step 22: all forged authority fields rejected (0 accepted)", all(forged_results), str(forged_results))

        # Step 23: two users simultaneously.
        status_a = webhook(token_rotated, alert("BUY_CE", "soak-a-7"))
        status_b = webhook(token_b, alert("BUY_PE", "soak-b-1"))
        check("step 23: both tenants accepted", status_a[0] == 202 and status_b[0] == 202)
        wait_terminal(cookie_a, instance_a, "soak-a-7")
        wait_terminal(cookie_b, instance_b, "soak-b-1")
        position_a = position(cookie_a)
        position_b = position(cookie_b)
        check("step 23: tenant A CE (2 lots = 130)", position_a.get("option_side") == "CE" and position_a.get("qty") == 130,
              f"{position_a.get('option_side')}/{position_a.get('qty')}")
        check("step 23: tenant B PE (3 lots = 195)", position_b.get("option_side") == "PE" and position_b.get("qty") == 195,
              f"{position_b.get('option_side')}/{position_b.get('qty')}")

        # Step 24: worker restart after job persistence.
        stop(worker)
        status_code, _ = webhook(token_b, alert("EXIT", "soak-b-2"))
        check("step 24: signal accepted while worker is down", status_code == 202)
        time.sleep(2.0)
        pending = execution_status(cookie_b, instance_b, "soak-b-2")
        check("step 24: job persisted, not executed while worker down", pending and pending.get("job_status") == "queued",
              json.dumps(pending))
        worker = start_worker()
        wait_terminal(cookie_b, instance_b, "soak-b-2")
        position_b = position(cookie_b)
        check("step 24: restarted worker executed exactly once (B flat)", position_b.get("has_open_position") is False)

        # Close A as well for final parity.
        webhook(token_rotated, alert("EXIT", "soak-a-8"))
        wait_terminal(cookie_a, instance_a, "soak-a-8")

        # ------------------------------------------------------------------
        # Step 25: final parity + zero-findings audit.
        # ------------------------------------------------------------------
        from sqlalchemy import func, select, text

        with session_scope() as db:
            jobs = db.scalars(select(models.StrategyExecutionJob)).all()
            cross_tenant = [
                job for job in jobs
                if (job.strategy_name == f"instance:{instance_a}" and str(job.user_id) != user_a)
                or (job.strategy_name == f"instance:{instance_b}" and str(job.user_id) != user_b)
            ]
            duplicate_jobs = (
                db.execute(
                    select(models.StrategyExecutionJob.strategy_name, models.StrategyExecutionJob.signal_id,
                           func.count(models.StrategyExecutionJob.id))
                    .group_by(models.StrategyExecutionJob.strategy_name, models.StrategyExecutionJob.signal_id)
                    .having(func.count(models.StrategyExecutionJob.id) > 1)
                ).all()
            )
            non_terminal = [job.signal_id for job in jobs if job.status not in {"completed", "failed"}]
            active_positions = db.scalars(
                select(models.StrategyInstancePosition).where(
                    models.StrategyInstancePosition.position_state.in_(models.ACTIVE_POSITION_STATES)
                )
            ).all()
            event_count = db.scalar(select(func.count(models.PositionEvent.id)))
            persisted_text = ""
            for table in (
                "strategy_instance_webhook_credentials",
                "webhook_events",
                "strategy_signals",
                "strategy_execution_jobs",
                "audit_logs",
            ):
                rows = db.execute(text(f"SELECT * FROM {table}")).fetchall()
                persisted_text += json.dumps([tuple(map(str, row)) for row in rows])
            order_ids: list[str] = []
            for job in jobs:
                summary = job.result_summary or {}
                execution_result = summary.get("execution_result") or {}
                if isinstance(execution_result, dict) and execution_result.get("order_id"):
                    order_ids.append(str(execution_result["order_id"]))
                reversal = execution_result.get("reversal") if isinstance(execution_result, dict) else None
                if isinstance(reversal, dict):
                    exit_result = reversal.get("exit_result") or {}
                    if isinstance(exit_result, dict) and exit_result.get("order_id"):
                        order_ids.append(str(exit_result["order_id"]))

        check("0 cross-tenant executions", not cross_tenant, str(cross_tenant))
        check("0 duplicate execution jobs", not duplicate_jobs, str(duplicate_jobs))
        check("0 duplicate broker/PaperBroker order ids", len(order_ids) == len(set(order_ids)),
              f"{len(order_ids)} orders, {len(set(order_ids))} unique")
        check("0 live orders", all(order_id.startswith("PAPER-") for order_id in order_ids))
        check("all jobs terminal", not non_terminal, str(non_terminal))
        check("final parity: JSON flat AND 0 active PG position rows",
              position(cookie_a).get("has_open_position") is False
              and position(cookie_b).get("has_open_position") is False
              and len(active_positions) == 0,
              f"active_pg_rows={len(active_positions)}")
        check("typed PG position events were recorded (ledger active)", (event_count or 0) > 0, f"events={event_count}")
        check(
            "0 plaintext webhook credentials in PostgreSQL",
            all(token not in persisted_text for token in (token_a, token_b, token_pre_rotate, token_rotated)),
        )

        # Tenant status isolation via API.
        status_code, _ = http("GET", f"/api/strategy-instances/{instance_b}/webhook-executions", cookie=cookie_a)
        check("cross-user execution-status request rejected", status_code == 404, str(status_code))

        # Access logging is disabled and no application output may contain a
        # credential, including rejected and rotated values.
        stop(server)
        server_output = server.stdout.read() if server.stdout else ""
        check(
            "0 webhook credentials in captured Uvicorn/application logs",
            all(token not in server_output for token in (token_a, token_b, token_pre_rotate, token_rotated)),
        )

    finally:
        stop(worker)
        stop(server)

    failed = [name for name, passed, _ in CHECKS if not passed]
    print(f"\n{'=' * 64}\nSoak checks: {len(CHECKS)}  Passed: {len(CHECKS) - len(failed)}  Failed: {len(failed)}", flush=True)
    if failed:
        for name in failed:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("PHASE 3A/3B LOCAL SOAK PASSED", flush=True)


if __name__ == "__main__":
    main()
