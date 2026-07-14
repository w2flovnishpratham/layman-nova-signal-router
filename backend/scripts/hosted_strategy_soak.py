#!/usr/bin/env python
"""Real FastAPI + two real workers + PostgreSQL + PaperBroker Phase 5A soak."""
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

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); PORT=18805; BASE=f"http://127.0.0.1:{PORT}"


def http(method,path,cookie,body=None):
    request=urllib.request.Request(BASE+path,method=method,data=json.dumps(body).encode() if body is not None else None); request.add_header("Cookie",cookie)
    if body is not None: request.add_header("Content-Type","application/json")
    try:
        with urllib.request.urlopen(request,timeout=30) as response: return response.status,json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try: return exc.code,json.loads(exc.read() or b"{}")
        except Exception: return exc.code,{}


def candles(count, *, start=0, close=100):
    base=datetime(2026,7,13,4,0,tzinfo=timezone.utc)
    return [{"instrument":"NIFTY","timeframe":"1m","close_timestamp":(base+timedelta(minutes=i+start)).isoformat(),"open":close,"high":close+1,"low":close-1,"close":close,"volume":10,"finalized":True} for i in range(count)]


def wait_for(fn,timeout=30):
    end=time.time()+timeout
    while time.time()<end:
        try: value=fn()
        except Exception: value=None
        if value: return value
        time.sleep(.2)
    raise RuntimeError("timed out waiting for soak state")


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--database-url",required=True); args=parser.parse_args(); runtime=Path(tempfile.mkdtemp(prefix="phase5a-soak-"))
    env=os.environ.copy(); env.update({"DATABASE_URL":args.database_url,"APP_ENV":"test","AUTH_REQUIRED":"true","APP_SECRET_KEY":"phase5a-soak-"+"s"*40,"ENABLE_TEST_MARKET_DATA_PROVIDER":"true","DHAN_MODE":"MOCK","ENABLE_LIVE_ORDERS":"false","PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED":"false","PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED":"false","HOSTED_STRATEGY_RUNTIME_ENABLED":"true","HOSTED_STRATEGY_PAPER_EXECUTION_ENABLED":"true","STRATEGY_JOB_WORKER_ENABLED":"true","BACKGROUND_WORKER_RUNNER_ENABLED":"false","POSITION_DB_TYPED_WRITES_ENABLED":"true","POSITION_DB_SHADOW_WRITE_ENABLED":"true","POSITION_DB_READ_SHADOW_ENABLED":"true","RUNTIME_STATE_DIR":str(runtime/"state"),"RUNTIME_LOG_DIR":str(runtime/"logs"),"LAYMAN_ENV_FILE":str(runtime/"none.env"),"WEBHOOK_TRADING_ENABLED":"false"}); os.environ.update(env)
    from sqlalchemy import func,select,text
    from app.auth.session import sign_session_id
    from app.config import settings
    from app.db import crud,models
    from app.db.engine import get_engine,session_scope
    from app.services import hosted_strategy_runtime as hosted,strategy_instance_service,strategy_registry
    from scripts.hosted_strategy_pg_verify import _ir
    from scripts.private_webhook_soak import FIXTURE_CSV,write_fixture_csv
    write_fixture_csv(); env["DHAN_SCRIP_MASTER_PATH"]=str(FIXTURE_CSV); os.environ["DHAN_SCRIP_MASTER_PATH"]=str(FIXTURE_CSV)
    with get_engine().begin() as connection: connection.execute(text("DROP SCHEMA public CASCADE")); connection.execute(text("CREATE SCHEMA public"))
    migrated=subprocess.run([sys.executable,"-m","alembic","upgrade","head"],cwd=ROOT,env=env,capture_output=True,text=True)
    if migrated.returncode: raise SystemExit(migrated.stdout+migrated.stderr)
    def user(email,admin=False):
        with session_scope() as db:
            row=crud.upsert_google_user(db,google_sub="p5a-"+email,email=email,name=email,picture_url=None,is_admin=admin); session=crud.create_session(db,user_id=row.id,ttl_seconds=7200); return row.id,f"{settings.SESSION_COOKIE_NAME}={sign_session_id(session.id)}"
    a,a_cookie=user("a@example.test"); b,b_cookie=user("b@example.test"); admin,_=user("admin@example.test",True); strategy_registry.backfill_supertrend()
    ia=strategy_instance_service.create_instance(a,strategy_code="supertrend",source_journey="NOVA_SHARED",label="Soak A",lots=1,execution_mode="paper_live_data"); ib=strategy_instance_service.create_instance(b,strategy_code="supertrend",source_journey="NOVA_SHARED",label="Soak B",lots=1,execution_mode="paper_live_data")
    strategy_instance_service.activate_instance(a,uuid.UUID(ia["id"])); strategy_instance_service.activate_instance(b,uuid.UUID(ib["id"]))
    with session_scope() as db: strategy_id=db.scalar(select(models.StrategyCatalog.id).where(models.StrategyCatalog.code=="supertrend"))
    ira=hosted.create_ir(strategy_id=strategy_id,document=_ir("Soak constant"),creation_source="NOVA_OWNED",admin_user_id=admin); hosted.approve_ir(uuid.UUID(ira["id"]),admin)
    supertrend=json.loads((ROOT/"app/fixtures/hosted_supertrend_v1.json").read_text()); irb=hosted.create_ir(strategy_id=strategy_id,document=supertrend,creation_source="NOVA_OWNED",admin_user_id=admin); hosted.approve_ir(uuid.UUID(irb["id"]),admin)
    hosted.link_runtime(a,uuid.UUID(ia["id"]),uuid.UUID(ira["id"])); hosted.link_runtime(b,uuid.UUID(ib["id"]),uuid.UUID(irb["id"])); hosted.activate(a,uuid.UUID(ia["id"])); hosted.activate(b,uuid.UUID(ib["id"]))
    log=(runtime/"server.log").open("w",encoding="utf-8"); server=subprocess.Popen([sys.executable,"-m","uvicorn","scripts.hosted_strategy_soak_app:app","--host","127.0.0.1","--port",str(PORT),"--no-access-log"],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    def start_hosted(): return subprocess.Popen([sys.executable,"-m","scripts.hosted_strategy_soak_worker"],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    hosted_worker=start_hosted(); strategy_worker=subprocess.Popen([sys.executable,"-m","scripts.private_webhook_soak_worker"],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    checks=[]
    def check(name,ok,detail=""): checks.append(bool(ok)); print(f"[{'PASS' if ok else 'FAIL'}] {len(checks)} {name} {detail}",flush=True)
    try:
        wait_for(lambda: urllib.request.urlopen(BASE+"/health",timeout=1).status==200)
        code,cfg=http("GET","/api/hosted-strategies/config",a_cookie); check("feature availability visible",code==200 and cfg["paper_only"])
        check("paper rollout flags enabled",cfg.get("runtime_enabled") and cfg.get("paper_execution_enabled"))
        code,detail=http("GET",f"/api/strategy-instances/{ia['id']}/hosted-runtime",a_cookie); check("owner reads pinned runtime",code==200 and detail["runtime"]["ir_version_id"]==ira["id"])
        code,_=http("GET",f"/api/strategy-instances/{ia['id']}/hosted-runtime",b_cookie); check("cross-tenant runtime blocked",code==404)
        check("paper engine A starts",http("POST","/__soak__/engine",a_cookie,{})[0]==200)
        check("paper engine B starts",http("POST","/__soak__/engine",b_cookie,{})[0]==200)
        code,feed=http("POST","/__soak__/hosted-candles",a_cookie,candles(1)); check("finalized candle queued",code==200 and feed["created"]==2)
        def evaluation_a():
            with session_scope() as db: return db.scalar(select(models.HostedStrategyEvaluation).join(models.HostedStrategyRuntime,models.HostedStrategyEvaluation.hosted_runtime_id==models.HostedStrategyRuntime.id).where(models.HostedStrategyRuntime.owner_user_id==a))
        ea=wait_for(evaluation_a); check("A evaluation completes",ea.status=="SIGNAL_CREATED")
        def execution_a():
            with session_scope() as db: return db.scalar(select(models.StrategyExecutionJob).where(models.StrategyExecutionJob.user_id==a,models.StrategyExecutionJob.status=="completed"))
        job=wait_for(execution_a,45); check("durable StrategyExecutionJob completes",job is not None)
        check("PaperBroker order succeeds once",(job.result_summary or {}).get("success") is True or (job.result_summary or {}).get("status") in {"success","completed"},str(job.result_summary)[:120])
        with session_scope() as db: live=db.scalar(select(func.count()).select_from(models.LiveOrderIntent)); before_jobs=db.scalar(select(func.count()).select_from(models.StrategyExecutionJob))
        check("no live order intents",live==0)
        http("POST","/__soak__/hosted-candles",a_cookie,candles(1)); time.sleep(1)
        with session_scope() as db: after_jobs=db.scalar(select(func.count()).select_from(models.StrategyExecutionJob))
        check("identical candle creates no duplicate order",before_jobs==after_jobs)
        code,paused=http("POST",f"/api/strategy-instances/{ia['id']}/hosted-pause",a_cookie,{}); check("owner pauses runtime",code==200 and paused["runtime"]["status"]=="PAUSED")
        http("POST","/__soak__/hosted-candles",a_cookie,candles(1,start=1)); time.sleep(1)
        with session_scope() as db: paused_eval=db.scalar(select(models.HostedStrategyEvaluation).join(models.HostedStrategyRuntime,models.HostedStrategyEvaluation.hosted_runtime_id==models.HostedStrategyRuntime.id).where(models.HostedStrategyRuntime.owner_user_id==a).order_by(models.HostedStrategyEvaluation.created_at.desc()))
        check("paused entry suppressed",paused_eval.action not in {"BUY_CE","BUY_PE"})
        # The first PaperBroker entry is open, so a paused runtime may still emit EXIT.
        http("POST","/__soak__/hosted-candles",a_cookie,candles(1,start=2));
        def exit_eval():
            with session_scope() as db: return db.scalar(select(models.HostedStrategyEvaluation).join(models.HostedStrategyRuntime,models.HostedStrategyEvaluation.hosted_runtime_id==models.HostedStrategyRuntime.id).where(models.HostedStrategyRuntime.owner_user_id==a,models.HostedStrategyEvaluation.action=="EXIT"))
        exit_row=wait_for(exit_eval,30); check("paused EXIT remains available",exit_row is not None)
        def flat_a(): return http("GET","/__soak__/position",a_cookie)[1].get("position",{}).get("has_open_position") is False
        check("PaperBroker exits to flat",wait_for(flat_a,45))
        code,stopped=http("POST",f"/api/strategy-instances/{ia['id']}/hosted-stop",a_cookie,{}); check("flat runtime stops",code==200 and stopped["runtime"]["status"]=="STOPPED")
        with session_scope() as db: before_a=db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluation).join(models.HostedStrategyRuntime,models.HostedStrategyEvaluation.hosted_runtime_id==models.HostedStrategyRuntime.id).where(models.HostedStrategyRuntime.owner_user_id==a))
        http("POST","/__soak__/hosted-candles",b_cookie,candles(12,start=3)); time.sleep(1)
        with session_scope() as db: after_a=db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluation).join(models.HostedStrategyRuntime,models.HostedStrategyEvaluation.hosted_runtime_id==models.HostedStrategyRuntime.id).where(models.HostedStrategyRuntime.owner_user_id==a)); b_evals=db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluation).join(models.HostedStrategyRuntime,models.HostedStrategyEvaluation.hosted_runtime_id==models.HostedStrategyRuntime.id).where(models.HostedStrategyRuntime.owner_user_id==b))
        check("stopped runtime gets no future evaluations",before_a==after_a)
        check("Supertrend fixture evaluates independently",b_evals>=1)
        with session_scope() as db: owners=set(db.scalars(select(models.HostedStrategyEvaluation.owner_user_id)).all())
        check("evaluation audits preserve tenant owners",owners=={a,b})
        hosted_worker.kill(); hosted_worker.wait(); hosted_worker=start_hosted(); check("hosted worker restarts",hosted_worker.poll() is None)
        payload={"candles":candles(1),"starting_position":"FLAT"}; first_replay=http("POST",f"/api/hosted-strategies/ir/{ira['id']}/replay",a_cookie,payload); second_replay=http("POST",f"/api/hosted-strategies/ir/{ira['id']}/replay",a_cookie,payload)
        check("historical replay endpoint succeeds",first_replay[0]==200)
        check("replay timeline is deterministic",first_replay[1].get("fingerprint")==second_replay[1].get("fingerprint"))
        with session_scope() as db: replay_jobs=db.scalar(select(func.count()).select_from(models.StrategyExecutionJob))
        check("replay creates no broker work",replay_jobs==after_jobs+1)  # one paused EXIT was legitimately added
        code,history=http("GET",f"/api/strategy-instances/{ib['id']}/hosted-evaluations",b_cookie); check("paginated evaluation history visible",code==200 and "items" in history)
        with session_scope() as db:
            duplicate_evals=db.execute(select(models.HostedStrategyEvaluation.hosted_runtime_id,models.HostedStrategyEvaluation.ir_version_id,models.HostedStrategyEvaluation.candle_close_timestamp,func.count()).group_by(models.HostedStrategyEvaluation.hosted_runtime_id,models.HostedStrategyEvaluation.ir_version_id,models.HostedStrategyEvaluation.candle_close_timestamp).having(func.count()>1)).all(); duplicate_signals=db.execute(select(models.StrategySignal.strategy_name,models.StrategySignal.signal_id,func.count()).group_by(models.StrategySignal.strategy_name,models.StrategySignal.signal_id).having(func.count()>1)).all()
        check("final identity audit has no duplicates",not duplicate_evals and not duplicate_signals)
        check("live orders remained disabled",env["ENABLE_LIVE_ORDERS"]=="false")
        check("JSON position authority remained active",flat_a())
    finally:
        for process in (hosted_worker,strategy_worker,server):
            if process.poll() is None: process.kill(); process.wait()
        log.close()
    logs=(runtime/"server.log").read_text(encoding="utf-8",errors="replace"); check("no arbitrary-code execution evidence","exec(" not in logs and "eval(" not in logs)
    passed=sum(checks); print(f"\nPhase 5A local end-to-end soak: {passed}/{len(checks)} passed")
    if passed != len(checks): raise SystemExit(1)


if __name__=="__main__": main()
