#!/usr/bin/env python
"""Phase 4A local HTTP soak: real Uvicorn + PostgreSQL, no Pine execution."""
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 18804
BASE = f"http://127.0.0.1:{PORT}"
SENTINEL = "PHASE4A_PRIVATE_SOURCE_SENTINEL_7f2a9c"
VALID = f'''//@version=6
indicator("NOVA NIFTY {SENTINEL}", overlay=true)
confirmed = barstate.isconfirmed
eod = hour == 15 and minute >= 15
if confirmed
    alert("BUY_CE", alert.freq_once_per_bar_close)
if confirmed
    alert("BUY_PE", alert.freq_once_per_bar_close)
if confirmed or eod
    alert("EXIT", alert.freq_once_per_bar_close)
'''
INVALID = '''//@version=4
indicator("BANKNIFTY invalid", overlay=true)
alert("BUY", alert.freq_all)
'''


def http(method, path, cookie, body=None):
    request = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    request.add_header("Cookie", cookie)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": raw.decode(errors="replace")[:200]}


def wait_ready():
    for _ in range(80):
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=1):
                return
        except Exception:
            time.sleep(.25)
    raise RuntimeError("Uvicorn did not become ready")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    runtime = Path(tempfile.mkdtemp(prefix="phase4a-soak-"))
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": args.database_url,
        "APP_ENV": "test",
        "AUTH_REQUIRED": "true",
        "APP_SECRET_KEY": "phase4a-soak-" + "s" * 40,
        "ENABLE_LIVE_ORDERS": "false",
        "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED": "false",
        "PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED": "false",
        "STRATEGY_JOB_WORKER_ENABLED": "false",
        "BACKGROUND_WORKER_RUNNER_ENABLED": "false",
        "RUNTIME_STATE_DIR": str(runtime / "state"),
        "RUNTIME_LOG_DIR": str(runtime / "logs"),
        "LAYMAN_ENV_FILE": str(runtime / "none.env"),
    })
    os.environ.update(env)
    migration = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, capture_output=True, text=True)
    if migration.returncode:
        raise SystemExit(migration.stdout + migration.stderr)

    from sqlalchemy import func, select
    from app.auth.session import sign_session_id
    from app.config import settings
    from app.db import crud, models
    from app.db.engine import session_scope

    def make_user(email, admin=False):
        with session_scope() as db:
            user = crud.upsert_google_user(db, google_sub="soak-" + email, email=email, name=email, picture_url=None, is_admin=admin)
            session = crud.create_session(db, user_id=user.id, ttl_seconds=7200)
            return str(user.id), f"{settings.SESSION_COOKIE_NAME}={sign_session_id(session.id)}"

    a_id, a_cookie = make_user("p4a-a@example.test")
    b_id, b_cookie = make_user("p4a-b@example.test")
    _, admin_cookie = make_user("p4a-admin@example.test", True)
    with session_scope() as db:
        catalog = models.StrategyCatalog(code="p4a-soak-base", display_name="Soak base", owner_type="nova", visibility="private", status="active")
        db.add(catalog)
        db.flush()
        base_version = models.StrategyVersion(strategy_id=catalog.id, version="1.0.0", payload_spec_version="nova.v1", source_journey="personal_tradingview", status="approved", execution_kind="external_webhook")
        db.add(base_version)
        db.flush()
        instances = []
        for owner, label in ((a_id, "A paper"), (b_id, "B paper")):
            row = models.StrategyInstance(user_id=uuid.UUID(owner), strategy_id=catalog.id, strategy_version_id=base_version.id, source_journey="PERSONAL_TRADINGVIEW", label=label, status="draft", execution_mode="paper_live_data", current_lots=1)
            db.add(row)
            db.flush()
            instances.append(str(row.id))

    server_log = runtime / "uvicorn.log"
    stream = server_log.open("w", encoding="utf-8")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT), "--no-access-log"], cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    checks = []

    def check(name, passed, detail=""):
        checks.append((name, bool(passed)))
        print(f"[{'PASS' if passed else 'FAIL'}] {name} {detail}", flush=True)

    try:
        wait_ready()
        status, created = http("POST", "/api/personal-pine-strategies", a_cookie, {"name": "A private <script>", "source": VALID, "filename": "a<script>.pine", "description": "<img src=x onerror=alert(1)>"})
        sid, vid = created["strategy"]["id"], created["version"]["id"]
        check("1 owner A upload valid Pine", status == 200)
        status, validated = http("POST", f"/api/personal-pine-strategies/{sid}/versions/{vid}/validate", a_cookie)
        check("2 deterministic validation passes", status == 200 and validated["report"]["eligible_for_review"])
        status, submitted = http("POST", f"/api/personal-pine-strategies/{sid}/versions/{vid}/submit", a_cookie)
        check("3 owner submits exact passing version", status == 200 and submitted["version"]["status"] == "submitted")
        status, detail = http("GET", f"/api/admin/pine-reviews/{vid}", admin_cookie)
        check("4 admin opens immutable source", status == 200 and detail["review"]["source"] == VALID)
        status, _ = http("POST", f"/api/admin/pine-reviews/{vid}/start", admin_cookie, {"note": "<b>reviewed as text</b>"})
        check("5 admin starts review", status == 200)
        status, approved = http("POST", f"/api/admin/pine-reviews/{vid}/approve", admin_cookie, {"note": "Exact static contract", "acknowledge_warnings": True})
        check("6 admin approves", status == 200 and approved["version"]["status"] == "approved")
        status, downloaded = http("GET", f"/api/personal-pine-strategies/{sid}/versions/{vid}/source", a_cookie)
        check("7 approved source exact download", status == 200 and downloaded["source"] == VALID)
        status, linked = http("POST", f"/api/personal-strategies/{instances[0]}/link-version", a_cookie, {"strategy_id": sid, "version_id": vid})
        check("8 approved version linked paper-only", status == 200 and not linked["link"]["hosted_execution_enabled"] and not linked["link"]["live_private_webhook_execution_enabled"])

        status, b_created = http("POST", "/api/personal-pine-strategies", b_cookie, {"name": "B invalid", "source": INVALID, "filename": "b.pine"})
        b_sid, b_vid = b_created["strategy"]["id"], b_created["version"]["id"]
        check("9 owner B uploads invalid Pine", status == 200)
        status, b_report = http("POST", f"/api/personal-pine-strategies/{b_sid}/versions/{b_vid}/validate", b_cookie)
        check("10 invalid report blocks", status == 200 and b_report["report"]["error_count"] > 0 and not b_report["report"]["eligible_for_review"])
        status, _ = http("POST", f"/api/personal-pine-strategies/{b_sid}/versions/{b_vid}/submit", b_cookie)
        check("11 failing version cannot submit", status == 409)
        status, foreign = http("GET", f"/api/personal-pine-strategies/{sid}/versions/{vid}/source", b_cookie)
        check("12 cross-tenant source returns 404", status == 404 and SENTINEL not in json.dumps(foreign))

        changed_source = VALID + "\nplot(close)\n"
        status, changed = http("POST", f"/api/personal-pine-strategies/{sid}/versions", a_cookie, {"source": changed_source, "filename": "a-v2.pine", "changelog": "corrected"})
        changed_id = changed["version"]["id"]
        check("13 modified source creates version", status == 200 and changed_id != vid)
        status, original = http("GET", f"/api/personal-pine-strategies/{sid}/versions/{vid}/source", a_cookie)
        check("14 approved original immutable", status == 200 and original["source"] == VALID)

        review_source = VALID + "\n// changes flow\n"
        _, review = http("POST", f"/api/personal-pine-strategies/{sid}/versions", a_cookie, {"source": review_source, "filename": "review.pine"})
        review_id = review["version"]["id"]
        http("POST", f"/api/personal-pine-strategies/{sid}/versions/{review_id}/validate", a_cookie)
        http("POST", f"/api/personal-pine-strategies/{sid}/versions/{review_id}/submit", a_cookie)
        http("POST", f"/api/admin/pine-reviews/{review_id}/start", admin_cookie, {})
        status, changes = http("POST", f"/api/admin/pine-reviews/{review_id}/request-changes", admin_cookie, {"note": "Create a replacement"})
        check("15 admin requests changes", status == 200 and changes["version"]["status"] == "changes_requested")
        status, replacement = http("POST", f"/api/personal-pine-strategies/{sid}/versions", a_cookie, {"source": review_source + "\n// replacement\n", "filename": "replacement.pine"})
        check("16 replacement is a new version", status == 200 and replacement["version"]["id"] != review_id)

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: http("POST", f"/api/personal-pine-strategies/{sid}/versions/{changed_id}/validate", a_cookie), range(6)))
        check("17 concurrent HTTP validation idempotent", all(code == 200 for code, _ in results) and len({body["report"]["id"] for _, body in results}) == 1)
        status, listing = http("GET", "/api/personal-pine-strategies", a_cookie)
        check("18 list summaries exclude source", status == 200 and SENTINEL not in json.dumps(listing))
        status, _ = http("GET", "/api/admin/pine-reviews", a_cookie)
        check("19 non-admin review queue blocked", status == 403)

        with session_scope() as db:
            duplicates = db.execute(select(models.StrategyValidationReport.strategy_version_id, models.StrategyValidationReport.validator_version, models.StrategyValidationReport.contract_version, models.StrategyValidationReport.source_sha256, func.count()).group_by(models.StrategyValidationReport.strategy_version_id, models.StrategyValidationReport.validator_version, models.StrategyValidationReport.contract_version, models.StrategyValidationReport.source_sha256).having(func.count() > 1)).all()
            jobs = db.scalar(select(func.count()).select_from(models.StrategyExecutionJob))
            live_intents = db.scalar(select(func.count()).select_from(models.LiveOrderIntent))
        check("20 no duplicate validation reports", not duplicates)
        check("21 no hosted Pine execution jobs", jobs == 0, f"jobs={jobs}")
        check("22 no live order intents", live_intents == 0, f"intents={live_intents}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        stream.close()
    logs = server_log.read_text(encoding="utf-8", errors="replace")
    check("23 sentinel absent from server logs", SENTINEL not in logs)
    check("24 live execution disabled", env["ENABLE_LIVE_ORDERS"] == "false" and env["PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED"] == "false")
    passed = sum(ok for _, ok in checks)
    print(f"\nPhase 4A local HTTP soak: {passed}/{len(checks)} passed")
    if passed != len(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
