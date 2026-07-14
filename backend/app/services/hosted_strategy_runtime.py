"""Durable, tenant-scoped, paper-only hosted strategy control and evaluation."""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import DEFAULT_EXCHANGE_SEGMENT, settings
from app.db import models
from app.db.engine import get_engine, session_scope
from app.schemas.hosted_strategy import Candle, StrategyIR, VALIDATOR_VERSION
from app.schemas.signal import NormalizedSignal
from app.services.hosted_strategy_engine import canonical_ir, evaluate, replay, validate_ir_document

ALLOWED_CREATION_SOURCES = {"MANUAL_TEST_FIXTURE", "NOVA_OWNED"}


class HostedStrategyError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "INVALID_REQUEST") -> None:
        super().__init__(message); self.status_code = status_code; self.code = code


def _now() -> datetime: return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _owned_runtime(db, user_id: uuid.UUID, instance_id: uuid.UUID, *, lock: bool = False):
    query = select(models.HostedStrategyRuntime).where(
        models.HostedStrategyRuntime.strategy_instance_id == instance_id,
        models.HostedStrategyRuntime.owner_user_id == user_id,
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None: raise HostedStrategyError("Hosted runtime not found.", status_code=404, code="NOT_FOUND")
    return row


def _ir_public(row: models.HostedStrategyIRVersion, *, include_document: bool = False) -> dict[str, Any]:
    data = {
        "id": str(row.id), "strategy_id": str(row.strategy_id), "owner_user_id": str(row.owner_user_id) if row.owner_user_id else None,
        "ir_version": row.ir_version, "canonical_sha256": row.canonical_sha256, "creation_source": row.creation_source,
        "validator_version": row.validator_version, "validation_report": row.validation_report, "eligible": row.eligible,
        "approved": row.approved_at is not None, "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if include_document: data["ir"] = row.ir_document
    return data


def _runtime_public(row: models.HostedStrategyRuntime) -> dict[str, Any]:
    return {
        "id": str(row.id), "strategy_instance_id": str(row.strategy_instance_id), "ir_version_id": str(row.ir_version_id),
        "paper_only": True, "status": row.status, "last_evaluated_candle": row.last_evaluated_candle_at.isoformat() if row.last_evaluated_candle_at else None,
        "last_emitted_action": row.last_emitted_action, "last_signal_id": row.last_signal_id,
        "warmup_status": row.warmup_status, "consecutive_error_count": row.consecutive_error_count,
        "safe_error_code": row.safe_error_code, "paused_reason": row.paused_reason,
    }


def create_ir(*, strategy_id: uuid.UUID, document: dict[str, Any], creation_source: str, admin_user_id: uuid.UUID) -> dict[str, Any]:
    if creation_source not in ALLOWED_CREATION_SOURCES:
        raise HostedStrategyError("Creation source is not eligible in Phase 5A.", code="CREATION_SOURCE_DISABLED")
    ir, report = validate_ir_document(document)
    if ir is None: raise HostedStrategyError("IR validation failed.", status_code=422, code="IR_INVALID")
    canonical, digest = canonical_ir(ir)
    try:
        with session_scope() as db:
            strategy = db.get(models.StrategyCatalog, strategy_id)
            if strategy is None: raise HostedStrategyError("Strategy not found.", status_code=404, code="NOT_FOUND")
            existing = db.scalar(select(models.HostedStrategyIRVersion).where(models.HostedStrategyIRVersion.strategy_id == strategy_id, models.HostedStrategyIRVersion.canonical_sha256 == digest))
            if existing: return _ir_public(existing, include_document=True)
            row = models.HostedStrategyIRVersion(
                strategy_id=strategy_id, owner_user_id=strategy.owner_user_id, ir_version=1, canonical_sha256=digest,
                ir_document=canonical, creation_source=creation_source, validator_version=VALIDATOR_VERSION,
                validation_report=report, eligible=bool(report["eligible"]),
            )
            db.add(row); db.flush(); return _ir_public(row, include_document=True)
    except IntegrityError as exc:
        raise HostedStrategyError("IR already exists.", status_code=409, code="DUPLICATE_IR") from exc


def approve_ir(ir_version_id: uuid.UUID, admin_user_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as db:
        row = db.get(models.HostedStrategyIRVersion, ir_version_id)
        if row is None: raise HostedStrategyError("IR not found.", status_code=404, code="NOT_FOUND")
        if not row.eligible or row.creation_source not in ALLOWED_CREATION_SOURCES:
            raise HostedStrategyError("IR is not eligible for activation.", status_code=409, code="IR_NOT_ELIGIBLE")
        if row.approved_at is None: row.approved_at = _now(); row.approved_by_user_id = admin_user_id
        db.flush(); return _ir_public(row, include_document=True)


def list_ir(user_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    limit = min(max(limit, 1), 100); offset = max(offset, 0)
    with session_scope() as db:
        predicate = (models.HostedStrategyIRVersion.owner_user_id.is_(None) | (models.HostedStrategyIRVersion.owner_user_id == user_id))
        total = db.scalar(select(func.count()).select_from(models.HostedStrategyIRVersion).where(predicate)) or 0
        rows = db.scalars(select(models.HostedStrategyIRVersion).where(predicate).order_by(models.HostedStrategyIRVersion.created_at.desc()).limit(limit).offset(offset)).all()
        return {"items": [_ir_public(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def admin_list_ir(*, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    limit = min(max(limit, 1), 100); offset = max(offset, 0)
    with session_scope() as db:
        total = db.scalar(select(func.count()).select_from(models.HostedStrategyIRVersion)) or 0
        rows = db.scalars(select(models.HostedStrategyIRVersion).order_by(models.HostedStrategyIRVersion.created_at.desc()).limit(limit).offset(offset)).all()
        return {"items": [_ir_public(row, include_document=row.creation_source in ALLOWED_CREATION_SOURCES) for row in rows], "total": total, "limit": limit, "offset": offset}


def admin_health() -> dict[str, Any]:
    with session_scope() as db:
        runtimes = dict(db.execute(select(models.HostedStrategyRuntime.status, func.count()).group_by(models.HostedStrategyRuntime.status)).all())
        evaluations = dict(db.execute(select(models.HostedStrategyEvaluation.status, func.count()).group_by(models.HostedStrategyEvaluation.status)).all())
        queue_depth = db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluationJob).where(models.HostedStrategyEvaluationJob.status.in_(["QUEUED", "PROCESSING"]))) or 0
        return {"runtimes": runtimes, "evaluations": evaluations, "queue_depth": queue_depth}


def admin_pause(runtime_id: uuid.UUID, reason: str = "Paused by admin") -> dict[str, Any]:
    with session_scope() as db:
        row = db.scalar(select(models.HostedStrategyRuntime).where(models.HostedStrategyRuntime.id == runtime_id).with_for_update())
        if row is None: raise HostedStrategyError("Hosted runtime not found.", status_code=404, code="NOT_FOUND")
        if row.status not in {"ACTIVE", "PAUSED", "ERROR"}: raise HostedStrategyError("Runtime cannot be paused.", status_code=409, code="INVALID_STATE")
        row.status = "PAUSED"; row.paused_reason = reason[:200]; db.flush(); return _runtime_public(row)


def get_ir(user_id: uuid.UUID, ir_version_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as db:
        row = db.get(models.HostedStrategyIRVersion, ir_version_id)
        if row is None or row.owner_user_id not in {None, user_id}:
            raise HostedStrategyError("IR not found.", status_code=404, code="NOT_FOUND")
        return _ir_public(row, include_document=True)


def link_runtime(user_id: uuid.UUID, instance_id: uuid.UUID, ir_version_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as db:
        instance = db.scalar(select(models.StrategyInstance).where(models.StrategyInstance.id == instance_id, models.StrategyInstance.user_id == user_id).with_for_update())
        ir = db.get(models.HostedStrategyIRVersion, ir_version_id)
        if instance is None or ir is None or ir.owner_user_id not in {None, user_id}:
            raise HostedStrategyError("Hosted resource not found.", status_code=404, code="NOT_FOUND")
        if ir.strategy_id != instance.strategy_id:
            raise HostedStrategyError("IR does not belong to the instance strategy.", status_code=409, code="STRATEGY_MISMATCH")
        if instance.execution_mode != "paper_live_data":
            raise HostedStrategyError("Hosted runtime requires paper_live_data mode.", status_code=409, code="PAPER_ONLY")
        row = db.scalar(select(models.HostedStrategyRuntime).where(models.HostedStrategyRuntime.strategy_instance_id == instance_id))
        if row and row.status not in {"READY", "STOPPED"}:
            raise HostedStrategyError("Active runtime IR is immutable.", status_code=409, code="RUNTIME_ACTIVE")
        if row: row.ir_version_id = ir.id; row.status = "READY"; row.safe_error_code = None
        else:
            row = models.HostedStrategyRuntime(strategy_instance_id=instance.id, ir_version_id=ir.id, owner_user_id=user_id, execution_mode="paper_live_data", status="READY")
            db.add(row)
        db.flush(); return _runtime_public(row)


def get_runtime(user_id: uuid.UUID, instance_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as db: return _runtime_public(_owned_runtime(db, user_id, instance_id))


def activate(user_id: uuid.UUID, instance_id: uuid.UUID) -> dict[str, Any]:
    if not settings.HOSTED_STRATEGY_RUNTIME_ENABLED or not settings.HOSTED_STRATEGY_PAPER_EXECUTION_ENABLED:
        raise HostedStrategyError("Hosted paper runtime is disabled.", status_code=403, code="DISABLED")
    with session_scope() as db:
        row = _owned_runtime(db, user_id, instance_id, lock=True); ir = db.get(models.HostedStrategyIRVersion, row.ir_version_id)
        instance = db.get(models.StrategyInstance, instance_id)
        if row.status not in {"READY", "PAUSED"} or not ir or not ir.eligible or not ir.approved_at:
            raise HostedStrategyError("Runtime is not ready for activation.", status_code=409, code="NOT_READY")
        if not instance or instance.status != "active" or instance.execution_mode != "paper_live_data":
            raise HostedStrategyError("Strategy instance must be active in paper mode.", status_code=409, code="INSTANCE_NOT_ACTIVE")
        per_user = db.scalar(select(func.count()).select_from(models.HostedStrategyRuntime).where(models.HostedStrategyRuntime.owner_user_id == user_id, models.HostedStrategyRuntime.status == "ACTIVE")) or 0
        total = db.scalar(select(func.count()).select_from(models.HostedStrategyRuntime).where(models.HostedStrategyRuntime.status == "ACTIVE")) or 0
        if per_user >= settings.HOSTED_STRATEGY_MAX_ACTIVE_PER_USER or total >= settings.HOSTED_STRATEGY_MAX_GLOBAL_ACTIVE:
            raise HostedStrategyError("Hosted runtime capacity reached.", status_code=429, code="CAPACITY")
        row.status = "ACTIVE"; row.paused_reason = None; row.safe_error_code = None; db.flush(); return _runtime_public(row)


def pause(user_id: uuid.UUID, instance_id: uuid.UUID, reason: str = "Paused by owner") -> dict[str, Any]:
    with session_scope() as db:
        row = _owned_runtime(db, user_id, instance_id, lock=True)
        if row.status not in {"ACTIVE", "PAUSED"}: raise HostedStrategyError("Runtime is not active.", status_code=409, code="INVALID_STATE")
        row.status = "PAUSED"; row.paused_reason = reason[:200]; db.flush(); return _runtime_public(row)


def resume(user_id: uuid.UUID, instance_id: uuid.UUID) -> dict[str, Any]: return activate(user_id, instance_id)


def _position_state(user_id: uuid.UUID) -> str:
    from app.services.execution_context import bind_user_execution_context
    from app.services.state_store import get_open_position, init_runtime_files
    from app.services.strategy_fanout import load_user_context
    user = load_user_context(user_id)
    if user is None: raise HostedStrategyError("Runtime owner unavailable.", code="OWNER_UNAVAILABLE")
    with bind_user_execution_context(user):
        init_runtime_files(); position = get_open_position()
    if not position.get("has_open_position"): return "FLAT"
    return "LONG_PE" if str(position.get("option_side") or "").upper() == "PE" else "LONG_CE"


def stop(user_id: uuid.UUID, instance_id: uuid.UUID, *, position_override: str | None = None) -> dict[str, Any]:
    if (position_override or _position_state(user_id)) != "FLAT":
        raise HostedStrategyError("Hosted runtime must be flat before stopping.", status_code=409, code="POSITION_OPEN")
    with session_scope() as db:
        row = _owned_runtime(db, user_id, instance_id, lock=True); row.status = "STOPPED"; row.paused_reason = "Stopped by owner"
        db.query(models.HostedStrategyEvaluationJob).filter(models.HostedStrategyEvaluationJob.hosted_runtime_id == row.id, models.HostedStrategyEvaluationJob.status == "QUEUED").update({"status": "SKIPPED", "safe_error_code": "RUNTIME_STOPPED", "completed_at": _now()})
        db.flush(); return _runtime_public(row)


def enqueue_finalized_candle(history: list[dict[str, Any]]) -> dict[str, int]:
    candles = [Candle.model_validate(item) for item in history]
    if not candles: raise HostedStrategyError("Candle history is required.")
    if any(candles[i].close_timestamp >= candles[i+1].close_timestamp for i in range(len(candles)-1)):
        raise HostedStrategyError("Candle history must be strictly ordered.", code="CANDLES_NOT_ORDERED")
    maximum = min(max(settings.HOSTED_STRATEGY_MAX_HISTORY_BARS, 1), 2000)
    candles = candles[-maximum:]; newest = candles[-1]
    if newest.close_timestamp > _now():
        raise HostedStrategyError("Future candles are not accepted.", code="FUTURE_CANDLE")
    payload = [c.model_dump(mode="json") for c in candles]
    created = duplicates = 0
    with session_scope() as db:
        queue_depth = db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluationJob).where(models.HostedStrategyEvaluationJob.status.in_(["QUEUED", "PROCESSING"]))) or 0
        if queue_depth >= settings.HOSTED_STRATEGY_QUEUE_DEPTH_LIMIT: raise HostedStrategyError("Evaluation queue is full.", status_code=503, code="QUEUE_FULL")
        runtimes = db.scalars(select(models.HostedStrategyRuntime).where(models.HostedStrategyRuntime.status.in_(["ACTIVE", "PAUSED"]))).all()
        for row in runtimes:
            if row.last_evaluated_candle_at and newest.close_timestamp <= _aware(row.last_evaluated_candle_at): duplicates += 1; continue
            nested = db.begin_nested()
            try:
                db.add(models.HostedStrategyEvaluationJob(hosted_runtime_id=row.id, ir_version_id=row.ir_version_id, owner_user_id=row.owner_user_id, candle_close_timestamp=newest.close_timestamp, candle_history=payload, status="QUEUED")); db.flush(); nested.commit(); created += 1
            except IntegrityError: nested.rollback(); duplicates += 1
    return {"created": created, "duplicates": duplicates}


def recover_stale_jobs() -> int:
    cutoff = _now() - timedelta(seconds=max(settings.HOSTED_STRATEGY_JOB_STALE_SECONDS, 1)); count = 0
    with session_scope() as db:
        for row in db.scalars(select(models.HostedStrategyEvaluationJob).where(models.HostedStrategyEvaluationJob.status == "PROCESSING", models.HostedStrategyEvaluationJob.locked_at < cutoff)).all():
            row.status = "FAILED" if row.attempts >= row.max_attempts else "QUEUED"; row.safe_error_code = "WORKER_LEASE_EXPIRED"; row.locked_at = None; count += 1
    return count


def _claim_jobs(limit: int) -> list[uuid.UUID]:
    with session_scope() as db:
        statement = select(models.HostedStrategyEvaluationJob).where(models.HostedStrategyEvaluationJob.status == "QUEUED", models.HostedStrategyEvaluationJob.available_at <= _now()).order_by(models.HostedStrategyEvaluationJob.created_at).limit(limit)
        if get_engine().dialect.name == "postgresql": statement = statement.with_for_update(skip_locked=True)
        statement = statement.limit(max(limit * 10, limit))
        candidates = list(db.scalars(statement).all())
        rows = []
        seen_owners = set()
        for row in candidates:
            if row.owner_user_id not in seen_owners:
                rows.append(row); seen_owners.add(row.owner_user_id)
            if len(rows) == limit: break
        if len(rows) < limit:
            rows.extend([row for row in candidates if row not in rows][:limit-len(rows)])
        for row in rows: row.status = "PROCESSING"; row.attempts += 1; row.locked_at = _now()
        db.flush(); return [row.id for row in rows]


def _normalized_signal(runtime: models.HostedStrategyRuntime, action: str, signal_id: str, candle_at: datetime) -> NormalizedSignal:
    entry = action in {"BUY_CE", "BUY_PE"}
    return NormalizedSignal(payload_format="NOVA", secret="", signal_id=signal_id, strategy_code=f"instance:{runtime.strategy_instance_id}", action="ENTRY" if entry else "EXIT", side="BUY" if entry else "SELL", symbol="NIFTY", instrument_type="OPTIDX", exchange_segment=DEFAULT_EXCHANGE_SEGMENT, option_side=action.removeprefix("BUY_") if entry else None, qty=1, order_type="MARKET", product_type="INTRADAY", source="hosted_strategy", raw_payload={"hosted_strategy": True, "runtime_id": str(runtime.id), "ir_version_id": str(runtime.ir_version_id), "candle_close_timestamp": candle_at.isoformat(), "normalized_action": action})


def process_job(job_id: uuid.UUID, *, position_override: str | None = None) -> str:
    with session_scope() as db:
        job = db.get(models.HostedStrategyEvaluationJob, job_id); runtime = db.get(models.HostedStrategyRuntime, job.hosted_runtime_id) if job else None; ir_row = db.get(models.HostedStrategyIRVersion, job.ir_version_id) if job else None
        if not job or not runtime or not ir_row or job.status != "PROCESSING": return "SKIPPED"
        past = db.scalars(select(models.HostedStrategyEvaluation).where(models.HostedStrategyEvaluation.hosted_runtime_id == runtime.id, models.HostedStrategyEvaluation.action.is_not(None)).order_by(models.HostedStrategyEvaluation.candle_close_timestamp.desc()).limit(100)).all()
        last_action_times = {}
        for item in past:
            last_action_times.setdefault(item.action, _aware(item.candle_close_timestamp))
        job_data = {"runtime_id": runtime.id, "owner": runtime.owner_user_id, "ir_id": ir_row.id, "instance": runtime.strategy_instance_id, "status": runtime.status, "candle_at": job.candle_close_timestamp, "history": job.candle_history, "document": ir_row.ir_document, "last_action_times": last_action_times}
    safe_error = None; result = None; position = position_override or _position_state(job_data["owner"])
    started = time.perf_counter()
    try:
        ir = StrategyIR.model_validate(job_data["document"]); candles = [Candle.model_validate(item) for item in job_data["history"]]
        indexes = {action: index for action, timestamp in job_data["last_action_times"].items() for index, candle in enumerate(candles) if candle.close_timestamp == timestamp}
        result = evaluate(ir, candles, position=position, last_action_index=indexes)
        elapsed = int((time.perf_counter()-started)*1000)
        if elapsed > settings.HOSTED_STRATEGY_EVALUATION_TIMEOUT_MS: raise ValueError("EVALUATION_TIMEOUT")
    except ValueError as exc:
        safe_error = "WARMUP_INCOMPLETE" if str(exc) == "WARMUP_INCOMPLETE" else "EVALUATION_FAILED"
        elapsed = int((time.perf_counter()-started)*1000)
    signal_id = None
    with session_scope() as db:
        job = db.scalar(select(models.HostedStrategyEvaluationJob).where(models.HostedStrategyEvaluationJob.id == job_id).with_for_update()); runtime = db.scalar(select(models.HostedStrategyRuntime).where(models.HostedStrategyRuntime.id == job.hosted_runtime_id).with_for_update())
        existing = db.scalar(select(models.HostedStrategyEvaluation).where(models.HostedStrategyEvaluation.hosted_runtime_id == runtime.id, models.HostedStrategyEvaluation.ir_version_id == job.ir_version_id, models.HostedStrategyEvaluation.candle_close_timestamp == job.candle_close_timestamp))
        if existing: job.status = "SKIPPED"; job.completed_at = _now(); return "SKIPPED"
        if runtime.ir_version_id != job.ir_version_id or runtime.status not in {"ACTIVE", "PAUSED"}: safe_error = "RUNTIME_CHANGED"
        action = result.action if result else None
        if runtime.status == "PAUSED" and action in {"BUY_CE", "BUY_PE"}: action = "HOLD"
        if safe_error == "WARMUP_INCOMPLETE": status = "SKIPPED"
        elif safe_error: status = "FAILED"
        elif action == "HOLD": status = "NO_SIGNAL"
        else: status = "SIGNAL_CREATED"
        if status == "SIGNAL_CREATED":
            signal_id = f"hosted:{runtime.strategy_instance_id}:{job.ir_version_id}:{job.candle_close_timestamp.isoformat()}:{action}"
            signal_row = models.StrategySignal(strategy_name=f"instance:{runtime.strategy_instance_id}", signal_id=signal_id, status="queued", result_summary={"source": "hosted_strategy", "action": action})
            db.add(signal_row); db.flush()
            signal = _normalized_signal(runtime, action, signal_id, job.candle_close_timestamp)
            db.add(models.StrategyExecutionJob(strategy_signal_id=signal_row.id, user_id=runtime.owner_user_id, strategy_name=f"instance:{runtime.strategy_instance_id}", signal_id=signal_id, signal_payload=signal.model_dump(mode="json"), lots=1, execution_mode="paper_live_data", status="queued"))
        db.add(models.HostedStrategyEvaluation(hosted_runtime_id=runtime.id, ir_version_id=job.ir_version_id, owner_user_id=runtime.owner_user_id, candle_close_timestamp=job.candle_close_timestamp, position_state=position, status=status, action=action, signal_id=signal_id, indicator_fingerprint=result.indicator_fingerprint if result else None, result_fingerprint=result.result_fingerprint if result else None, duration_ms=elapsed, safe_error_code=safe_error))
        job.status = status; job.safe_error_code = safe_error; job.completed_at = _now(); runtime.last_evaluated_candle_at = job.candle_close_timestamp
        runtime.warmup_status = "INCOMPLETE" if safe_error == "WARMUP_INCOMPLETE" else "READY"
        if status == "FAILED":
            runtime.consecutive_error_count += 1; runtime.safe_error_code = safe_error
            if runtime.consecutive_error_count >= settings.HOSTED_STRATEGY_MAX_CONSECUTIVE_ERRORS: runtime.status = "PAUSED"; runtime.paused_reason = "Automatic pause after evaluation failures"
        else: runtime.consecutive_error_count = 0; runtime.safe_error_code = None
        if signal_id: runtime.last_emitted_action = action; runtime.last_signal_id = signal_id
    if signal_id:
        from app.workers.strategy_job_worker import wake_strategy_job_worker
        wake_strategy_job_worker()
    return status


def process_queued_jobs_once(limit: int | None = None) -> int:
    if not settings.HOSTED_STRATEGY_RUNTIME_ENABLED: return 0
    ids = _claim_jobs(max(limit or settings.HOSTED_STRATEGY_WORKER_CONCURRENCY, 1))
    with ThreadPoolExecutor(max_workers=min(max(settings.HOSTED_STRATEGY_WORKER_CONCURRENCY, 1), len(ids) or 1), thread_name_prefix="nova-hosted-eval") as pool:
        list(pool.map(process_job, ids))
    return len(ids)


def list_evaluations(user_id: uuid.UUID, instance_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    limit = min(max(limit, 1), 100); offset = max(offset, 0)
    with session_scope() as db:
        runtime = _owned_runtime(db, user_id, instance_id)
        total = db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluation).where(models.HostedStrategyEvaluation.hosted_runtime_id == runtime.id)) or 0
        rows = db.scalars(select(models.HostedStrategyEvaluation).where(models.HostedStrategyEvaluation.hosted_runtime_id == runtime.id).order_by(models.HostedStrategyEvaluation.candle_close_timestamp.desc()).limit(limit).offset(offset)).all()
        return {"items": [{"candle_close_timestamp": row.candle_close_timestamp.isoformat(), "status": row.status, "action": row.action, "signal_id": row.signal_id, "duration_ms": row.duration_ms, "safe_error_code": row.safe_error_code} for row in rows], "total": total, "limit": limit, "offset": offset}


def replay_ir(user_id: uuid.UUID, ir_version_id: uuid.UUID, candles: list[Candle], starting_position: str = "FLAT") -> dict[str, Any]:
    with session_scope() as db:
        row = db.get(models.HostedStrategyIRVersion, ir_version_id)
        if row is None or row.owner_user_id not in {None, user_id}: raise HostedStrategyError("IR not found.", status_code=404, code="NOT_FOUND")
        ir = StrategyIR.model_validate(row.ir_document)
    return replay(ir, candles, starting_position=starting_position)


def replay_latest(user_id: uuid.UUID, instance_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as db:
        runtime = _owned_runtime(db, user_id, instance_id)
        ir_row = db.get(models.HostedStrategyIRVersion, runtime.ir_version_id)
        job = db.scalar(select(models.HostedStrategyEvaluationJob).where(models.HostedStrategyEvaluationJob.hosted_runtime_id == runtime.id).order_by(models.HostedStrategyEvaluationJob.candle_close_timestamp.desc()))
        if ir_row is None or job is None: raise HostedStrategyError("No bounded candle history is available for replay.", status_code=409, code="REPLAY_UNAVAILABLE")
        ir = StrategyIR.model_validate(ir_row.ir_document); candles = [Candle.model_validate(item) for item in job.candle_history]
    return replay(ir, candles, starting_position="FLAT")
