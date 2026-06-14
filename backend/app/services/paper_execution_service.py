from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import func, select

from app.auth.db import session_scope
from app.auth.models import (
    PaperLedgerEntry,
    PaperPosition,
    PaperStrategyOrder,
    StrategySignal,
    UserSignalJob,
    UserStrategySubscription,
    utc_now_dt,
)
from app.config import settings
from app.services.risk_manager import _market_is_open
from app.services.user_notification_service import create_user_notification


FINAL_JOB_STATUSES = {
    "skipped_subscription_paused",
    "skipped_risk_not_set",
    "skipped_daily_trade_limit",
    "skipped_daily_loss_limit",
    "skipped_market_closed",
    "skipped_duplicate",
    "skipped_invalid_instrument",
    "paper_filled",
    "paper_rejected",
    "failed",
}
EXIT_ACTIONS = {"SELL", "EXIT", "CLOSE"}


def execute_paper_job(job_id: int) -> UserSignalJob:
    with session_scope() as session:
        job = session.get(UserSignalJob, job_id)
        if job is None:
            raise LookupError("Paper signal job not found.")
        if job.status in FINAL_JOB_STATUSES:
            return _detach_job(session, job)
        subscription = session.get(UserStrategySubscription, job.subscription_id)
        signal = session.get(StrategySignal, job.strategy_signal_id)
        if subscription is None or signal is None:
            return _finish(session, job, "failed", "missing_dependency", "Job dependencies are missing.")

        job.status = "processing"
        job.started_at = job.started_at or utc_now_dt()
        job.attempt_count += 1
        session.add(job)
        session.commit()
        _notify_job(job, "strategy.job.processing")

        if subscription.user_id != job.user_id:
            return _finish(session, job, "failed", "user_scope_mismatch", "User scope validation failed.")
        if subscription.status != "active":
            return _finish(
                session,
                job,
                "skipped_subscription_paused",
                "subscription_not_active",
                "Skipped: strategy subscription is not active.",
            )
        if subscription.mode != "paper" or job.mode != "paper":
            return _finish(session, job, "failed", "live_mode_blocked", "Live execution is disabled in Stage 5B.")
        if not _risk_is_configured(subscription):
            return _finish(
                session,
                job,
                "skipped_risk_not_set",
                "risk_not_configured",
                "Skipped: quantity, daily loss, and daily trade limits must be configured.",
            )
        if not _valid_instrument(signal, subscription):
            return _finish(
                session,
                job,
                "skipped_invalid_instrument",
                "invalid_instrument",
                "Skipped: signal instrument or option side is not allowed.",
            )
        if settings.REQUIRE_MARKET_HOURS and not _market_is_open():
            return _finish(
                session,
                job,
                "skipped_market_closed",
                "market_closed",
                "Skipped: market-hours check failed.",
            )
        if signal.price is None or float(signal.price) <= 0:
            return _finish(
                session,
                job,
                "paper_rejected",
                "missing_signal_price",
                "Paper order rejected: a positive signal price is required.",
            )

        if signal.action == "BUY":
            return _execute_buy(session, job, subscription, signal)
        if signal.action in EXIT_ACTIONS:
            return _execute_exit(session, job, subscription, signal)
        return _finish(session, job, "paper_rejected", "unsupported_action", "Paper action is unsupported.")


def fail_paper_job(job_id: int, message: str = "Paper execution failed safely.") -> UserSignalJob:
    with session_scope() as session:
        job = session.get(UserSignalJob, job_id)
        if job is None:
            raise LookupError("Paper signal job not found.")
        if job.status in FINAL_JOB_STATUSES:
            return _detach_job(session, job)
        return _finish(session, job, "failed", "paper_execution_failed", message)


