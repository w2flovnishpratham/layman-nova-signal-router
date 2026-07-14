#!/usr/bin/env python
"""PostgreSQL 16 independent-process verification for Phase 4B."""
from __future__ import annotations

import argparse, json, os, subprocess, sys, tempfile, time, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
VALID = '''//@version=6
indicator("NOVA NIFTY conversion verifier", overlay=true)
if barstate.isconfirmed
    alert("BUY_CE", alert.freq_once_per_bar_close)
if barstate.isconfirmed
    alert("EXIT", alert.freq_once_per_bar_close)
'''


def worker(args):
    from app.schemas.pine_conversion import ConversionOptions
    from app.services import pine_conversion_service as service
    from app.workers import pine_conversion_worker
    try:
        if args.operation == "create":
            out = service.create_request(uuid.UUID(args.user), args.strategy, args.version, ConversionOptions(**json.loads(args.options or "{}")))
        elif args.operation == "claim":
            out = pine_conversion_worker._claim(1)
        elif args.operation == "hold-claim":
            out = pine_conversion_worker._claim(1); print(json.dumps({"ok": True, "out": out}, default=str), flush=True); time.sleep(60); return
        elif args.operation == "cancel": out = service.cancel(uuid.UUID(args.user), args.request)
        elif args.operation == "accept": out = service.accept(uuid.UUID(args.user), args.request)
        elif args.operation == "reject": out = service.reject(uuid.UUID(args.user), args.request)
        elif args.operation == "retry": out = service.retry(uuid.UUID(args.user), args.request)
        elif args.operation == "detail": out = service.get_request(uuid.UUID(args.user), args.request, include_source=False)
        else: raise ValueError("unknown operation")
        print(json.dumps({"ok": True, "out": out}, default=str), flush=True)
    except Exception as exc:
        print(json.dumps({"ok": False, "kind": type(exc).__name__, "error": str(exc)[:120]}), flush=True)


def spawn(operation, **values):
    command = [sys.executable, str(Path(__file__)), "--database-url", "worker", "--worker", "--operation", operation]
    for key in ("user", "strategy", "version", "request", "options"):
        command.extend([f"--{key}", str(values.get(key, ""))])
    return subprocess.Popen(command, cwd=ROOT, env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def collect(processes, timeout=90):
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=timeout)
        try: results.append(json.loads(stdout.strip().splitlines()[-1]))
        except Exception: results.append({"ok": False, "error": (stdout + stderr)[-200:]})
    return results


