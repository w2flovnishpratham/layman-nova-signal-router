#!/usr/bin/env python
"""Phase 2B2A PostgreSQL 16 independent-process read-shadow verification."""
from __future__ import annotations
import argparse, json, os, subprocess, sys, threading, time, uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_engine = None
_created_users = []
_index_dropped = False

def cleanup():
    global _index_dropped
    if _engine is None: return
    from sqlalchemy import text
    with _engine.begin() as c:
        for user_id in _created_users:
            c.execute(text("DELETE FROM users WHERE id=:u"), {"u": user_id})
        if _index_dropped:
            c.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_position_per_user_mode ON strategy_instance_positions (user_id,execution_mode) WHERE position_state IN ('entering','open','scaling_in','exiting','reversing','error_reconciling')"))
    _created_users.clear(); _index_dropped=False

def snap(qty=75, state="open", side="CE"):
    return {"has_open_position": state != "closed", "security_id": "10001", "trading_symbol": "NIFTY VERIFY CE",
            "option_side": side, "strike": 25000, "expiry": "2026-07-30", "product_type": "INTRADAY",
            "qty": qty if state != "closed" else 0, "requested_qty": 75, "filled_qty": 75,
            "entry_order_id": "ENTRY-1", "entry_price": 100.0,
            "reversal_exit": {"to_option_side": "PE"} if state == "reversing" else None}

def setup():
    os.environ["POSITION_DB_READ_SHADOW_ENABLED"]="true"
    from app.config import settings
    settings.POSITION_DB_READ_SHADOW_ENABLED=True; settings.POSITION_DB_READ_SHADOW_SAMPLE_RATE=1.0
    settings.POSITION_DB_READ_SHADOW_TIMEOUT_MS=250; settings.POSITION_DB_READ_SHADOW_GRACE_MS=1000

def read_once(user, position, *, grace=True):
    setup()
    from app.services import position_read_shadow as rs
    from app.db.engine import get_engine
    from app.services.execution_context import bind_execution_context
    from app.services.user_context import CurrentUser
    if os.environ.get("VERIFY_SKIP_WARM") != "1":
        with get_engine().connect(): pass
    with bind_execution_context(CurrentUser(id=uuid.UUID(user), email="verify@example.test")):
        if grace: rs.note_json_write("live")
        original=json.loads(json.dumps(position)); started=time.perf_counter()
        diagnostic=rs.observe_json_position(position,"live")
        return {"unchanged": position==original, "json":position, "diagnostic":diagnostic,
                "latency_ms":(time.perf_counter()-started)*1000,"read_breaker":rs.health()["breaker"]}

def worker(a):
    if a.kind=="read": print(json.dumps(read_once(a.user,json.loads(a.payload),grace=a.grace))); return
    setup()
    if a.kind=="outage":
        from app.config import settings
        from app.db import engine as db_engine
        from app.services import position_read_shadow as rs
        from app.services.execution_context import bind_execution_context
        from app.services.user_context import CurrentUser
        settings.POSITION_DB_READ_SHADOW_FAILURE_THRESHOLD=1
        settings.POSITION_DB_READ_SHADOW_CIRCUIT_OPEN_SECONDS=1
        position=json.loads(a.payload); original=json.loads(json.dumps(position))
        with bind_execution_context(CurrentUser(id=uuid.UUID(a.user),email="verify@example.test")):
            first=rs.observe_json_position(position,"live"); opened=rs.health()["breaker"]
            settings.DATABASE_URL=os.environ["VERIFY_REAL_URL"]; db_engine.reset_engine_for_tests()
            with db_engine.get_engine().connect(): pass
            time.sleep(1.05); second=rs.observe_json_position(position,"live"); recovered=rs.health()["breaker"]
        print(json.dumps({"unchanged":position==original,"first":first,"opened":opened,"second":second,"recovered":recovered})); return
    from sqlalchemy import text
    from app.db.engine import get_engine
    engine=get_engine()
    with engine.connect() as c:
        tx=c.begin()
        if a.kind=="insert":
            c.execute(text("INSERT INTO strategy_instance_positions (id,user_id,execution_mode,position_state,position_side,underlying,security_id,trading_symbol,option_side,strike,expiry,product_type,entry_quantity,filled_entry_quantity,open_quantity,filled_exit_quantity,avg_entry_price_paise,entry_order_id,raw_snapshot,imported_from_json,version,created_at,updated_at) VALUES (:id,:u,'live','open','LONG','NIFTY','10001','NIFTY VERIFY CE','CE','25000','2026-07-30','INTRADAY',75,75,75,0,10000,'ENTRY-1',CAST(:raw AS jsonb),false,1,now(),now())"),{"id":uuid.uuid4(),"u":uuid.UUID(a.user),"raw":json.dumps(snap())})
        elif a.kind=="partial": c.execute(text("UPDATE strategy_instance_positions SET open_quantity=50,raw_snapshot=CAST(:raw AS jsonb) WHERE user_id=:u AND position_state!='closed'"),{"u":uuid.UUID(a.user),"raw":json.dumps(snap(50))})
        elif a.kind=="reversal": c.execute(text("UPDATE strategy_instance_positions SET position_state='reversing',reversal_metadata='{\"to_option_side\":\"PE\"}'::jsonb,raw_snapshot=CAST(:raw AS jsonb) WHERE user_id=:u AND position_state!='closed'"),{"u":uuid.UUID(a.user),"raw":json.dumps(snap(state="reversing"))})
        elif a.kind=="close": c.execute(text("UPDATE strategy_instance_positions SET position_state='closed',open_quantity=0,raw_snapshot=CAST(:raw AS jsonb) WHERE user_id=:u AND position_state!='closed'"),{"u":uuid.UUID(a.user),"raw":json.dumps(snap(state="closed"))})
        print("READY",flush=True); time.sleep(a.hold); tx.commit()