def _execute_buy(
    session,
    job: UserSignalJob,
    subscription: UserStrategySubscription,
    signal: StrategySignal,
) -> UserSignalJob:
    day_start = _india_day_start_utc()
    trades_today = session.exec(
        select(func.count())
        .select_from(PaperLedgerEntry)
        .where(
            PaperLedgerEntry.user_id == job.user_id,
            PaperLedgerEntry.subscription_id == subscription.id,
            PaperLedgerEntry.entry_type == "order_fill",
            PaperLedgerEntry.created_at >= day_start,
        )
    ).one()
    if int(subscription.max_trades_per_day or 0) > 0 and int(trades_today or 0) >= int(
        subscription.max_trades_per_day or 0
    ):
        return _finish(
            session,
            job,
            "skipped_daily_trade_limit",
            "daily_trade_limit",
            "Skipped: daily Paper trade limit reached.",
        )

    realized_today = session.exec(
        select(func.coalesce(func.sum(PaperLedgerEntry.realized_pnl), 0.0)).where(
            PaperLedgerEntry.user_id == job.user_id,
            PaperLedgerEntry.subscription_id == subscription.id,
            PaperLedgerEntry.created_at >= day_start,
        )
    ).one()
    if float(subscription.max_daily_loss or 0) > 0 and float(realized_today or 0) <= -float(
        subscription.max_daily_loss or 0
    ):
        return _finish(
            session,
            job,
            "skipped_daily_loss_limit",
            "daily_loss_limit",
            "Skipped: daily Paper loss limit reached.",
        )

    open_position = _open_position_query(job, signal)
    if session.exec(open_position).first() is not None:
        return _finish(
            session,
            job,
            "skipped_duplicate",
            "position_already_open",
            "Skipped: this subscription already has an open Paper position for the instrument.",
        )

    quantity = int(subscription.max_qty or 0)
    fill_price = float(signal.price or 0)
    now = utc_now_dt()
    order = PaperStrategyOrder(
        user_id=job.user_id,
        subscription_id=int(subscription.id),
        strategy_signal_id=int(signal.id),
        strategy_id=job.strategy_id,
        strategy_version_id=job.strategy_version_id,
        action=signal.action,
        option_type=signal.option_type,
        symbol=signal.symbol,
        strike=signal.strike,
        expiry=signal.expiry,
        qty=quantity,
        fill_price=fill_price,
        status="filled",
    )
    session.add(order)
    try:
        session.flush()
        position = PaperPosition(
            user_id=job.user_id,
            subscription_id=int(subscription.id),
            strategy_id=job.strategy_id,
            strategy_version_id=job.strategy_version_id,
            symbol=signal.symbol,
            option_type=str(signal.option_type),
            strike=float(signal.strike),
            expiry=str(signal.expiry),
            quantity=quantity,
            avg_entry_price=fill_price,
            opened_at=now,
            source_signal_id=int(signal.id),
            last_signal_job_id=int(job.id),
            created_at=now,
            updated_at=now,
        )
        session.add(position)
        session.flush()
        session.add(
            PaperLedgerEntry(
                user_id=job.user_id,
                subscription_id=int(subscription.id),
                strategy_id=job.strategy_id,
                strategy_signal_id=int(signal.id),
                user_signal_job_id=int(job.id),
                paper_position_id=int(position.id),
                entry_type="order_fill",
                amount=round(quantity * fill_price, 2),
                quantity=quantity,
                price=fill_price,
                description="Paper BUY filled and position opened.",
            )
        )
        job.paper_order_id = int(order.id)
        job.status = "paper_filled"
        job.reason_code = "paper_position_opened"
        job.reason_message = "Paper BUY filled and the position was opened."
        job.finished_at = now
        session.add(job)
        session.commit()
        session.refresh(job)
        result = _detach_job(session, job)
    except IntegrityError:
        session.rollback()
        if _paper_effect_exists(job_id=int(job.id)):
            return _mark_duplicate_after_rollback(int(job.id))
        raise

    _notify_job(result, "strategy.job.completed")
    _notify_position(
        result,
        "paper.position.opened",
        position_id=int(position.id),
        realized_pnl=0.0,
    )
    return result


