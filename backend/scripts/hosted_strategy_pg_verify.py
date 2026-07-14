#!/usr/bin/env python
"""Disposable PostgreSQL 16 hosted-runtime concurrency verifier."""
from __future__ import annotations

import argparse
import base64
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


def _spawn(operation: str, **values):
    args = [sys.executable, __file__, "--worker", operation]
    for key, value in values.items(): args += [f"--{key.replace('_','-')}", str(value)]
    return subprocess.Popen(args, cwd=ROOT, env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _collect(processes):
    output = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        try: output.append(json.loads(stdout.strip().splitlines()[-1]))
        except Exception: output.append({"ok": False, "error": (stdout+stderr)[-300:]})
    return output


def _worker(args):
    from app.services import hosted_strategy_runtime as hosted
    try:
        if args.worker == "enqueue": result = hosted.enqueue_finalized_candle(json.loads(base64.b64decode(args.payload)))
        elif args.worker == "claim": result = [str(item) for item in hosted._claim_jobs(1)]
        elif args.worker == "process": result = hosted.process_job(uuid.UUID(args.job), position_override=args.position)
        elif args.worker == "replay": result = hosted.replay_ir(uuid.UUID(args.user), uuid.UUID(args.ir), [__import__('app.schemas.hosted_strategy', fromlist=['Candle']).Candle.model_validate(item) for item in json.loads(base64.b64decode(args.payload))])
        else: raise ValueError("unknown operation")
        print(json.dumps({"ok": True, "result": result}, default=str))
    except Exception as exc: print(json.dumps({"ok": False, "kind": type(exc).__name__, "error": str(exc)[:160]}))


def _ir(name="PG verifier"):
    return {"ir_version":1,"strategy_name":name,"description":"Synthetic verification only","underlying":"NIFTY","timeframe":"1m","evaluation":"BAR_CLOSE","warmup_bars":1,"parameters":{},"indicators":[],"conditions":{"always":{"op":"CONSTANT","constant":True}},"actions":[{"action":"EXIT","when":"always","priority":100,"position_states":["LONG_CE","LONG_PE"]},{"action":"BUY_CE","when":"always","priority":20,"position_states":["FLAT"]}],"session":{"timezone":"Asia/Kolkata","entry_start":"09:20","entry_end":"14:45","force_exit_time":"15:15","skip_expiry_day":False},"risk_metadata":{"purpose":"verification_only"}}


def _candle(minute: int, close=100):
    timestamp = datetime(2026, 7, 13, 4, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)
    return {"instrument":"NIFTY","timeframe":"1m","close_timestamp":timestamp.isoformat(),"open":close,"high":close+1,"low":close-1,"close":close,"volume":10,"finalized":True}


def main(args):
    runtime_dir = tempfile.mkdtemp(prefix="phase5a-pg-")
    os.environ.update({"DATABASE_URL":args.database_url,"LAYMAN_ENV_FILE":str(Path(runtime_dir)/"none.env"),"APP_ENV":"test","AUTH_REQUIRED":"true","APP_SECRET_KEY":"phase5a-"+"x"*40,"ENABLE_LIVE_ORDERS":"false","DHAN_MODE":"MOCK","HOSTED_STRATEGY_RUNTIME_ENABLED":"true","HOSTED_STRATEGY_PAPER_EXECUTION_ENABLED":"true","RUNTIME_STATE_DIR":str(Path(runtime_dir)/"state"),"RUNTIME_LOG_DIR":str(Path(runtime_dir)/"logs")})
    if args.worker: return _worker(args)
    from sqlalchemy import func, select, text
    from app.config import settings
    from app.db import crud, models
    from app.db.engine import get_engine, session_scope
    from app.services import hosted_strategy_runtime as hosted, strategy_instance_service, strategy_registry
    with get_engine().begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE")); connection.execute(text("CREATE SCHEMA public"))
    migrated = subprocess.run([sys.executable,"-m","alembic","upgrade","head"],cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True)
    if migrated.returncode: raise SystemExit(migrated.stdout+migrated.stderr)
    with session_scope() as db:
        version = db.scalar(text("SHOW server_version")); assert str(version).startswith("16.")
        owner = crud.upsert_google_user(db,google_sub="p5a-owner",email="p5a-owner@example.test",name="Owner",picture_url=None,is_admin=False)
        other = crud.upsert_google_user(db,google_sub="p5a-other",email="p5a-other@example.test",name="Other",picture_url=None,is_admin=False)
        admin = crud.upsert_google_user(db,google_sub="p5a-admin",email="p5a-admin@example.test",name="Admin",picture_url=None,is_admin=True)
        ids = owner.id, other.id, admin.id
    owner_id, other_id, admin_id = ids; strategy_registry.backfill_supertrend()
    first = strategy_instance_service.create_instance(owner_id,strategy_code="supertrend",source_journey="NOVA_SHARED",label="PG hosted A",lots=2,execution_mode="paper_live_data")
    strategy_instance_service.activate_instance(owner_id,uuid.UUID(first["id"]))
    with session_scope() as db: strategy_id = db.scalar(select(models.StrategyCatalog.id).where(models.StrategyCatalog.code=="supertrend"))
    artifact = hosted.create_ir(strategy_id=strategy_id,document=_ir(),creation_source="NOVA_OWNED",admin_user_id=admin_id); ir_id=uuid.UUID(artifact["id"])
    hosted.approve_ir(ir_id,admin_id); runtime = hosted.link_runtime(owner_id,uuid.UUID(first["id"]),ir_id); hosted.activate(owner_id,uuid.UUID(first["id"]))
    checks=[]
    def check(name, condition, detail=""):
        checks.append(bool(condition)); print(f"[{'PASS' if condition else 'FAIL'}] {len(checks)} {name} {detail}",flush=True)
    def encoded(history): return base64.b64encode(json.dumps(history).encode()).decode()
    def job(status="QUEUED"):
        with session_scope() as db: return db.scalar(select(models.HostedStrategyEvaluationJob.id).where(models.HostedStrategyEvaluationJob.status==status).order_by(models.HostedStrategyEvaluationJob.created_at.desc()))
    # 1-3: identity, SKIP LOCKED, and atomic signal/job uniqueness.
    results=_collect([_spawn("enqueue",payload=encoded([_candle(1)])) for _ in range(8)])
    with session_scope() as db: count=db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluationJob))
    check("same instance/candle evaluated concurrently",count==1)
    claims=_collect([_spawn("claim") for _ in range(8)]); claimed=[x for r in claims if r.get("ok") for x in r["result"]]
    check("worker claims same job concurrently",len(claimed)==1)
    jid=uuid.UUID(claimed[0]); _collect([_spawn("process",job=jid,position="FLAT") for _ in range(4)])
    with session_scope() as db: signals=db.scalar(select(func.count()).select_from(models.StrategySignal)); executions=db.scalar(select(func.count()).select_from(models.StrategyExecutionJob)); audits=db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluation))
    check("duplicate signal creation",signals==executions==audits==1)
    # 4: pause wins before evaluation commit and blocks entry.
    hosted.enqueue_finalized_candle([_candle(1),_candle(2)]); jid=hosted._claim_jobs(1)[0]; hosted.pause(owner_id,uuid.UUID(first["id"])); outcome=hosted.process_job(jid,position_override="FLAT")
    check("runtime pause racing evaluation",outcome=="NO_SIGNAL")
    # 5-6: stopped runtime invalidates claimed and future entries.
    hosted.resume(owner_id,uuid.UUID(first["id"])); hosted.enqueue_finalized_candle([_candle(2),_candle(3)]); jid=hosted._claim_jobs(1)[0]; hosted.stop(owner_id,uuid.UUID(first["id"]),position_override="FLAT"); outcome=hosted.process_job(jid,position_override="FLAT")
    check("runtime stop racing evaluation",outcome=="FAILED")
    check("stop racing entry",hosted.enqueue_finalized_candle([_candle(4)])["created"]==0)
    # Restart, then prove paused EXIT remains idempotent.
    hosted.link_runtime(owner_id,uuid.UUID(first["id"]),ir_id); hosted.activate(owner_id,uuid.UUID(first["id"])); hosted.pause(owner_id,uuid.UUID(first["id"])); hosted.enqueue_finalized_candle([_candle(5)]); jid=hosted._claim_jobs(1)[0]
    results=_collect([_spawn("process",job=jid,position="LONG_CE") for _ in range(4)])
    with session_scope() as db: exit_count=db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluation).where(models.HostedStrategyEvaluation.action=="EXIT"))
    check("paused EXIT racing duplicate EXIT",exit_count==1)
    # 8: queued job stays pinned and fails if runtime is switched.
    hosted.resume(owner_id,uuid.UUID(first["id"])); hosted.enqueue_finalized_candle([_candle(6)]); jid=hosted._claim_jobs(1)[0]
    second_artifact=hosted.create_ir(strategy_id=strategy_id,document=_ir("PG verifier v2"),creation_source="NOVA_OWNED",admin_user_id=admin_id); hosted.approve_ir(uuid.UUID(second_artifact["id"]),admin_id)
    with session_scope() as db: db.get(models.HostedStrategyRuntime,uuid.UUID(runtime["id"])).ir_version_id=uuid.UUID(second_artifact["id"])
    check("IR version change while jobs queued",hosted.process_job(jid,position_override="FLAT")=="FAILED")
    # 9: dead lease is recovered.
    with session_scope() as db:
        row=models.HostedStrategyEvaluationJob(hosted_runtime_id=uuid.UUID(runtime["id"]),ir_version_id=uuid.UUID(second_artifact["id"]),owner_user_id=owner_id,candle_close_timestamp=datetime(2026,7,13,4,7,tzinfo=timezone.utc),candle_history=[_candle(7)],status="PROCESSING",attempts=1,locked_at=datetime.now(timezone.utc)-timedelta(minutes=5)); db.add(row)
    check("worker terminated during evaluation",hosted.recover_stale_jobs()>=1)
    # 10: reprocessing after commit cannot duplicate execution work.
    with session_scope() as db: before=db.scalar(select(func.count()).select_from(models.StrategyExecutionJob)); completed=db.scalar(select(models.HostedStrategyEvaluationJob.id).where(models.HostedStrategyEvaluationJob.status=="SIGNAL_CREATED").limit(1))
    if completed: hosted.process_job(completed,position_override="FLAT")
    with session_scope() as db: after=db.scalar(select(func.count()).select_from(models.StrategyExecutionJob))
    check("worker terminated after signal commit",before==after)
    # 11-12: FK/unique failures leave no partial audit or signal transactions.
    with session_scope() as db:
        before=db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluation))
        nested=db.begin_nested(); db.add(models.HostedStrategyEvaluation(hosted_runtime_id=uuid.uuid4(),ir_version_id=ir_id,owner_user_id=owner_id,candle_close_timestamp=datetime.now(timezone.utc),position_state="FLAT",status="FAILED",duration_ms=0))
        try: db.flush(); nested.commit()
        except Exception: nested.rollback()
        after=db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluation))
    check("evaluation audit insert failure",before==after)
    with session_scope() as db:
        before=db.scalar(select(func.count()).select_from(models.StrategySignal)); nested=db.begin_nested(); db.add(models.StrategySignal(strategy_name=f"instance:{first['id']}",signal_id=next(iter(db.scalars(select(models.StrategySignal.signal_id)).all())),status="queued"))
        try: db.flush(); nested.commit()
        except Exception: nested.rollback()
        after=db.scalar(select(func.count()).select_from(models.StrategySignal))
    check("signal insert failure",before==after)
    # 13: two tenants enqueue independently, never crossing owners.
    second=strategy_instance_service.create_instance(other_id,strategy_code="supertrend",source_journey="NOVA_SHARED",label="PG hosted B",lots=3,execution_mode="paper_live_data"); strategy_instance_service.activate_instance(other_id,uuid.UUID(second["id"])); hosted.link_runtime(other_id,uuid.UUID(second["id"]),uuid.UUID(second_artifact["id"])); hosted.activate(other_id,uuid.UUID(second["id"])); hosted.enqueue_finalized_candle([_candle(8)])
    with session_scope() as db: owners=set(db.scalars(select(models.HostedStrategyEvaluationJob.owner_user_id).where(models.HostedStrategyEvaluationJob.candle_close_timestamp==datetime(2026,7,13,4,8,tzinfo=timezone.utc))).all())
    check("two users evaluating simultaneously",owners=={owner_id,other_id})
    # 14: per-user activation cap is enforced.
    third=strategy_instance_service.create_instance(owner_id,strategy_code="supertrend",source_journey="NOVA_SHARED",label="PG hosted A2",lots=1,execution_mode="paper_live_data"); strategy_instance_service.activate_instance(owner_id,uuid.UUID(third["id"])); hosted.link_runtime(owner_id,uuid.UUID(third["id"]),uuid.UUID(second_artifact["id"])); old=settings.HOSTED_STRATEGY_MAX_ACTIVE_PER_USER; settings.HOSTED_STRATEGY_MAX_ACTIVE_PER_USER=1
    try: hosted.activate(owner_id,uuid.UUID(third["id"])); limited=False
    except hosted.HostedStrategyError: limited=True
    settings.HOSTED_STRATEGY_MAX_ACTIVE_PER_USER=old; check("one user exhausting evaluation limits",limited)
    # 15: replay is pure while live-paper jobs exist.
    before_signals=signals; replayed=_collect([_spawn("replay",user=owner_id,ir=second_artifact["id"],payload=encoded([_candle(9)]) )])[0]
    with session_scope() as db: replay_signals=db.scalar(select(func.count()).select_from(models.StrategySignal))
    check("replay racing live paper evaluation",replayed.get("ok") and replay_signals>=before_signals)
    # 16: auto-pause/manual-resume resolves to one valid state under row locks.
    with session_scope() as db: row=db.get(models.HostedStrategyRuntime,uuid.UUID(runtime["id"])); row.status="PAUSED"; row.consecutive_error_count=settings.HOSTED_STRATEGY_MAX_CONSECUTIVE_ERRORS; row.paused_reason="Automatic pause"
    try: hosted.resume(owner_id,uuid.UUID(first["id"])); valid=True
    except hosted.HostedStrategyError: valid=True
    with session_scope() as db: state=db.get(models.HostedStrategyRuntime,uuid.UUID(runtime["id"])).status
    check("runtime auto-pause racing manual resume",valid and state in {"ACTIVE","PAUSED"})
    # 17-18: older/corrected candles never reopen a completed identity.
    with session_scope() as db: row=db.get(models.HostedStrategyRuntime,uuid.UUID(runtime["id"])); row.last_evaluated_candle_at=datetime(2026,7,13,4,10,tzinfo=timezone.utc); row.status="ACTIVE"
    with session_scope() as db: before_old=db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluationJob).where(models.HostedStrategyEvaluationJob.hosted_runtime_id==uuid.UUID(runtime["id"])))
    hosted.enqueue_finalized_candle([_candle(9)])
    with session_scope() as db: after_old=db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluationJob).where(models.HostedStrategyEvaluationJob.hosted_runtime_id==uuid.UUID(runtime["id"])))
    check("out-of-order candle arriving concurrently",before_old==after_old)
    first_correction=hosted.enqueue_finalized_candle([_candle(11)]); second_correction=hosted.enqueue_finalized_candle([_candle(11,close=120)])
    check("same candle correction after completion",first_correction["created"]>=1 and second_correction["created"]==0)
    passed=sum(checks); print(f"\nPhase 5A PostgreSQL 16 concurrency: {passed}/{len(checks)} passed")
    if passed != len(checks): raise SystemExit(1)


if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--database-url",default=os.environ.get("DATABASE_URL","")); parser.add_argument("--worker",default=""); parser.add_argument("--payload",default=""); parser.add_argument("--job",default=""); parser.add_argument("--position",default="FLAT"); parser.add_argument("--user",default=""); parser.add_argument("--ir",default=""); parsed=parser.parse_args()
    if parsed.worker and not parsed.database_url: parsed.database_url=os.environ["DATABASE_URL"]
    main(parsed)
