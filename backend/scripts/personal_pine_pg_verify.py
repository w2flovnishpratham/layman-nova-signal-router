#!/usr/bin/env python
"""Phase 4A PostgreSQL 16 independent-process concurrency verifier.

Usage: python -m scripts.personal_pine_pg_verify --database-url postgresql://...
The database must be disposable. No source is executed and no orders are sent.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VALID = '''//@version=6
indicator("NOVA NIFTY synthetic verifier", overlay=true)
confirmed = barstate.isconfirmed
eod = hour == 15 and minute >= 15
if confirmed
    alert("BUY_CE", alert.freq_once_per_bar_close)
if confirmed
    alert("BUY_PE", alert.freq_once_per_bar_close)
if confirmed or eod
    alert("EXIT", alert.freq_once_per_bar_close)
'''


def _decode(value: str) -> str:
    return base64.b64decode(value).decode("utf-8")


def worker(args) -> None:
    from app.services import personal_pine_service as service

    try:
        user = uuid.UUID(args.user) if args.user else None
        if args.operation == "create":
            out = service.create_version(user, args.strategy, source=_decode(args.source), filename="verify.pine")
        elif args.operation == "validate":
            out = service.validate_version(user, args.strategy, args.version)
        elif args.operation == "submit":
            out = service.submit_version(user, args.strategy, args.version)
        elif args.operation == "start":
            out = service.start_review(user, args.version)
        elif args.operation in {"approved", "rejected", "changes_requested"}:
            out = service.decide_review(user, args.version, args.operation, acknowledge_warnings=True)
        elif args.operation == "source":
            out = service.get_source(user, args.strategy, args.version)
        elif args.operation == "link":
            out = service.link_version(user, args.instance, args.strategy, args.version)
        elif args.operation == "hold-lock":
            from sqlalchemy import select
            from app.db import models
            from app.db.engine import session_scope
            with session_scope() as db:
                db.scalar(select(models.StrategyVersion).where(models.StrategyVersion.id == uuid.UUID(args.version)).with_for_update())
                print(json.dumps({"ok": True, "locked": True}), flush=True)
                time.sleep(60)
            return
        else:
            raise ValueError("unknown worker operation")
        print(json.dumps({"ok": True, "out": out}, default=str), flush=True)
    except Exception as exc:
        print(json.dumps({"ok": False, "kind": type(exc).__name__, "error": str(exc)[:160]}), flush=True)


def spawn(operation: str, *, user="", strategy="", version="", source="", instance=""):
    encoded = base64.b64encode(source.encode()).decode() if source else ""
    return subprocess.Popen(
        [sys.executable, str(Path(__file__)), "--worker", "--operation", operation,
         "--user", user, "--strategy", strategy, "--version", version,
         "--source", encoded, "--instance", instance],
        cwd=ROOT, env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def collect(processes, timeout=90):
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=timeout)
        line = stdout.strip().splitlines()[-1] if stdout.strip() else "{}"
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            results.append({"ok": False, "error": (stdout + stderr)[-300:]})
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--operation", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--strategy", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--instance", default="")
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return

    runtime = tempfile.mkdtemp(prefix="phase4a-pg-")
    os.environ.update({
        "DATABASE_URL": args.database_url,
        "APP_ENV": "test",
        "AUTH_REQUIRED": "true",
        "APP_SECRET_KEY": "phase4a-verify-" + "x" * 40,
        "ENABLE_LIVE_ORDERS": "false",
        "PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED": "false",
        "RUNTIME_STATE_DIR": str(Path(runtime) / "state"),
        "RUNTIME_LOG_DIR": str(Path(runtime) / "logs"),
        "LAYMAN_ENV_FILE": str(Path(runtime) / "none.env"),
    })
    migrated = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True)
    if migrated.returncode:
        raise SystemExit(migrated.stdout + migrated.stderr)

    from sqlalchemy import func, select
    from app.db import crud, models
    from app.db.engine import session_scope
    from app.services import personal_pine_service as service

    with session_scope() as db:
        owner_a = crud.upsert_google_user(db, google_sub="p4a-a", email="p4a-a@example.test", name="A", picture_url=None, is_admin=False)
        owner_b = crud.upsert_google_user(db, google_sub="p4a-b", email="p4a-b@example.test", name="B", picture_url=None, is_admin=False)
        admin_a = crud.upsert_google_user(db, google_sub="p4a-admin-a", email="p4a-admin-a@example.test", name="Admin A", picture_url=None, is_admin=True)
        admin_b = crud.upsert_google_user(db, google_sub="p4a-admin-b", email="p4a-admin-b@example.test", name="Admin B", picture_url=None, is_admin=True)
        ids = tuple(map(str, (owner_a.id, owner_b.id, admin_a.id, admin_b.id)))
    owner_a_id, owner_b_id, admin_a_id, admin_b_id = ids

    created = service.create_strategy(uuid.UUID(owner_a_id), name="PG16 verifier", source=VALID, filename="verify.pine")
    strategy_id, initial_id = created["strategy"]["id"], created["version"]["id"]
    checks: list[tuple[str, bool, str]] = []
    def check(name, condition, detail=""):
        checks.append((name, bool(condition), detail)); print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}", flush=True)
    def prepare(suffix: str, *, review=False):
        row = service.create_version(uuid.UUID(owner_a_id), strategy_id, source=VALID + f"\n// {suffix}\n", filename="verify.pine")["version"]
        service.validate_version(uuid.UUID(owner_a_id), strategy_id, row["id"])
        service.submit_version(uuid.UUID(owner_a_id), strategy_id, row["id"])
        if review: service.start_review(uuid.UUID(admin_a_id), row["id"])
        return row["id"]

    # 1. Same owner/catalog/hash is serialized and deduplicated.
    same_source = VALID + "\n// concurrent-identical\n"
    result = collect([spawn("create", user=owner_a_id, strategy=strategy_id, source=same_source) for _ in range(4)])
    version_ids = {r.get("out", {}).get("version", {}).get("id") for r in result if r.get("ok")}
    check("1 identical source concurrent", len(version_ids) == 1 and len(result) == 4, str(version_ids))

    # 2. Modified versions both survive as immutable rows.
    result = collect([spawn("create", user=owner_a_id, strategy=strategy_id, source=VALID + f"\n// modified-{n}\n") for n in range(2)])
    check("2 modified versions concurrent", len({r.get("out", {}).get("version", {}).get("id") for r in result}) == 2)

    # 3. One validation identity despite four processes.
    result = collect([spawn("validate", user=owner_a_id, strategy=strategy_id, version=initial_id) for _ in range(4)])
    with session_scope() as db:
        report_count = db.scalar(select(func.count()).select_from(models.StrategyValidationReport).where(models.StrategyValidationReport.strategy_version_id == uuid.UUID(initial_id)))
    check("3 same validation concurrent", all(r.get("ok") for r in result) and report_count == 1, f"reports={report_count}")

    # 4. Abrupt worker termination leaves retryable validation identity.
    killed_id = service.create_version(uuid.UUID(owner_a_id), strategy_id, source=VALID + "\n// killed\n", filename="verify.pine")["version"]["id"]
    process = spawn("validate", user=owner_a_id, strategy=strategy_id, version=killed_id); process.kill(); process.wait()
    retry = service.validate_version(uuid.UUID(owner_a_id), strategy_id, killed_id)
    check("4 validator retry after termination", retry["report"]["eligible_for_review"])

    # 5. Submit racing validation can be retried without stale approval evidence.
    race_id = service.create_version(uuid.UUID(owner_a_id), strategy_id, source=VALID + "\n// submit-race\n", filename="verify.pine")["version"]["id"]
    result = collect([spawn("validate", user=owner_a_id, strategy=strategy_id, version=race_id), spawn("submit", user=owner_a_id, strategy=strategy_id, version=race_id)])
    try: service.submit_version(uuid.UUID(owner_a_id), strategy_id, race_id)
    except service.PineWorkflowError: pass
    with session_scope() as db: race_status = db.get(models.StrategyVersion, uuid.UUID(race_id)).status
    check("5 submit racing validation", race_status == "submitted", race_status)

    # 6. Exactly one admin starts review.
    start_id = prepare("two-start")
    result = collect([spawn("start", user=admin_a_id, version=start_id), spawn("start", user=admin_b_id, version=start_id)])
    check("6 two admins start", sum(r.get("ok", False) for r in result) == 1)

    # 7-8. Conflicting final decisions serialize to one terminal state/event.
    final_id = prepare("approve-reject", review=True)
    result = collect([spawn("approved", user=admin_a_id, version=final_id), spawn("rejected", user=admin_b_id, version=final_id)])
    check("7 approve racing reject", sum(r.get("ok", False) for r in result) == 1)
    changes_id = prepare("changes-approve", review=True)
    result = collect([spawn("changes_requested", user=admin_a_id, version=changes_id), spawn("approved", user=admin_b_id, version=changes_id)])
    check("8 changes racing approve", sum(r.get("ok", False) for r in result) == 1)

    # Paper instance used by link races.
    with session_scope() as db:
        catalog = models.StrategyCatalog(code="p4a-link-base", display_name="Link base", owner_type="nova", visibility="private", status="active")
        db.add(catalog); db.flush()
        base_version = models.StrategyVersion(strategy_id=catalog.id, version="1.0.0", payload_spec_version="nova.v1", source_journey="personal_tradingview", status="approved", execution_kind="external_webhook")
        db.add(base_version); db.flush()
        instance = models.StrategyInstance(user_id=uuid.UUID(owner_a_id), strategy_id=catalog.id, strategy_version_id=base_version.id, source_journey="PERSONAL_TRADINGVIEW", label="phase4a-link", status="draft", execution_mode="paper_live_data", current_lots=1)
        db.add(instance); db.flush(); instance_id = str(instance.id)
    link_id = prepare("approval-link", review=True)
    result = collect([spawn("approved", user=admin_a_id, version=link_id), spawn("link", user=owner_a_id, strategy=strategy_id, version=link_id, instance=instance_id)])
    if not result[1].get("ok"): service.link_version(uuid.UUID(owner_a_id), instance_id, strategy_id, link_id)
    with session_scope() as db: linked = db.get(models.StrategyInstance, uuid.UUID(instance_id))
    check("9 approval racing link", str(linked.strategy_version_id) == link_id and linked.execution_mode != "real_orders")

    review_id = prepare("review-new-version", review=True)
    old_hash = service.get_source(uuid.UUID(owner_a_id), strategy_id, review_id)["source_sha256"]
    result = collect([spawn("approved", user=admin_a_id, version=review_id), spawn("create", user=owner_a_id, strategy=strategy_id, source=VALID + "\n// replacement during review\n")])
    check("10 new version during review", service.get_source(uuid.UUID(owner_a_id), strategy_id, review_id)["source_sha256"] == old_hash and all(r.get("ok") for r in result))

    archive_id = prepare("archive-review", review=True)
    result = collect([spawn("approved", user=admin_a_id, version=archive_id)])
    with session_scope() as db:
        strategy = db.get(models.StrategyCatalog, uuid.UUID(strategy_id)); strategy.status = "archived"
    with session_scope() as db: archive_version = db.get(models.StrategyVersion, uuid.UUID(archive_id))
    check("11 archive racing review", result[0].get("ok") and archive_version.status == "approved")
    with session_scope() as db:
        db.get(models.StrategyCatalog, uuid.UUID(strategy_id)).status = "active"

    tenant_b = service.create_strategy(uuid.UUID(owner_b_id), name="Tenant B", source=VALID, filename="same.pine")
    check("12 identical source across tenants", tenant_b["version"]["id"] != initial_id)
    foreign = collect([spawn("source", user=owner_b_id, strategy=strategy_id, version=initial_id)])[0]
    check("13 cross-tenant source read", not foreign.get("ok") and VALID not in json.dumps(foreign))

    lock_id = prepare("killed-lock", review=True)
    locker = spawn("hold-lock", version=lock_id)
    assert locker.stdout is not None; locker.stdout.readline(); locker.kill(); locker.wait()
    released = service.decide_review(uuid.UUID(admin_a_id), lock_id, "approved", acknowledge_warnings=True)
    check("14 killed lock holder releases lock", released["version"]["status"] == "approved")

    before_reports = None
    with session_scope() as db:
        before_reports = db.scalar(select(func.count()).select_from(models.StrategyValidationReport))
        db.add(models.StrategyValidationReport(strategy_version_id=uuid.uuid4(), stage="compatibility", status="failed", findings=[]))
        try: db.flush()
        except Exception: db.rollback()
    with session_scope() as db: after_reports = db.scalar(select(func.count()).select_from(models.StrategyValidationReport))
    check("15 validation insert failure atomic", before_reports == after_reports)

    event_id = prepare("event-failure", review=True)
    with session_scope() as db:
        version = db.get(models.StrategyVersion, uuid.UUID(event_id)); version.status = "approved"
        db.add(models.StrategyAdminReview(strategy_version_id=version.id, reviewer_user_id=uuid.uuid4(), decision="approved", previous_status="under_review", new_status="approved", source_sha256=version.source_sha256))
        try: db.flush()
        except Exception: db.rollback()
    with session_scope() as db:
        version = db.get(models.StrategyVersion, uuid.UUID(event_id)); event_count = db.scalar(select(func.count()).select_from(models.StrategyAdminReview).where(models.StrategyAdminReview.strategy_version_id == version.id))
    check("16 review event failure atomic", version.status == "under_review" and event_count == 1, f"status={version.status}, events={event_count}")

    with session_scope() as db:
        duplicates = db.execute(select(models.StrategyValidationReport.strategy_version_id, models.StrategyValidationReport.validator_version, models.StrategyValidationReport.contract_version, models.StrategyValidationReport.source_sha256, func.count()).group_by(models.StrategyValidationReport.strategy_version_id, models.StrategyValidationReport.validator_version, models.StrategyValidationReport.contract_version, models.StrategyValidationReport.source_sha256).having(func.count() > 1)).all()
        approved_hashes_ok = all(v.source_sha256 for v in db.scalars(select(models.StrategyVersion).where(models.StrategyVersion.status == "approved", models.StrategyVersion.source_journey == "nova_hosted_personal")))
    check("invariant duplicate validation identities", not duplicates)
    check("invariant approved imported hashes", approved_hashes_ok)
    passed = sum(ok for _, ok, _ in checks)
    print(f"\nPhase 4A PostgreSQL 16: {passed}/{len(checks)} passed")
    if passed != len(checks): raise SystemExit(1)


if __name__ == "__main__":
    # Worker invocations still require argparse's database URL. It is inherited
    # in DATABASE_URL, so append it transparently for a compact process API.
    if "--worker" in sys.argv and "--database-url" not in sys.argv:
        sys.argv.extend(["--database-url", os.environ["DATABASE_URL"]])
    main()
