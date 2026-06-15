"""Fan one normalized TradingView strategy signal out to its subscribers."""
from __future__ import annotations

import ipaddress
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import DEFAULT_STRATEGY_CODE, settings
from app.db import crud, models
from app.db.engine import database_configured, session_scope
from app.schemas.signal import NormalizedSignal
from app.services import live_engine, user_credential_vault as vault
from app.services.execution_context import bind_execution_context
from app.services.execution_router import route_signal
from app.services.state_store import init_runtime_files
from app.services.user_context import CurrentUser, current_user_from_model


_STRATEGY_LOCKS: dict[str, threading.Lock] = {}
_STRATEGY_LOCKS_GUARD = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_strategy_name(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("_", "-")
    aliases = {
        "supertrend": "supertrend",
        DEFAULT_STRATEGY_CODE.lower().replace("_", "-"): "supertrend",
    }
    return aliases.get(normalized, normalized)


def subscribe_user(
    user_id: uuid.UUID,
    strategy_name: str,
    *,
    lots: int = 1,
    execution_mode: str = "signal_only",
) -> dict[str, Any]:
    strategy_name = canonical_strategy_name(strategy_name)
    if not strategy_name:
        raise ValueError("strategy_name is required.")
    if execution_mode not in live_engine.EXECUTION_MODES:
        raise ValueError(f"Invalid execution_mode '{execution_mode}'.")
    if lots < 1:
        raise ValueError("lots must be at least 1.")

    with session_scope() as db:
        row = db.scalar(
            select(models.StrategySubscription).where(
                models.StrategySubscription.user_id == user_id,
                models.StrategySubscription.strategy_name == strategy_name,
            )
        )
        if row is None:
            row = models.StrategySubscription(
                user_id=user_id,
                strategy_name=strategy_name,
                lots=lots,
                execution_mode=execution_mode,
                active=True,
            )
            db.add(row)
        else:
            row.active = True
            row.lots = lots
            row.execution_mode = execution_mode
            row.updated_at = _now()
        db.flush()
        return _sub_public(row)


def set_subscription_active(
    user_id: uuid.UUID,
    strategy_name: str,
    active: bool,
) -> bool:
    strategy_name = canonical_strategy_name(strategy_name)
    with session_scope() as db:
        row = db.scalar(
            select(models.StrategySubscription).where(
                models.StrategySubscription.user_id == user_id,
                models.StrategySubscription.strategy_name == strategy_name,
            )
        )
        if row is None:
            return False
        row.active = active
        row.updated_at = _now()
        db.flush()
        return True


def unsubscribe_user(user_id: uuid.UUID, strategy_name: str) -> bool:
    return set_subscription_active(user_id, strategy_name, False)


def list_user_subscriptions(user_id: uuid.UUID) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.scalars(
            select(models.StrategySubscription)
            .where(models.StrategySubscription.user_id == user_id)
            .order_by(models.StrategySubscription.created_at.desc())
        ).all()
        return [_sub_public(row) for row in rows]


def _sub_public(row: models.StrategySubscription) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "strategy_name": row.strategy_name,
        "active": row.active,
        "lots": row.lots,
        "execution_mode": row.execution_mode,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def active_subscriber_count(strategy_name: str) -> int:
    with session_scope() as db:
        return int(
            db.scalar(
                select(func.count(models.StrategySubscription.id)).where(
                    models.StrategySubscription.strategy_name
                    == canonical_strategy_name(strategy_name),
                    models.StrategySubscription.active.is_(True),
                )
            )
            or 0
        )


def active_subscribers(strategy_name: str) -> list[models.StrategySubscription]:
    with session_scope() as db:
        rows = db.scalars(
            select(models.StrategySubscription).where(
                models.StrategySubscription.strategy_name
                == canonical_strategy_name(strategy_name),
                models.StrategySubscription.active.is_(True),
            )
        ).all()
        for row in rows:
            db.expunge(row)
        return list(rows)


def claim_strategy_signal(strategy_name: str, signal_id: str) -> bool:
    """Atomically claim a signal so retries cannot fan out twice."""
    try:
        with session_scope() as db:
            db.add(
                models.StrategySignal(
                    strategy_name=canonical_strategy_name(strategy_name),
                    signal_id=signal_id,
                    status="accepted",
                )
            )
            db.flush()
        return True
    except IntegrityError:
        return False


def _finish_strategy_signal(
    strategy_name: str,
    signal_id: str,
    *,
    status: str,
    summary: dict[str, Any],
) -> None:
    with session_scope() as db:
        row = db.scalar(
            select(models.StrategySignal).where(
                models.StrategySignal.strategy_name
                == canonical_strategy_name(strategy_name),
                models.StrategySignal.signal_id == signal_id,
            )
        )
        if row is not None:
            row.status = status
            row.result_summary = summary
            row.updated_at = _now()


def _validated_public_ip(public_ip: str) -> str:
    address = ipaddress.ip_address((public_ip or "").strip())
    if not address.is_global:
        raise ValueError("public_ip must be a public internet address.")
    return str(address)


def _validated_proxy_url(proxy_url: str) -> str:
    parsed = urlparse((proxy_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port:
        raise ValueError("proxy_url must be an http(s) URL with an explicit port.")
    return parsed.geturl()


def set_user_egress(
    user_id: uuid.UUID,
    *,
    public_ip: str,
    proxy_url: str,
    active: bool = True,
) -> dict[str, Any]:
    public_ip = _validated_public_ip(public_ip)
    proxy_url = _validated_proxy_url(proxy_url)
    if urlparse(proxy_url).hostname != public_ip:
        raise ValueError("proxy_url hostname must match public_ip.")
    encrypted_proxy_url = vault._encrypt(proxy_url)
    try:
        with session_scope() as db:
            row = db.scalar(
                select(models.UserEgress).where(models.UserEgress.user_id == user_id)
            )
            if row is None:
                row = models.UserEgress(user_id=user_id)
                db.add(row)
            row.public_ip = public_ip
            row.proxy_url_encrypted = encrypted_proxy_url
            row.active = active
            row.last_verified_at = None
            row.last_observed_ip = None
            row.verification_error = None
            row.updated_at = _now()
            db.flush()
            return {
                "user_id": str(user_id),
                "public_ip": public_ip,
                "active": active,
                "has_proxy": bool(encrypted_proxy_url),
            }
    except IntegrityError as exc:
        raise ValueError("That public IP is already assigned to another user.") from exc


def get_user_egress(user_id: uuid.UUID) -> dict[str, Any] | None:
    if not database_configured():
        return None
    with session_scope() as db:
        row = db.scalar(
            select(models.UserEgress).where(models.UserEgress.user_id == user_id)
        )
        if row is None or not row.active:
            return None
        proxy_url = (
            vault._decrypt(row.proxy_url_encrypted)
            if row.proxy_url_encrypted
            else None
        )
        return {
            "public_ip": row.public_ip,
            "proxy_url": proxy_url,
            "active": row.active,
            "verified": _egress_is_verified(row),
            "last_verified_at": (
                row.last_verified_at.isoformat()
                if row.last_verified_at
                else None
            ),
        }


def user_egress_status(user_id: uuid.UUID) -> dict[str, Any]:
    if not database_configured():
        return {"public_ip": None, "active": False, "has_proxy": False}
    with session_scope() as db:
        row = db.scalar(
            select(models.UserEgress).where(models.UserEgress.user_id == user_id)
        )
        if row is None:
            return {"public_ip": None, "active": False, "has_proxy": False}
        return {
            "public_ip": row.public_ip,
            "active": row.active,
            "has_proxy": bool(row.proxy_url_encrypted),
            "verified": _egress_is_verified(row),
            "last_verified_at": (
                row.last_verified_at.isoformat()
                if row.last_verified_at
                else None
            ),
            "last_observed_ip": row.last_observed_ip,
            "verification_error": row.verification_error,
        }


def verify_user_egress(user_id: uuid.UUID, *, timeout: float = 8.0) -> dict[str, Any]:
    egress = get_user_egress(user_id)
    if egress is None or not egress.get("proxy_url"):
        return {"ok": False, "error": "No active egress proxy is assigned."}
    try:
        with httpx.Client(proxy=egress["proxy_url"], timeout=timeout) as client:
            response = client.get("https://api.ipify.org?format=json")
            response.raise_for_status()
            observed_ip = str(response.json().get("ip") or "").strip()
    except Exception as exc:
        result = {
            "ok": False,
            "error": str(exc),
            "expected_ip": egress["public_ip"],
            "observed_ip": None,
        }
        _record_egress_verification(user_id, result)
        return result
    result = {
        "ok": observed_ip == egress["public_ip"],
        "expected_ip": egress["public_ip"],
        "observed_ip": observed_ip,
    }
    if not result["ok"]:
        result["error"] = "Observed proxy IP does not match assigned public IP."
    _record_egress_verification(user_id, result)
    return result


def _record_egress_verification(
    user_id: uuid.UUID,
    result: dict[str, Any],
) -> None:
    with session_scope() as db:
        row = db.scalar(
            select(models.UserEgress).where(models.UserEgress.user_id == user_id)
        )
        if row is None:
            return
        row.last_observed_ip = result.get("observed_ip")
        row.verification_error = result.get("error")
        row.last_verified_at = _now() if result.get("ok") else None
        row.updated_at = _now()


def _egress_is_verified(row: models.UserEgress) -> bool:
    if not row.last_verified_at or row.last_observed_ip != row.public_ip:
        return False
    verified_at = row.last_verified_at
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    return verified_at >= _now() - timedelta(hours=24)


def _effective_mode(requested: str) -> str:
    return requested


def _load_user(user_id: uuid.UUID) -> CurrentUser | None:
    with session_scope() as db:
        user = crud.get_user_by_id(db, user_id)
        return current_user_from_model(user) if user else None


def _quantity_for_subscription(lots: int) -> int:
    from app.routers.setup import current_nifty_lot_size

    return max(int(lots), 1) * current_nifty_lot_size()


def process_signal(
    strategy_name: str,
    signal: NormalizedSignal,
) -> dict[str, Any]:
    """Execute one signal independently for every active subscriber."""
    strategy_name = canonical_strategy_name(strategy_name)
    with _STRATEGY_LOCKS_GUARD:
        strategy_lock = _STRATEGY_LOCKS.setdefault(
            strategy_name,
            threading.Lock(),
        )
    with strategy_lock:
        return _process_signal_serialized(strategy_name, signal)


def _process_signal_serialized(
    strategy_name: str,
    signal: NormalizedSignal,
) -> dict[str, Any]:
    subscribers = active_subscribers(strategy_name)
    results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(1, min(len(subscribers), 8))) as executor:
        futures = [
            executor.submit(_dispatch_to_subscriber, subscriber, signal)
            for subscriber in subscribers
        ]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(
                    {
                        "status": "error",
                        "reason": str(exc),
                    }
                )

    summary = {
        "strategy_name": strategy_name,
        "signal_id": signal.signal_id,
        "subscriber_count": len(subscribers),
        "results": results,
    }
    final_status = "completed" if not any(
        result.get("status") == "error" for result in results
    ) else "completed_with_errors"
    _finish_strategy_signal(
        strategy_name,
        signal.signal_id,
        status=final_status,
        summary=summary,
    )
    return summary


def _dispatch_to_subscriber(
    subscription: models.StrategySubscription,
    signal: NormalizedSignal,
) -> dict[str, Any]:
    user = _load_user(subscription.user_id)
    if user is None:
        return {
            "user_id": str(subscription.user_id),
            "status": "skipped",
            "reason": "user_not_found",
        }

    mode = _effective_mode(subscription.execution_mode)
    base = {
        "user_id": user.id_str,
        "execution_mode": mode,
        "signal_id": signal.signal_id,
    }
    if mode == "signal_only":
        return {**base, "status": "accepted"}

    egress = get_user_egress(user.id) if mode == "real_orders" else None
    if mode == "real_orders":
        if (
            not settings.ENABLE_LIVE_ORDERS
            or settings.DHAN_MODE.upper() != "REAL"
            or settings.DHAN_READ_ONLY_REAL_DATA
        ):
            return {
                **base,
                "status": "blocked",
                "reason": "live_orders_not_armed",
            }
        if not settings.EXECUTION_NODE_ROUTING_ENABLED:
            return {**base, "status": "blocked", "reason": "egress_routing_disabled"}
        if egress is None or not egress.get("proxy_url"):
            return {**base, "status": "blocked", "reason": "no_egress_node"}
        if not egress.get("verified"):
            return {**base, "status": "blocked", "reason": "egress_not_verified"}
        if vault.get_user_dhan_credentials(user.id) is None:
            return {**base, "status": "blocked", "reason": "no_credentials"}

    run_id: str | None = None
    with session_scope() as db:
        run = crud.create_run(
            db,
            user_id=user.id,
            run_type="live" if mode == "real_orders" else "paper",
            strategy_name=subscription.strategy_name,
            execution_mode=mode,
            mode_config={"lots": subscription.lots, "signal_id": signal.signal_id},
            status="running",
        )
        crud.add_audit_log(
            db,
            user_id=user.id,
            action="STRATEGY_SIGNAL_FANOUT",
            metadata={
                "strategy": subscription.strategy_name,
                "signal_id": signal.signal_id,
                "execution_mode": mode,
            },
        )
        run_id = str(run.id)

    per_user_signal = signal.model_copy(
        update={"qty": _quantity_for_subscription(subscription.lots)}
    )
    try:
        with bind_execution_context(
            user,
            proxy_url=egress.get("proxy_url") if egress else None,
            egress_ip=egress.get("public_ip") if egress else None,
        ):
            init_runtime_files()
            execution_result = route_signal(per_user_signal)
    except Exception as exc:
        _update_run(run_id, "error", str(exc))
        return {**base, "run_id": run_id, "status": "error", "reason": str(exc)}

    failed = execution_result.get("success") is False or execution_result.get("blocked")
    _update_run(
        run_id,
        "error" if failed else "completed",
        execution_result.get("reason") if failed else None,
    )
    return {
        **base,
        "run_id": run_id,
        "status": "blocked" if execution_result.get("blocked") else "completed",
        "execution_result": execution_result,
    }


def _update_run(run_id: str | None, status: str, error: str | None) -> None:
    if not run_id:
        return
    with session_scope() as db:
        run = crud.get_run(db, run_id)
        if run is not None:
            crud.update_run_status(
                db,
                run,
                status=status,
                error_message=error,
            )
