from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import func, select

from app.auth.db import session_scope
from app.auth.models import (
    PaperStrategyOrder,
    LiveOrderJob,
    PaperLedgerEntry,
    PaperPosition,
    Strategy,
    StrategySignal,
    StrategyVersion,
    User,
    UserSignalJob,
    UserStrategySubscription,
    UserNotification,
    WorkerHeartbeat,
    WorkerJob,
    utc_now_dt,
)
from app.auth.security import current_user_from_request
from app.auth.service import admin_emails
from app.config import settings
from app.services.signal_fanout_service import queue_strategy_signal_fanout
from app.services.strategy_catalog_service import (
    get_active_version,
    get_strategy_by_code,
    list_active_strategies,
)
from app.services.strategy_signal_service import (
    DuplicateStrategySignal,
    StrategySignalError,
    accept_strategy_signal,
)
from app.services.subscription_service import (
    SubscriptionError,
    SubscriptionRisk,
    disable_subscription,
    list_user_subscriptions,
    pause_subscription,
    resume_subscription,
    subscribe_user,
)
from app.services.queue_service import WorkerQueueError, queue_counts, retry_job


router = APIRouter()


class SubscribeStrategyRequest(BaseModel):
    strategy_code: str = Field(..., min_length=2, max_length=80)
    mode: Literal["paper", "live"] = "paper"
    max_qty: int | None = Field(default=None, gt=0, le=100000)
    max_daily_loss: float | None = Field(default=None, ge=0, le=100000000)
    max_trades_per_day: int | None = Field(default=None, ge=0, le=10000)
    allowed_option_side: Literal["CE", "PE", "BOTH"] = "BOTH"


class StrategySignalIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    strategy_code: str = Field(..., min_length=2, max_length=80)
    signal_id: str = Field(..., min_length=3, max_length=200)
    symbol: str = Field(..., min_length=1, max_length=40)
    action: Literal["BUY", "SELL", "EXIT", "CLOSE"]
    option_type: Literal["CE", "PE"] | None = None
    strike: float | None = Field(default=None, gt=0)
    expiry: str | None = Field(default=None, max_length=40)
    timeframe: str | None = Field(default=None, max_length=20)
    price: float | None = Field(default=None, gt=0)
    source: str = Field(default="internal", min_length=2, max_length=80)


def _require_platform_user(request: Request) -> User:
    user = getattr(request.state, "auth_user", None) or current_user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    return user


def _require_admin_user(request: Request) -> User:
    user = _require_platform_user(request)
    admins = admin_emails()
    if not admins or user.email.strip().lower() not in admins:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


@router.get("/strategies")
def strategies_catalog(user: User = Depends(_require_platform_user)) -> dict[str, Any]:
    del user
    with session_scope() as session:
        records = list_active_strategies(session)
        items = [_strategy_payload(strategy, get_active_version(session, int(strategy.id))) for strategy in records]
    return {"strategies": items}


@router.get("/strategies/{strategy_code}")
def strategy_detail(strategy_code: str, user: User = Depends(_require_platform_user)) -> dict[str, Any]:
    del user
    with session_scope() as session:
        strategy = get_strategy_by_code(session, strategy_code)
        if strategy is None or not strategy.is_active:
            raise HTTPException(status_code=404, detail="Strategy not found.")
        version = get_active_version(session, int(strategy.id))
        return {"strategy": _strategy_payload(strategy, version)}


@router.get("/me/strategy-subscriptions")
def my_strategy_subscriptions(user: User = Depends(_require_platform_user)) -> dict[str, Any]:
    rows = list_user_subscriptions(user.id)
    return {
        "freeActiveLimit": settings.FREE_MAX_ACTIVE_PAPER_STRATEGIES,
        "subscriptions": [_subscription_payload(subscription, strategy, version) for subscription, strategy, version in rows],
    }


@router.post("/me/strategy-subscriptions", status_code=201)
def create_strategy_subscription(
    body: SubscribeStrategyRequest,
    user: User = Depends(_require_platform_user),
) -> dict[str, Any]:
    try:
        subscription = subscribe_user(
            user_id=user.id,
            strategy_code=body.strategy_code,
            mode=body.mode,
            risk=SubscriptionRisk(
                max_qty=body.max_qty,
                max_daily_loss=body.max_daily_loss,
                max_trades_per_day=body.max_trades_per_day,
                allowed_option_side=body.allowed_option_side,
            ),
        )
    except SubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"subscription": _subscription_by_id_payload(user.id, int(subscription.id))}