def _execute_exit(
    session,
    job: UserSignalJob,
    subscription: UserStrategySubscription,
    signal: StrategySignal,
) -> UserSignalJob:
    position = session.exec(_open_position_query(job, signal)).first()
    fill_price = float(signal.price or 0)
    if position is None:
        _record_rejected_order(session, job, subscription, signal, fill_price)
        return _finish(
            session,
            job,
            "paper_rejected",
            "no_open_position",
            "Paper exit rejected: no matching open position exists.",
        )

    quantity = int(position.quantity)
    realized_pnl = round((fill_price - float(position.avg_entry_price)) * quantity, 2)
    now = utc_now_dt()
    close_result = session.execute(
        update(PaperPosition)
        .where(PaperPosition.id == position.id, PaperPosition.status == "open")
        .values(
            status="closed",
            closed_at=now,
            realized_pnl=realized_pnl,
            last_signal_job_id=int(job.id),
            updated_at=now,
        )
        .returning(PaperPosition.id)
    ).scalar_one_or_none()
    if close_result is None:
        _record_rejected_order(session, job, subscription, signal, fill_price)
        return _finish(
            session,
            job,
            "paper_rejected",
            "no_open_position",
            "Paper exit rejected: the matching position was already closed.",
        )

    order = PaperStrategyOrder(
        user_id=job.user_id,
        subscription_id=int(subscription.id),
        strategy_signal_id=int(signal.id),
        strategy_id=job.strategy_id,
        strategy_version_id=job.strategy_version_id,
        action=signal.action,
        option_type=signal.option_type,
        symbol=signal.symbol,
        strike=signal.strike,
        expiry=signal.expiry,
        qty=quantity,
        fill_price=fill_price,
        status="filled",
        realized_pnl=realized_pnl,
    )
    session.add(order)
    try:
        session.flush()
        session.add(
            PaperLedgerEntry(
                user_id=job.user_id,
                subscription_id=int(subscription.id),
                strategy_id=job.strategy_id,
                strategy_signal_id=int(signal.id),
                user_signal_job_id=int(job.id),
                paper_position_id=int(position.id),
                entry_type="position_close",
                amount=round(quantity * fill_price, 2),
                quantity=quantity,
                price=fill_price,
                realized_pnl=realized_pnl,
                description="Paper position closed.",
            )
        )
        job.paper_order_id = int(order.id)
        job.status = "paper_filled"
        job.reason_code = "paper_position_closed"
        job.reason_message = "Paper exit filled and the matching position was closed."
        job.finished_at = now
        session.add(job)
        session.commit()
        session.refresh(job)
        result = _detach_job(session, job)
    except IntegrityError:
        session.rollback()
        if _paper_effect_exists(job_id=int(job.id)):
            return _mark_duplicate_after_rollback(int(job.id))
        raise

    _notify_job(result, "strategy.job.completed")
    _notify_position(
        result,
        "paper.position.closed",
        position_id=int(position.id),
        realized_pnl=realized_pnl,
    )
    return result


def _finish(
    session,
    job: UserSignalJob,
    status: str,
    reason_code: str,
    message: str,
) -> UserSignalJob:
    job.status = status
    job.reason_code = reason_code
    job.reason_message = message
    job.finished_at = utc_now_dt()
    session.add(job)
    _add_risk_block_ledger(session, job, reason_code, message)
    session.commit()
    session.refresh(job)
    result = _detach_job(session, job)
    event_type = "strategy.job.failed" if status == "failed" else "strategy.job.skipped"
    _notify_job(result, event_type)
    return result


def _add_risk_block_ledger(session, job: UserSignalJob, reason_code: str, message: str) -> None:
    if reason_code in {"missing_dependency", "user_scope_mismatch"}:
        return
    existing = session.exec(
        select(PaperLedgerEntry.id).where(
            PaperLedgerEntry.user_signal_job_id == job.id,
            PaperLedgerEntry.entry_type == "risk_block",
        )
    ).first()
    if existing is not None:
        return
    session.add(
        PaperLedgerEntry(
            user_id=job.user_id,
            subscription_id=job.subscription_id,
            strategy_id=job.strategy_id,
            strategy_signal_id=job.strategy_signal_id,
            user_signal_job_id=int(job.id),
            entry_type="risk_block",
            description=f"{reason_code}: {message}"[:500],
        )
    )


