from __future__ import annotations

import secrets
import hashlib
import hmac
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.exc import IntegrityError
from sqlmodel import func, select

from app.auth.db import session_scope
from app.auth.models import (
    LiveOrderJob,
    Strategy,
    StrategySignal,
    StrategyVersion,
    UserSignalJob,
    UserStrategySubscription,
    utc_now_dt,
)
from app.config import DEFAULT_EXCHANGE_SEGMENT, DEFAULT_ORDER_TYPE, executor_shared_secrets, settings
from app.services.credential_vault import get_dhan_credentials, mask_client_id
from app.services.executor_registry_service import verified_route_for_user
from app.services.executor_signing import canonical_json_bytes, signed_executor_headers
from app.services.live_pilot_service import evaluate_live_readiness
from app.services.risk_manager import _market_is_open
from app.services.security_id_resolver import resolve_security_id_for_contract
from app.services.state_store import get_runtime_settings
from app.services.user_context import reset_current_user_id, set_current_user_id


FINAL_LIVE_STATUSES = {"dry_run_verified", "sent", "confirmed", "blocked", "failed"}


def create_live_order_job(session, *, user_job: UserSignalJob, executor_node_id: int) -> LiveOrderJob:
    live_job = LiveOrderJob(
        user_id=user_job.user_id,
        subscription_id=user_job.subscription_id,
        strategy_signal_id=user_job.strategy_signal_id,
        user_signal_job_id=int(user_job.id),
        executor_node_id=executor_node_id,
        correlation_id=f"live_{secrets.token_urlsafe(18)}",
        dry_run=bool(settings.LIVE_ORDER_DRY_RUN_ONLY),
    )
    try:
        with session.begin_nested():
            session.add(live_job)
            session.flush()
        return live_job
    except IntegrityError:
        existing = session.exec(
            select(LiveOrderJob).where(
                LiveOrderJob.user_id == user_job.user_id,
                LiveOrderJob.subscription_id == user_job.subscription_id,
                LiveOrderJob.strategy_signal_id == user_job.strategy_signal_id,
            )
        ).first()
        if existing is None:
            raise
        return existing


def execute_live_order_job(live_job_id: int, *, client: httpx.Client | None = None) -> LiveOrderJob:
    with session_scope() as session:
        job = session.get(LiveOrderJob, live_job_id)
        if job is None:
            raise LookupError("Live order job not found.")
        if job.status in FINAL_LIVE_STATUSES:
            session.expunge(job)
            return job
        user_job = session.get(UserSignalJob, job.user_signal_job_id)
        subscription = session.get(UserStrategySubscription, job.subscription_id)
        signal = session.get(StrategySignal, job.strategy_signal_id)
        strategy = session.get(Strategy, user_job.strategy_id) if user_job else None
        version = session.get(StrategyVersion, user_job.strategy_version_id) if user_job else None
        if not all((user_job, subscription, signal, strategy, version)):
            return _finish(session, job, user_job, "failed", "missing_dependency", "Live job dependencies are missing.")
        job.status = "processing"
        job.started_at = job.started_at or utc_now_dt()
        user_job.status = "processing"
        user_job.started_at = user_job.started_at or utc_now_dt()
        user_job.attempt_count += 1
        session.add(job)
        session.add(user_job)
        session.commit()
        bound_user_id = job.user_id
        bound_job_id = int(job.id)

    token = set_current_user_id(bound_user_id)
    try:
        return _execute_bound(bound_job_id, client=client)
    finally:
        reset_current_user_id(token)