@router.post("/me/strategy-subscriptions/{subscription_id}/pause")
def pause_my_strategy(
    subscription_id: int,
    user: User = Depends(_require_platform_user),
) -> dict[str, Any]:
    return {"subscription": _change_subscription(pause_subscription, user.id, subscription_id)}


@router.post("/me/strategy-subscriptions/{subscription_id}/resume")
def resume_my_strategy(
    subscription_id: int,
    user: User = Depends(_require_platform_user),
) -> dict[str, Any]:
    return {"subscription": _change_subscription(resume_subscription, user.id, subscription_id)}


@router.delete("/me/strategy-subscriptions/{subscription_id}")
def remove_my_strategy(
    subscription_id: int,
    user: User = Depends(_require_platform_user),
) -> dict[str, Any]:
    try:
        disable_subscription(user.id, subscription_id)
    except SubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"success": True}


@router.get("/me/strategy-signals")
def my_strategy_signals(
    user: User = Depends(_require_platform_user),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    with session_scope() as session:
        rows = session.exec(
            select(StrategySignal, Strategy)
            .join(UserSignalJob, UserSignalJob.strategy_signal_id == StrategySignal.id)
            .join(Strategy, Strategy.id == StrategySignal.strategy_id)
            .where(UserSignalJob.user_id == user.id)
            .distinct()
            .order_by(StrategySignal.received_at.desc())
            .limit(limit)
        ).all()
        return {
            "signals": [
                {
                    "id": signal.id,
                    "strategyCode": signal.strategy_code,
                    "strategyName": strategy.name,
                    "signalId": signal.signal_id,
                    "symbol": signal.symbol,
                    "action": signal.action,
                    "optionType": signal.option_type,
                    "strike": signal.strike,
                    "expiry": signal.expiry,
                    "timeframe": signal.timeframe,
                    "price": signal.price,
                    "receivedAt": _iso(signal.received_at),
                    "source": signal.source,
                    "status": signal.status,
                }
                for signal, strategy in rows
            ]
        }


@router.get("/me/signal-jobs")
def my_signal_jobs(
    user: User = Depends(_require_platform_user),
    status: str | None = Query(default=None, max_length=80),
    strategy_code: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=100, ge=1, le=300),
) -> dict[str, Any]:
    with session_scope() as session:
        statement = (
            select(UserSignalJob)
            .where(UserSignalJob.user_id == user.id)
            .order_by(UserSignalJob.created_at.desc())
            .limit(limit)
        )
        if status:
            statement = statement.where(UserSignalJob.status == status)
        if strategy_code:
            strategy = get_strategy_by_code(session, strategy_code)
            if strategy is None:
                return {"jobs": []}
            statement = statement.where(UserSignalJob.strategy_id == strategy.id)
        jobs = list(
            session.exec(statement).all()
        )
        return {"jobs": [_job_payload(session, job) for job in jobs]}


@router.get("/me/signal-jobs/{job_id}")
def my_signal_job_detail(job_id: int, user: User = Depends(_require_platform_user)) -> dict[str, Any]:
    with session_scope() as session:
        job = session.exec(
            select(UserSignalJob).where(
                UserSignalJob.id == job_id,
                UserSignalJob.user_id == user.id,
            )
        ).first()
        if job is None:
            raise HTTPException(status_code=404, detail="Signal job not found.")
        return {"job": _job_payload(session, job)}


