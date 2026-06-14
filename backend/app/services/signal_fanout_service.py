from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.auth.db import session_scope
from app.auth.models import (
    Strategy,
    StrategySignal,
    StrategyVersion,
    UserSignalJob,
    UserStrategySubscription,
    WorkerJob,
    utc_now_dt,
)
from app.services.live_execution_service import create_live_order_job
from app.services.live_pilot_service import evaluate_live_readiness
from app.services.paper_execution_service import execute_paper_job, fail_paper_job
from app.services.user_notification_service import create_user_notification
from app.services.queue_service import (
    JOB_PAPER_SIGNAL_EXECUTION,
    JOB_LIVE_ORDER_EXECUTION,
    JOB_STRATEGY_SIGNAL_FANOUT,
    enqueue_job_in_session,
)


logger = logging.getLogger("strategy_fanout")


@dataclass(frozen=True)
class FanoutResult:
    jobs_created: int
    jobs_skipped: int


def queue_strategy_signal_fanout(
    strategy_signal_id: int,
    *,
    created_by_user_id: str | None = None,
) -> WorkerJob:
    with session_scope() as session:
        signal = session.get(StrategySignal, strategy_signal_id)
        if signal is None:
            raise LookupError("Strategy signal not found.")
        queue_job = enqueue_job_in_session(
            session,
            job_type=JOB_STRATEGY_SIGNAL_FANOUT,
            payload={"strategy_signal_id": strategy_signal_id},
            dedupe_key=f"strategy-signal-fanout:{strategy_signal_id}",
            priority=50,
            max_attempts=5,
            created_by_user_id=created_by_user_id,
        )
        if signal.status not in {"fanout_completed", "partial_failed"}:
            signal.status = "fanout_queued"
            session.add(signal)
        session.commit()
        session.refresh(queue_job)
        session.expunge(queue_job)
        return queue_job


def fanout_strategy_signal(strategy_signal_id: int) -> FanoutResult:
    notifications: list[tuple[str, int]] = []
    created = 0
    skipped = 0
    try:
        with session_scope() as session:
            signal = session.get(StrategySignal, strategy_signal_id)
            if signal is None:
                raise LookupError("Strategy signal not found.")
            signal.status = "fanout_started"
            session.add(signal)
            subscription_ids = list(
                session.exec(
                    select(UserStrategySubscription.id).where(
                        UserStrategySubscription.strategy_id == signal.strategy_id,
                        UserStrategySubscription.strategy_version_id == signal.strategy_version_id,
                        UserStrategySubscription.status == "active",
                    )
                ).all()
            )

            for subscription_id in subscription_ids:
                job, was_created = _create_user_job_in_session(
                    session,
                    signal=signal,
                    subscription_id=int(subscription_id),
                )
                if not was_created:
                    skipped += 1
                else:
                    created += 1
                    notifications.append((job.user_id, int(job.id)))
                subscription = session.get(UserStrategySubscription, int(subscription_id))
                if subscription.mode == "paper":
                    enqueue_job_in_session(
                        session,
                        job_type=JOB_PAPER_SIGNAL_EXECUTION,
                        payload={"user_signal_job_id": int(job.id)},
                        dedupe_key=f"paper-signal-execution:{job.id}",
                        priority=100,
                        max_attempts=3,
                    )
                else:
                    strategy = session.get(Strategy, signal.strategy_id)
                    version = session.get(StrategyVersion, signal.strategy_version_id)
                    readiness = evaluate_live_readiness(
                        user_id=subscription.user_id,
                        strategy=strategy,
                        version=version,
                        subscription=subscription,
                    )
                    if not readiness.ready:
                        job.status = readiness.reason_code or "skipped_live_disabled"
                        job.reason_code = readiness.reason_code
                        job.reason_message = readiness.reason_message
                        job.finished_at = utc_now_dt()
                        session.add(job)
                        skipped += 1
                    else:
                        live_job = create_live_order_job(
                            session,
                            user_job=job,
                            executor_node_id=int(readiness.executor_id),
                        )
                        enqueue_job_in_session(
                            session,
                            job_type=JOB_LIVE_ORDER_EXECUTION,
                            payload={"live_order_job_id": int(live_job.id)},
                            dedupe_key=f"live-order-execution:{live_job.id}",
                            priority=90,
                            max_attempts=3,
                        )

            signal.status = "fanout_completed"
            session.add(signal)
            session.commit()
    except Exception:
        _mark_fanout_failed(strategy_signal_id)
        raise

    for user_id, user_signal_job_id in notifications:
        try:
            create_user_notification(
                user_id=user_id,
                event_type="strategy.signal.received",
                payload={
                    "strategySignalId": strategy_signal_id,
                    "userSignalJobId": user_signal_job_id,
                    "mode": _job_mode(user_signal_job_id),
                },
                dedupe_key=f"strategy-signal-received:{user_signal_job_id}",
            )
            create_user_notification(
                user_id=user_id,
                event_type="strategy.job.queued",
                payload={
                    "jobId": user_signal_job_id,
                    "strategySignalId": strategy_signal_id,
                    "status": "queued",
                    "mode": _job_mode(user_signal_job_id),
                },
                dedupe_key=f"strategy-job-queued:{user_signal_job_id}",
            )
        except Exception:
            logger.exception(
                "Failed to enqueue user notification without failing fanout: user_signal_job_id=%s",
                user_signal_job_id,
            )
    return FanoutResult(jobs_created=created, jobs_skipped=skipped)


def _create_user_job_in_session(
    session,
    *,
    signal: StrategySignal,
    subscription_id: int,
) -> tuple[UserSignalJob, bool]:
    subscription = session.get(UserStrategySubscription, subscription_id)
    if subscription is None or subscription.status != "active":
        raise LookupError("Active strategy subscription not found.")
    job = UserSignalJob(
        user_id=subscription.user_id,
        subscription_id=int(subscription.id),
        strategy_signal_id=int(signal.id),
        strategy_id=signal.strategy_id,
        strategy_version_id=signal.strategy_version_id,
        mode=subscription.mode,
        status="queued",
    )
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        existing = session.exec(
            select(UserSignalJob).where(
                UserSignalJob.user_id == subscription.user_id,
                UserSignalJob.strategy_signal_id == signal.id,
                UserSignalJob.subscription_id == subscription.id,
            )
        ).first()
        if existing is None:
            raise
        return existing, False
    return job, True


def _job_mode(user_signal_job_id: int) -> str:
    with session_scope() as session:
        job = session.get(UserSignalJob, user_signal_job_id)
        return job.mode if job else "paper"


def _mark_fanout_failed(strategy_signal_id: int) -> None:
    try:
        with session_scope() as session:
            signal = session.get(StrategySignal, strategy_signal_id)
            if signal is not None:
                signal.status = "fanout_failed"
                session.add(signal)
                session.commit()
    except Exception:
        logger.exception("Unable to persist failed fanout state: strategy_signal_id=%s", strategy_signal_id)