def _execute_bound(live_job_id: int, *, client: httpx.Client | None) -> LiveOrderJob:
    with session_scope() as session:
        job = session.get(LiveOrderJob, live_job_id)
        user_job = session.get(UserSignalJob, job.user_signal_job_id)
        subscription = session.get(UserStrategySubscription, job.subscription_id)
        signal = session.get(StrategySignal, job.strategy_signal_id)
        strategy = session.get(Strategy, user_job.strategy_id)
        version = session.get(StrategyVersion, user_job.strategy_version_id)
        readiness = evaluate_live_readiness(
            user_id=job.user_id,
            strategy=strategy,
            version=version,
            subscription=subscription,
        )
        if not readiness.ready:
            return _finish(session, job, user_job, "blocked", readiness.reason_code, readiness.reason_message)
        route = verified_route_for_user(job.user_id)
        if route is None or route.node.id != job.executor_node_id:
            return _finish(
                session, job, user_job, "blocked", "executor_assignment_mismatch",
                "Live job executor does not match the user's verified assignment.",
            )
        if not _verified_relay_signal(signal):
            return _finish(
                session, job, user_job, "blocked", "signed_relay_required",
                "Live jobs require a verified Nova signing-relay signal.",
            )
        runtime = get_runtime_settings()
        if runtime.get("emergency_stop") or runtime.get("global_kill_switch"):
            return _finish(
                session, job, user_job, "blocked", "kill_switch_active",
                "Emergency stop or global kill switch blocked the live job.",
            )
        if settings.REQUIRE_MARKET_HOURS and not _market_is_open():
            return _finish(session, job, user_job, "blocked", "market_closed", "Market-hours check failed.")
        risk_error = _risk_error(session, job, subscription, signal)
        if risk_error:
            return _finish(session, job, user_job, "blocked", risk_error[0], risk_error[1])
        credentials = get_dhan_credentials()
        if credentials is None:
            return _finish(
                session, job, user_job, "blocked", "broker_credentials_unavailable",
                "User-scoped Dhan credentials are unavailable.",
            )
        secret = executor_shared_secrets().get(route.node.executor_code)
        if not secret:
            return _finish(
                session, job, user_job, "blocked", "executor_secret_missing",
                "Executor-specific signing secret is not configured.",
            )

        if not job.dry_run:
            # Real-order send. Every flag is re-checked here at the call site so
            # that a flag flipped after the job was queued still fails closed.
            if not settings.ENABLE_LIVE_ORDERS:
                return _finish(
                    session, job, user_job, "blocked", "live_orders_disabled",
                    "Real live order blocked because ENABLE_LIVE_ORDERS=false.",
                )
            if settings.LIVE_ORDER_DRY_RUN_ONLY:
                return _finish(
                    session, job, user_job, "blocked", "dry_run_only",
                    "Real live order blocked because LIVE_ORDER_DRY_RUN_ONLY=true.",
                )
            if not settings.EXECUTION_NODE_ROUTING_ENABLED:
                return _finish(
                    session, job, user_job, "blocked", "execution_node_routing_disabled",
                    "Real live order blocked because EXECUTION_NODE_ROUTING_ENABLED=false.",
                )
            resolution = resolve_security_id_for_contract(
                symbol=signal.symbol,
                expiry=signal.expiry or "",
                strike=signal.strike if signal.strike is not None else "",
                option_side=signal.option_type or "",
                exchange_segment=DEFAULT_EXCHANGE_SEGMENT,
            )
            if not resolution.ok or not resolution.security_id:
                return _finish(
                    session, job, user_job, "blocked", "security_id_unresolved",
                    "Real live order blocked: instrument security_id could not be resolved.",
                )
            # Short-lived credentials are placed only in this signed, single-use
            # request body. They are never persisted by the main process and the
            # token is never logged here.
            payload = {
                "correlation_id": job.correlation_id,
                "user_id": job.user_id,
                "broker_client_id": credentials.client_id,
                "dhan_access_token": credentials.access_token,
                "order": {
                    "symbol": signal.symbol,
                    "action": signal.action,
                    "option_type": signal.option_type,
                    "strike": signal.strike,
                    "expiry": signal.expiry,
                    "security_id": resolution.security_id,
                    "exchange_segment": DEFAULT_EXCHANGE_SEGMENT,
                    "quantity": int(subscription.max_qty or 0),
                    "order_type": DEFAULT_ORDER_TYPE,
                    "product_type": "INTRADAY",
                },
                "dry_run": False,
            }
            raw_body = canonical_json_bytes(payload)
            try:
                response = (client or httpx.Client(timeout=settings.EXECUTOR_REQUEST_TIMEOUT_SECONDS)).post(
                    route.node.execute_url,
                    content=raw_body,
                    headers=signed_executor_headers(route.node.executor_code, secret, raw_body),
                )
            except httpx.TimeoutException:
                return _finish(
                    session, job, user_job, "failed", "broker_timeout",
                    "Executor did not respond before timeout; broker outcome is unconfirmed.",
                )
            finally:
                # Drop the request body reference holding the token as soon as the
                # call returns, before any further processing or logging.
                raw_body = b""
                payload["dhan_access_token"] = "[REDACTED]"
            if response.status_code == 504:
                return _finish(
                    session, job, user_job, "failed", "broker_timeout",
                    "Executor reported a broker timeout; broker outcome is unconfirmed.",
                )
            safe_response = _safe_executor_response(response.json())
            status = safe_response.get("status")
            if response.is_success and status in {"sent", "confirmed"}:
                return _finish(
                    session, job, user_job, status, None,
                    f"Real order {status} through {route.node.executor_code} / {route.node.reserved_ip}.",
                    response_json=safe_response,
                    dhan_order_id=safe_response.get("order_id"),
                )
            return _finish(
                session, job, user_job, "failed", "broker_rejected",
                "Assigned executor reported a broker rejection for the real order.",
                response_json=safe_response,
                dhan_order_id=safe_response.get("order_id"),
            )

        # Dry-run send: masked client id, never a token.
        payload = {
            "correlation_id": job.correlation_id,
            "user_id": job.user_id,
            "broker_client_id": mask_client_id(credentials.client_id),
            "order": {
                "symbol": signal.symbol,
                "action": signal.action,
                "option_type": signal.option_type,
                "strike": signal.strike,
                "expiry": signal.expiry,
                "quantity": int(subscription.max_qty or 0),
                "price": signal.price,
                "product_type": "INTRADAY",
            },
            "dry_run": True,
        }
        raw_body = canonical_json_bytes(payload)
        response = (client or httpx.Client(timeout=settings.EXECUTOR_REQUEST_TIMEOUT_SECONDS)).post(
            route.node.execute_url,
            content=raw_body,
            headers=signed_executor_headers(route.node.executor_code, secret, raw_body),
        )
        safe_response = response.json()
        if not response.is_success or safe_response.get("status") != "dry_run_verified":
            return _finish(
                session, job, user_job, "failed", "executor_rejected",
                "Assigned executor rejected the signed dry-run request.",
                response_json=_safe_executor_response(safe_response),
            )
        return _finish(
            session, job, user_job, "dry_run_verified", None,
            f"Dry run verified through {route.node.executor_code} / {route.node.reserved_ip}.",
            response_json=_safe_executor_response(safe_response),
        )