def _record_rejected_order(
    session,
    job: UserSignalJob,
    subscription: UserStrategySubscription,
    signal: StrategySignal,
    fill_price: float,
) -> None:
    existing = session.exec(
        select(PaperStrategyOrder.id).where(
            PaperStrategyOrder.user_id == job.user_id,
            PaperStrategyOrder.subscription_id == subscription.id,
            PaperStrategyOrder.strategy_signal_id == signal.id,
        )
    ).first()
    if existing is not None:
        return
    session.add(
        PaperStrategyOrder(
            user_id=job.user_id,
            subscription_id=int(subscription.id),
            strategy_signal_id=int(signal.id),
            strategy_id=job.strategy_id,
            strategy_version_id=job.strategy_version_id,
            action=signal.action,
            option_type=signal.option_type,
            symbol=signal.symbol,
            strike=signal.strike,
            expiry=signal.expiry,
            qty=int(subscription.max_qty or 1),
            fill_price=fill_price,
            status="rejected",
        )
    )


def _open_position_query(job: UserSignalJob, signal: StrategySignal):
    return select(PaperPosition).where(
        PaperPosition.user_id == job.user_id,
        PaperPosition.subscription_id == job.subscription_id,
        PaperPosition.symbol == signal.symbol,
        PaperPosition.option_type == signal.option_type,
        PaperPosition.strike == signal.strike,
        PaperPosition.expiry == signal.expiry,
        PaperPosition.side == "LONG",
        PaperPosition.status == "open",
    )


def _paper_effect_exists(job_id: int) -> bool:
    with session_scope() as session:
        order_id = session.exec(
            select(PaperStrategyOrder.id)
            .join(UserSignalJob, UserSignalJob.paper_order_id == PaperStrategyOrder.id)
            .where(UserSignalJob.id == job_id)
        ).first()
        ledger_id = session.exec(
            select(PaperLedgerEntry.id).where(PaperLedgerEntry.user_signal_job_id == job_id)
        ).first()
        return order_id is not None or ledger_id is not None


def _mark_duplicate_after_rollback(job_id: int) -> UserSignalJob:
    with session_scope() as session:
        job = session.get(UserSignalJob, job_id)
        if job is None:
            raise LookupError("Paper signal job not found.")
        return _finish(
            session,
            job,
            "skipped_duplicate",
            "duplicate_paper_effect",
            "Skipped: this user signal was already applied.",
        )


def _detach_job(session, job: UserSignalJob) -> UserSignalJob:
    session.expunge(job)
    return job


def _notify_job(job: UserSignalJob, event_type: str) -> None:
    try:
        create_user_notification(
            user_id=job.user_id,
            event_type=event_type,
            payload={
                "jobId": job.id,
                "strategySignalId": job.strategy_signal_id,
                "status": job.status,
                "reason": job.reason_message,
                "paperOrderId": job.paper_order_id,
                "mode": "paper",
            },
            dedupe_key=f"{event_type}:{job.id}:{job.attempt_count}",
        )
    except Exception:
        # Execution state is authoritative. Notification delivery is retriable but
        # must not roll back a completed Paper transaction.
        return


def _notify_position(
    job: UserSignalJob,
    event_type: str,
    *,
    position_id: int,
    realized_pnl: float,
) -> None:
    try:
        create_user_notification(
            user_id=job.user_id,
            event_type=event_type,
            payload={
                "jobId": job.id,
                "positionId": position_id,
                "strategySignalId": job.strategy_signal_id,
                "realizedPnl": realized_pnl,
                "mode": "paper",
            },
            dedupe_key=f"{event_type}:{job.id}",
        )
    except Exception:
        return


def _risk_is_configured(subscription: UserStrategySubscription) -> bool:
    return (
        subscription.max_qty is not None
        and subscription.max_qty > 0
        and subscription.max_daily_loss is not None
        and subscription.max_daily_loss >= 0
        and subscription.max_trades_per_day is not None
        and subscription.max_trades_per_day >= 0
    )


def _valid_instrument(signal: StrategySignal, subscription: UserStrategySubscription) -> bool:
    if signal.symbol.upper() != "NIFTY":
        return False
    if signal.option_type not in {"CE", "PE"}:
        return False
    if signal.strike is None or signal.strike <= 0 or not signal.expiry:
        return False
    return subscription.allowed_option_side == "BOTH" or signal.option_type == subscription.allowed_option_side


def _india_day_start_utc() -> datetime:
    india = ZoneInfo("Asia/Kolkata")
    now = datetime.now(india)
    return datetime.combine(now.date(), time.min, tzinfo=india).astimezone(timezone.utc)