@router.get("/me/paper-positions")
def my_paper_positions(
    user: User = Depends(_require_platform_user),
    include_closed: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    with session_scope() as session:
        statement = (
            select(PaperPosition)
            .where(PaperPosition.user_id == user.id)
            .order_by(PaperPosition.opened_at.desc())
            .limit(limit)
        )
        if not include_closed:
            statement = statement.where(PaperPosition.status == "open")
        positions = list(session.exec(statement).all())
        return {"positions": [_position_payload(position) for position in positions]}


@router.get("/me/paper-positions/{position_id}")
def my_paper_position_detail(
    position_id: int,
    user: User = Depends(_require_platform_user),
) -> dict[str, Any]:
    with session_scope() as session:
        position = session.exec(
            select(PaperPosition).where(
                PaperPosition.id == position_id,
                PaperPosition.user_id == user.id,
            )
        ).first()
        if position is None:
            raise HTTPException(status_code=404, detail="Paper position not found.")
        return {"position": _position_payload(position)}


@router.get("/me/paper-ledger")
def my_paper_ledger(
    user: User = Depends(_require_platform_user),
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    with session_scope() as session:
        statement = (
            select(PaperLedgerEntry)
            .where(PaperLedgerEntry.user_id == user.id)
            .order_by(PaperLedgerEntry.created_at.desc())
            .limit(limit)
        )
        if date:
            try:
                day_start, day_end = _india_date_bounds_utc(date)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="date must be a valid YYYY-MM-DD value.") from exc
            statement = statement.where(
                PaperLedgerEntry.created_at >= day_start,
                PaperLedgerEntry.created_at < day_end,
            )
        entries = list(
            session.exec(statement).all()
        )
        return {"entries": [_ledger_payload(entry) for entry in entries]}


@router.get("/me/notifications")
def my_notifications(
    user: User = Depends(_require_platform_user),
    limit: int = Query(default=100, ge=1, le=300),
) -> dict[str, Any]:
    with session_scope() as session:
        notifications = list(
            session.exec(
                select(UserNotification)
                .where(UserNotification.user_id == user.id)
                .order_by(UserNotification.created_at.desc())
                .limit(limit)
            ).all()
        )
        return {
            "notifications": [
                {
                    "id": notification.id,
                    "eventType": notification.event_type,
                    "payload": notification.payload_json,
                    "status": notification.status,
                    "createdAt": _iso(notification.created_at),
                    "sentAt": _iso(notification.sent_at),
                }
                for notification in notifications
            ]
        }


@router.get("/admin/worker/jobs")
@router.get("/admin/worker-queue")
def admin_worker_queue(
    admin: User = Depends(_require_admin_user),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    del admin
    allowed_statuses = {"queued", "processing", "succeeded", "failed", "dead", "cancelled"}
    if status is not None and status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="Unsupported queue status.")
    with session_scope() as session:
        statement = select(WorkerJob).order_by(WorkerJob.created_at.desc()).limit(limit)
        if status:
            statement = statement.where(WorkerJob.status == status)
        jobs = list(session.exec(statement).all())
    return {"counts": queue_counts(), "jobs": [_worker_job_payload(job) for job in jobs]}


@router.get("/admin/worker/jobs/{worker_job_id}")
def admin_worker_job_detail(
    worker_job_id: int,
    admin: User = Depends(_require_admin_user),
) -> dict[str, Any]:
    del admin
    with session_scope() as session:
        job = session.get(WorkerJob, worker_job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Worker job not found.")
        return {"job": _worker_job_payload(job)}


@router.get("/admin/worker/heartbeats")
@router.get("/admin/workers")
def admin_workers(admin: User = Depends(_require_admin_user)) -> dict[str, Any]:
    del admin
    stale_before = utc_now_dt() - timedelta(seconds=max(30, settings.WORKER_HEARTBEAT_SECONDS * 3))
    with session_scope() as session:
        workers = list(
            session.exec(
                select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc())
            ).all()
        )
    return {
        "workers": [
            {
                "workerId": worker.worker_id,
                "role": worker.worker_role,
                "hostname": worker.hostname,
                "pid": worker.pid,
                "status": "stale" if _worker_is_stale(worker.last_seen_at, stale_before) else worker.status,
                "startedAt": _iso(worker.started_at),
                "lastSeenAt": _iso(worker.last_seen_at),
                "metadata": worker.metadata_json,
            }
            for worker in workers
        ]
    }


@router.post("/admin/worker/jobs/{worker_job_id}/retry")
@router.post("/admin/worker-queue/{worker_job_id}/retry")
def admin_retry_worker_job(
    worker_job_id: int,
    admin: User = Depends(_require_admin_user),
) -> dict[str, Any]:
    del admin
    try:
        job = retry_job(worker_job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkerQueueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job": _worker_job_payload(job)}


@router.post("/strategy-signals/intake")
def intake_strategy_signal(
    body: StrategySignalIntakeRequest,
    admin: User = Depends(_require_admin_user),
) -> dict[str, Any]:
    try:
        signal = accept_strategy_signal(body.model_dump())
    except (StrategySignalError, DuplicateStrategySignal) as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    queue_job = queue_strategy_signal_fanout(
        int(signal.id),
        created_by_user_id=admin.id,
    )
    if settings.PAPER_QUEUE_INLINE_LOCAL:
        from app.services.worker_runtime import drain_worker_jobs

        drain_worker_jobs()
    with session_scope() as session:
        jobs = list(
            session.exec(
                select(UserSignalJob).where(UserSignalJob.strategy_signal_id == signal.id)
            ).all()
        )
    return {
        "status": "accepted",
        "strategy_signal_id": signal.id,
        "fanout_status": "queued" if not settings.PAPER_QUEUE_INLINE_LOCAL else "processed_inline",
        "queue_job_id": queue_job.id,
        "fanout_jobs_created": len(jobs),
        "fanout_jobs_skipped": sum(job.status != "paper_filled" for job in jobs),
    }


def _strategy_payload(strategy: Strategy, version: StrategyVersion | None) -> dict[str, Any]:
    return {
        "id": strategy.id,
        "strategyCode": strategy.strategy_code,
        "name": strategy.name,
        "description": strategy.description,
        "riskLevel": strategy.risk_level,
        "market": strategy.market,
        "instrumentType": strategy.instrument_type,
        "timeframe": strategy.timeframe,
        "active": strategy.is_active,
        "paperAllowed": strategy.is_paper_allowed,
        "liveAllowed": strategy.is_live_allowed,
        "activeVersion": version.version if version else None,
    }


def _subscription_payload(
    subscription: UserStrategySubscription,
    strategy: Strategy,
    version: StrategyVersion,
) -> dict[str, Any]:
    with session_scope() as session:
        day_start = _india_day_start_utc()
        jobs_today = list(
            session.exec(
                select(UserSignalJob).where(
                    UserSignalJob.user_id == subscription.user_id,
                    UserSignalJob.subscription_id == subscription.id,
                    UserSignalJob.created_at >= day_start,
                )
            ).all()
        )
        last_job = session.exec(
            select(UserSignalJob)
            .where(
                UserSignalJob.user_id == subscription.user_id,
                UserSignalJob.subscription_id == subscription.id,
            )
            .order_by(UserSignalJob.created_at.desc())
        ).first()
    return {
        "id": subscription.id,
        "strategyId": subscription.strategy_id,
        "strategyCode": strategy.strategy_code,
        "strategyName": strategy.name,
        "strategyVersion": version.version,
        "mode": subscription.mode,
        "status": subscription.status,
        "risk": {
            "maxQty": subscription.max_qty,
            "maxDailyLoss": subscription.max_daily_loss,
            "maxTradesPerDay": subscription.max_trades_per_day,
            "allowedOptionSide": subscription.allowed_option_side,
        },
        "lastSignalAt": _iso(last_job.created_at) if last_job else None,
        "signalsToday": len(jobs_today),
        "paperTradesToday": sum(job.status == "paper_filled" for job in jobs_today),
        "skippedToday": sum(job.status.startswith("skipped_") or job.status == "paper_rejected" for job in jobs_today),
        "createdAt": _iso(subscription.created_at),
        "updatedAt": _iso(subscription.updated_at),
    }


def _subscription_by_id_payload(user_id: str, subscription_id: int) -> dict[str, Any]:
    rows = list_user_subscriptions(user_id)
    for subscription, strategy, version in rows:
        if subscription.id == subscription_id:
            return _subscription_payload(subscription, strategy, version)
    raise HTTPException(status_code=404, detail="Strategy subscription not found.")


def _change_subscription(operation, user_id: str, subscription_id: int) -> dict[str, Any]:
    try:
        operation(user_id, subscription_id)
    except SubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _subscription_by_id_payload(user_id, subscription_id)


def _job_payload(session, job: UserSignalJob) -> dict[str, Any]:
    signal = session.get(StrategySignal, job.strategy_signal_id)
    strategy = session.get(Strategy, job.strategy_id)
    order = session.get(PaperStrategyOrder, job.paper_order_id) if job.paper_order_id else None
    live_order = session.exec(
        select(LiveOrderJob).where(LiveOrderJob.user_signal_job_id == job.id)
    ).first()
    return {
        "id": job.id,
        "subscriptionId": job.subscription_id,
        "strategySignalId": job.strategy_signal_id,
        "strategyCode": signal.strategy_code if signal else None,
        "strategyName": strategy.name if strategy else None,
        "mode": job.mode,
        "status": job.status,
        "reasonCode": job.reason_code,
        "reasonMessage": job.reason_message,
        "createdAt": _iso(job.created_at),
        "startedAt": _iso(job.started_at),
        "finishedAt": _iso(job.finished_at),
        "attemptCount": job.attempt_count,
        "signal": {
            "signalId": signal.signal_id,
            "symbol": signal.symbol,
            "action": signal.action,
            "optionType": signal.option_type,
            "strike": signal.strike,
            "expiry": signal.expiry,
            "price": signal.price,
            "receivedAt": _iso(signal.received_at),
        } if signal else None,
        "paperOrder": {
            "id": order.id,
            "qty": order.qty,
            "fillPrice": order.fill_price,
            "status": order.status,
            "createdAt": _iso(order.created_at),
        } if order else None,
        "liveOrder": {
            "id": live_order.id,
            "executorNodeId": live_order.executor_node_id,
            "status": live_order.status,
            "correlationId": live_order.correlation_id,
            "dryRun": live_order.dry_run,
            "dhanOrderId": live_order.dhan_order_id,
            "createdAt": _iso(live_order.created_at),
        } if live_order else None,
    }


def _position_payload(position: PaperPosition) -> dict[str, Any]:
    return {
        "id": position.id,
        "subscriptionId": position.subscription_id,
        "strategyId": position.strategy_id,
        "strategyVersionId": position.strategy_version_id,
        "symbol": position.symbol,
        "optionType": position.option_type,
        "strike": position.strike,
        "expiry": position.expiry,
        "side": position.side,
        "quantity": position.quantity,
        "avgEntryPrice": position.avg_entry_price,
        "status": position.status,
        "openedAt": _iso(position.opened_at),
        "closedAt": _iso(position.closed_at),
        "realizedPnl": position.realized_pnl,
        "sourceSignalId": position.source_signal_id,
        "lastSignalJobId": position.last_signal_job_id,
    }


def _ledger_payload(entry: PaperLedgerEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "subscriptionId": entry.subscription_id,
        "strategyId": entry.strategy_id,
        "strategySignalId": entry.strategy_signal_id,
        "userSignalJobId": entry.user_signal_job_id,
        "paperPositionId": entry.paper_position_id,
        "entryType": entry.entry_type,
        "amount": entry.amount,
        "quantity": entry.quantity,
        "price": entry.price,
        "realizedPnl": entry.realized_pnl,
        "description": entry.description,
        "createdAt": _iso(entry.created_at),
    }


def _worker_job_payload(job: WorkerJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "jobType": job.job_type,
        "dedupeKey": job.dedupe_key,
        "payload": job.payload_json,
        "status": job.status,
        "priority": job.priority,
        "attemptCount": job.attempt_count,
        "maxAttempts": job.max_attempts,
        "availableAt": _iso(job.available_at),
        "lockedBy": job.locked_by,
        "lockedUntil": _iso(job.locked_until),
        "createdAt": _iso(job.created_at),
        "startedAt": _iso(job.started_at),
        "finishedAt": _iso(job.finished_at),
        "lastError": job.last_error,
    }


def _india_day_start_utc() -> datetime:
    india = ZoneInfo("Asia/Kolkata")
    now = datetime.now(india)
    return datetime.combine(now.date(), time.min, tzinfo=india).astimezone(timezone.utc)


def _india_date_bounds_utc(value: str) -> tuple[datetime, datetime]:
    india = ZoneInfo("Asia/Kolkata")
    parsed = datetime.strptime(value, "%Y-%m-%d").date()
    start = datetime.combine(parsed, time.min, tzinfo=india).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _worker_is_stale(last_seen_at: datetime, stale_before: datetime) -> bool:
    if last_seen_at.tzinfo is None:
        stale_before = stale_before.replace(tzinfo=None)
    return last_seen_at < stale_before