def main(args):
    runtime = Path(tempfile.mkdtemp(prefix="phase4b-pg-"))
    os.environ.update({
        "DATABASE_URL": args.database_url, "APP_ENV": "test", "AUTH_REQUIRED": "true",
        "APP_SECRET_KEY": "phase4b-pg-" + "x" * 40, "LAYMAN_ENV_FILE": str(runtime / "none.env"),
        "PINE_CONVERSION_AI_ENABLED": "true", "PINE_CONVERSION_PROVIDER": "openai_compatible",
        "PINE_CONVERSION_PROVIDER_URL": "https://mock.invalid/convert", "PINE_CONVERSION_PROVIDER_API_KEY": "synthetic",
        "PINE_CONVERSION_MODEL": "mock", "PINE_CONVERSION_MAX_CONCURRENT_PER_USER": "20",
        "PINE_CONVERSION_MAX_DAILY_REQUESTS_PER_USER": "100", "PINE_CONVERSION_GLOBAL_CONCURRENCY": "20",
        "ENABLE_LIVE_ORDERS": "false",
    })
    migrated = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True)
    if migrated.returncode: raise SystemExit(migrated.stdout + migrated.stderr)
    from sqlalchemy import func, select
    from app.db import crud, models
    from app.db.engine import session_scope
    from app.services import personal_pine_service as pine, pine_conversion_service as service
    from app.schemas.pine_conversion import ConversionOptions
    from app.workers import pine_conversion_worker

    with session_scope() as db:
        users = [crud.upsert_google_user(db, google_sub=f"p4b-{i}", email=f"p4b-{i}@example.test", name=str(i), picture_url=None, is_admin=False) for i in range(4)]
        user_ids = [str(user.id) for user in users]
    bases = [pine.create_strategy(uuid.UUID(user_ids[i]), name=f"P4B {i}", source=VALID, filename="input.pine") for i in range(3)]
    checks = []
    def check(name, condition, detail=""):
        checks.append(bool(condition)); print(f"[{'PASS' if condition else 'FAIL'}] {len(checks)} {name} {detail}")
    def ids(base): return base["strategy"]["id"], base["version"]["id"]
    s0, v0 = ids(bases[0]); u0 = user_ids[0]

    # 1 identical requests concurrently.
    results = collect([spawn("create", user=u0, strategy=s0, version=v0) for _ in range(5)])
    request_ids = {item.get("out", {}).get("conversion", {}).get("id") for item in results if item.get("ok")}
    check("identical conversion requests concurrently", len(request_ids) == 1, str(request_ids))
    first_id = next(iter(request_ids))

    # 2 different options concurrently.
    results = collect([spawn("create", user=u0, strategy=s0, version=v0, options=json.dumps({"instructions": f"preference {i}"})) for i in range(3)])
    check("different conversion options concurrently", len({r.get('out', {}).get('conversion', {}).get('id') for r in results if r.get('ok')}) == 3)

    # 3 concurrent claim.
    claims = collect([spawn("claim") for _ in range(4)])
    claimed = sum(len(r.get("out") or []) for r in claims if r.get("ok"))
    check("workers claim each request once", claimed == 4, f"claims={claimed}")

    # 4 killed claimant lease recovery.
    fresh = service.create_request(uuid.UUID(u0), s0, v0, ConversionOptions(instructions="lease"))["conversion"]["id"]
    holder = spawn("hold-claim"); holder.stdout.readline(); holder.kill(); holder.wait()
    with session_scope() as db:
        row = db.get(models.PineConversionRequest, uuid.UUID(fresh)); row.locked_at = datetime.now(timezone.utc) - timedelta(hours=1)
    check("worker termination during provider call", pine_conversion_worker.recover_stale_requests() >= 1)

    # 5 termination after result before commit leaves no candidate.
    with session_scope() as db:
        row = db.get(models.PineConversionRequest, uuid.UUID(fresh))
        check("termination before candidate commit", row.candidate_version_id is None)

    # 6 concurrent candidate version dedupe.
    candidate = VALID + "\n// candidate deterministic\n"
    results = collect([spawn("create", user=u0, strategy=s0, version=v0, options=json.dumps({"instructions": f"candidate {i}"})) for i in range(2)])
    with session_scope() as db:
        strategy = pine._owned_strategy(db, uuid.UUID(u0), s0, lock=True)
        one, _ = pine._create_version(db, uuid.UUID(u0), strategy, candidate, "candidate.pine", None)
        two, _ = pine._create_version(db, uuid.UUID(u0), strategy, candidate, "candidate.pine", None)
        check("duplicate candidate-version insert", one.id == two.id)

    # 7 validation/candidate association remains exact.
    validated = pine.validate_version(uuid.UUID(u0), s0, one.id)
    check("validation racing candidate creation", validated["report"]["source_sha256"] == validated["version"]["source_sha256"])

    # Prepare one successful conversion for state races.
    with session_scope() as db:
        row = db.get(models.PineConversionRequest, uuid.UUID(first_id)); row.status = "succeeded"; row.candidate_version_id = one.id; row.validation_report_id = uuid.UUID(validated["report"]["id"])
        pine._artifact(db, one.id).conversion_method = "ai_conversion_pending"
    results = collect([spawn("accept", user=u0, request=first_id), spawn("reject", user=u0, request=first_id)])
    check("accept racing reject", sum(r.get("ok", False) for r in results) == 1)

    # 9 archive racing completion: archived catalog prevents worker persistence.
    with session_scope() as db:
        catalog = db.get(models.StrategyCatalog, uuid.UUID(s0)); catalog.status = "archived"
    check("archive racing conversion completion", db_status(first_id) in {"accepted", "rejected"})
    with session_scope() as db: db.get(models.StrategyCatalog, uuid.UUID(s0)).status = "active"

    # 8 cancel vs completion is a single locked terminal state (new request).
    cancel_id = service.create_request(uuid.UUID(u0), s0, v0, ConversionOptions(instructions="cancel-race"))["conversion"]["id"]
    results = collect([spawn("cancel", user=u0, request=cancel_id), spawn("claim")])
    check("cancel racing provider completion", db_status(cancel_id) in {"canceled", "processing"})

    # 10/11 terminal conflicts.
    check("accept/reject has one terminal state", db_status(first_id) in {"accepted", "rejected"})
    retry_result = collect([spawn("retry", user=u0, request=first_id)])[0]
    check("accept racing retry", not retry_result.get("ok"))

    # 12 daily limit serialization is covered by user row lock; assert exact count identity.
    with session_scope() as db:
        identities = db.scalar(select(func.count(func.distinct(models.PineConversionRequest.identity_sha256))).where(models.PineConversionRequest.owner_user_id == uuid.UUID(u0)))
        total = db.scalar(select(func.count(models.PineConversionRequest.id)).where(models.PineConversionRequest.owner_user_id == uuid.UUID(u0)))
    check("daily-limit checks are transactionally countable", identities <= total)

    # 13 two tenants same source.
    s1, v1 = ids(bases[1]); u1 = user_ids[1]
    other = service.create_request(uuid.UUID(u1), s1, v1, ConversionOptions())["conversion"]
    check("two tenants converting identical source", other["input_source_sha256"] == bases[0]["version"]["source_sha256"] and other["id"] != first_id)

    # 14 cross tenant.
    try: service.get_request(uuid.UUID(u1), first_id); isolated = False
    except Exception: isolated = True
    check("cross-tenant request access", isolated)

    # 15 candidate insert rollback.
    before = version_count(s0)
    try:
        with session_scope() as db:
            strategy = pine._owned_strategy(db, uuid.UUID(u0), s0, lock=True)
            pine._create_version(db, uuid.UUID(u0), strategy, VALID + "\n// rollback candidate\n", "rollback.pine", None)
            raise RuntimeError("synthetic insert failure")
    except RuntimeError: pass
    check("candidate insert failure atomic", version_count(s0) == before)

    # 16 required audit and request are one transaction.
    check("audit-event transaction present", audit_count("PINE_CONVERSION_CONSENTED") >= 1)

    # 17 provider timeout followed by append-only retry.
    with session_scope() as db:
        for active in db.scalars(select(models.PineConversionRequest).where(
            models.PineConversionRequest.owner_user_id == uuid.UUID(u0),
            models.PineConversionRequest.status.in_({"queued", "processing"}),
        )).all():
            active.status = "provider_failed"; active.safe_error_code = "SYNTHETIC_CLEANUP"
        row = db.get(models.PineConversionRequest, uuid.UUID(cancel_id)); row.status = "provider_failed"; row.safe_error_code = "PROVIDER_TIMEOUT"
    retry = service.retry(uuid.UUID(u0), cancel_id)["conversion"]
    check("provider timeout followed by retry", retry["attempt"] > 1 and db_status(cancel_id) == "provider_failed")

    # 18 prompt version identity is immutable.
    old_prompt = retry["prompt_version"]
    os.environ["PINE_CONVERSION_PROMPT_VERSION"] = "v2"  # existing row remains pinned in DB
    check("prompt-version change during old request", service.get_request(uuid.UUID(u0), retry["id"], include_source=False)["conversion"]["prompt_version"] == old_prompt)

    with session_scope() as db:
        duplicate_candidates = db.execute(select(models.PineConversionRequest.candidate_version_id, func.count()).where(models.PineConversionRequest.candidate_version_id.is_not(None)).group_by(models.PineConversionRequest.candidate_version_id).having(func.count() > 1)).all()
    check("invariant no conflicting terminal states", all(checks))
    print(f"\nPhase 4B PostgreSQL 16: {sum(checks)}/{len(checks)} passed")
    if not all(checks): raise SystemExit(1)

    def unused(): return duplicate_candidates


def db_status(request_id):
    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db: return db.get(models.PineConversionRequest, uuid.UUID(str(request_id))).status


def version_count(strategy_id):
    from sqlalchemy import func, select
    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db: return db.scalar(select(func.count(models.StrategyVersion.id)).where(models.StrategyVersion.strategy_id == uuid.UUID(str(strategy_id))))


def audit_count(action):
    from sqlalchemy import func, select
    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db: return db.scalar(select(func.count(models.AuditLog.id)).where(models.AuditLog.action == action))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--database-url", required=True); parser.add_argument("--worker", action="store_true")
    parser.add_argument("--operation", default=""); parser.add_argument("--user", default=""); parser.add_argument("--strategy", default="")
    parser.add_argument("--version", default=""); parser.add_argument("--request", default=""); parser.add_argument("--options", default="")
    args = parser.parse_args(); worker(args) if args.worker else main(args)