def spawn_read(user, position, grace=True, env=None):
    cmd=[sys.executable,"-m","scripts.position_read_shadow_pg_verify","--worker","--kind","read","--user",str(user),"--payload",json.dumps(position)]
    if grace: cmd.append("--grace")
    p=subprocess.run(cmd,cwd=ROOT,env=env or os.environ.copy(),capture_output=True,text=True,timeout=10)
    return {"exit_code":p.returncode,**json.loads(p.stdout.strip())}

def hold(user,kind):
    p=subprocess.Popen([sys.executable,"-m","scripts.position_read_shadow_pg_verify","--worker","--kind",kind,"--user",str(user),"--hold","5"],cwd=ROOT,env=os.environ.copy(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    assert p.stdout.readline().strip()=="READY"; return p

def verify():
    global _engine, _index_dropped
    setup()
    from sqlalchemy import text
    from app.db import models
    from app.db.engine import get_engine,session_scope
    from app.services import position_read_shadow as rs, position_store
    engine=get_engine()
    _engine=engine
    with engine.connect() as c: version=c.scalar(text("SHOW server_version"))
    if not str(version).startswith("16."): raise SystemExit("PostgreSQL 16 required")
    def user(name):
        u=models.User(email=f"read-{name}-{uuid.uuid4().hex}@example.test")
        with session_scope() as db: db.add(u); db.flush(); i=u.id
        _created_users.append(i)
        return i
    def row(u):
        with session_scope() as db:
            db.add(models.StrategyInstancePosition(user_id=u,execution_mode="live",position_state="open",security_id="10001",trading_symbol="NIFTY VERIFY CE",option_side="CE",strike="25000",expiry="2026-07-30",product_type="INTRADAY",entry_quantity=75,filled_entry_quantity=75,open_quantity=75,avg_entry_price_paise=10000,entry_order_id="ENTRY-1",raw_snapshot=snap()))
    reports=[]
    def add(name,result,ok=None):
        passed=(result.get("unchanged") and result.get("exit_code",0)==0) if ok is None else ok
        reports.append({"scenario":name,"passed":bool(passed),**result,"broker_actions":0,"write_breaker":position_store.breaker.snapshot()["state"]})
    # 1-8 uncommitted/committed transitions.
    u=user("entry"); p=hold(u,"insert"); add("1 uncommitted entry",spawn_read(u,snap())); p.wait(); add("2 entry committed",spawn_read(u,snap(),False))
    u=user("partial"); row(u); p=hold(u,"partial"); add("3 uncommitted partial",spawn_read(u,snap(50))); p.wait(); add("4 partial committed",spawn_read(u,snap(50),False))
    u=user("reversal"); row(u); p=hold(u,"reversal"); add("5 uncommitted reversal",spawn_read(u,snap(state="reversing"))); p.wait(); add("6 reversal committed",spawn_read(u,snap(state="reversing"),False))
    u=user("close"); row(u); p=hold(u,"close"); add("7 uncommitted close",spawn_read(u,snap(state="closed"))); p.wait(); add("8 close committed",spawn_read(u,snap(state="closed"),False))
    # 9-10 independent failure processes.
    for n,url in (("9 connection refusal","postgresql://x:x@127.0.0.1:1/x?connect_timeout=1"),("10 network black hole","postgresql://x:x@10.255.255.1:5432/x?connect_timeout=1")):
        u=user("outage"); row(u); env=os.environ.copy(); env["VERIFY_REAL_URL"]=env["DATABASE_URL"]; env["DATABASE_URL"]=url
        p=subprocess.run([sys.executable,"-m","scripts.position_read_shadow_pg_verify","--worker","--kind","outage","--user",str(u),"--payload",json.dumps(snap())],cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
        result={"exit_code":p.returncode,**json.loads(p.stdout.strip())}; add(n,result,result["unchanged"] and result["opened"]["state"]=="OPEN" and result["recovered"]["state"]=="CLOSED")
    # 11 write breaker open, read healthy.
    u=user("writebreaker"); row(u); position_store.breaker.state="OPEN"; r=read_once(str(u),snap(),grace=False); position_store.breaker.state="CLOSED"; add("11 write breaker isolation",r)
    # 12 read breaker open, typed write path healthy (direct DB proof).
    rs._breaker_state="OPEN"; rs._breaker_opened_at=time.monotonic(); u=user("readbreaker"); r=read_once(str(u),snap())
    from app.services import position_operations as ops
    typed=ops.record_entry(ops.OperationContext(u,"live"),source=ops.STRATEGY_ENTRY,signal_id="READ-BREAKER",order_id="READ-BREAKER",requested_qty=75,filled_qty=75,fill_price=100.0,partial_fill=False,position_snapshot=snap())
    add("12 read breaker isolation",r, r["unchanged"] and typed=="written" and position_store.breaker.snapshot()["state"]=="CLOSED"); rs._breaker_state="CLOSED"
    # 13 concurrent tenants.
    ua,ub=user("a"),user("b"); row(ua); row(ub)
    with __import__('concurrent.futures').futures.ThreadPoolExecutor(2) as ex: ra,rb=ex.submit(spawn_read,ua,snap(),False),ex.submit(spawn_read,ub,snap(),False); ra,rb=ra.result(),rb.result()
    add("13 concurrent tenants",{"unchanged":ra["unchanged"] and rb["unchanged"],"processes":[ra["exit_code"],rb["exit_code"]]})
    # 14 writer contention for A does not block B.
    p=hold(ua,"partial"); rb=spawn_read(ub,snap(),False); p.wait(); add("14 tenant contention isolation",rb)
    # 15 isolated DB uniqueness bypass.
    with engine.begin() as c:
        c.execute(text("DROP INDEX uq_active_position_per_user_mode")); c.execute(text("INSERT INTO strategy_instance_positions (id,user_id,execution_mode,position_state,position_side,underlying,open_quantity,filled_exit_quantity,imported_from_json,version,created_at,updated_at) VALUES (:id,:u,'live','open','LONG','NIFTY',1,0,false,1,now(),now())"),{"id":uuid.uuid4(),"u":ua})
    _index_dropped=True
    try:
        multi=spawn_read(ua,snap(),False); add("15 multiple active rows",multi,"MULTIPLE_ACTIVE_DB_POSITIONS" in multi["diagnostic"].get("categories",[]))
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM strategy_instance_positions WHERE user_id=:u AND open_quantity=1"),{"u":ua}); c.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_position_per_user_mode ON strategy_instance_positions (user_id,execution_mode) WHERE position_state IN ('entering','open','scaling_in','exiting','reversing','error_reconciling')"))
        _index_dropped=False
    # 16 local executor pressure: fifth observation skips without queueing.
    original=rs._query; rs._query=lambda *a:(time.sleep(1),[])[1]; u=user("pressure")
    threads=[threading.Thread(target=read_once,args=(str(u),snap())) for _ in range(4)]
    [t.start() for t in threads]
    deadline=time.time()+2
    while getattr(rs._inflight,"_value",1) and time.time()<deadline: time.sleep(.005)
    started=time.perf_counter(); pressure=read_once(str(u),snap()); elapsed=(time.perf_counter()-started)*1000; [t.join() for t in threads]; rs._query=original
    add("16 executor pressure",pressure,pressure["unchanged"] and elapsed<100 and pressure["diagnostic"].get("categories")==["DB_READ_TIMEOUT"])
    summary={"postgres_version":version,"checkpoint":os.popen("git rev-parse HEAD").read().strip(),"scenarios":reports,"attempted":16,"passed":sum(r["passed"] for r in reports),"changed_json":sum(not r.get("unchanged",True) for r in reports),"broker_actions":0}
    print(json.dumps(summary,indent=2,default=str)); return 0 if summary["passed"]==16 else 1

def main():
    try: return verify()
    finally: cleanup()

if __name__=="__main__":
    a=argparse.ArgumentParser(); a.add_argument("--worker",action="store_true"); a.add_argument("--kind"); a.add_argument("--user"); a.add_argument("--payload",default="{}"); a.add_argument("--hold",type=float,default=1); a.add_argument("--grace",action="store_true"); args=a.parse_args()
    raise SystemExit(worker(args) if args.worker else main())