def _risk_error(session, job, subscription, signal) -> tuple[str, str] | None:
    if signal.option_type not in {"CE", "PE"} or signal.strike is None or not signal.expiry:
        return "invalid_instrument", "Live job instrument fields are incomplete."
    if subscription.allowed_option_side != "BOTH" and signal.option_type != subscription.allowed_option_side:
        return "option_side_blocked", "Signal option side is outside the approved risk profile."
    day_start = _india_day_start_utc()
    trades = session.exec(
        select(func.count()).select_from(LiveOrderJob).where(
            LiveOrderJob.user_id == job.user_id,
            LiveOrderJob.subscription_id == job.subscription_id,
            LiveOrderJob.status.in_(("dry_run_verified", "sent", "confirmed")),
            LiveOrderJob.created_at >= day_start,
        )
    ).one()
    if int(trades or 0) >= int(subscription.max_trades_per_day or 0):
        return "daily_trade_limit", "Daily live-pilot trade limit reached."
    realized = session.exec(
        select(func.coalesce(func.sum(LiveOrderJob.realized_pnl), 0.0)).where(
            LiveOrderJob.user_id == job.user_id,
            LiveOrderJob.subscription_id == job.subscription_id,
            LiveOrderJob.created_at >= day_start,
        )
    ).one()
    if float(realized or 0) <= -float(subscription.max_daily_loss or 0) and float(subscription.max_daily_loss or 0) > 0:
        return "daily_loss_limit", "Daily live-pilot loss limit reached."
    return None


def _finish(session, job, user_job, status, code, message, response_json=None, dhan_order_id=None):
    now = utc_now_dt()
    job.status = status
    job.reason_code = code
    job.reason_message = message
    job.response_json = response_json or {}
    if dhan_order_id:
        job.dhan_order_id = str(dhan_order_id)
    job.finished_at = now
    if user_job is not None:
        user_job.status = {
            "dry_run_verified": "live_dry_run_verified",
            "sent": "live_sent",
            "confirmed": "live_confirmed",
            "blocked": "live_blocked",
            "failed": "failed",
        }.get(status, "failed")
        user_job.reason_code = code
        user_job.reason_message = message
        user_job.finished_at = now
        session.add(user_job)
    session.add(job)
    session.commit()
    session.refresh(job)
    session.expunge(job)
    return job


def _safe_executor_response(payload):
    allowed = {"status", "executor_code", "correlation_id", "egress_ip", "order_id", "message"}
    return {key: payload[key] for key in allowed if key in payload}


def _verified_relay_signal(signal: StrategySignal) -> bool:
    if not settings.RELAY_ENABLED or len(settings.RELAY_SHARED_SECRET.strip()) < 32:
        return False
    stored = dict(signal.raw_payload_redacted_json)
    relay = stored.pop("_nova_relay", {})
    if signal.source != "tradingview-relay" or relay.get("verified") is not True:
        return False
    try:
        timestamp = int(relay.get("timestamp"))
    except (TypeError, ValueError):
        return False
    raw_body = canonical_json_bytes(stored)
    if not hmac.compare_digest(
        hashlib.sha256(raw_body).hexdigest(),
        str(relay.get("payload_sha256") or ""),
    ):
        return False
    expected = hmac.new(
        settings.RELAY_SHARED_SECRET.strip().encode("utf-8"),
        str(timestamp).encode() + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, str(relay.get("signature") or ""))


def _india_day_start_utc() -> datetime:
    india = ZoneInfo("Asia/Kolkata")
    now = datetime.now(india)
    return datetime.combine(now.date(), time.min, tzinfo=india).astimezone(timezone.utc)
