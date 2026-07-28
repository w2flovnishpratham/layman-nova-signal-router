"""Fan one normalized TradingView strategy signal out to its subscribers."""
from __future__ import annotations

import ipaddress
import json
import logging
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
from app.services import (
    entitlements,
    live_engine,
    setup_configuration,
    strategy_risk,
)
from app.services import (
    user_credential_vault as vault,
)
from app.services.execution_context import bind_execution_context
from app.services.execution_router import route_signal
from app.services.state_store import get_runtime_settings, init_runtime_files
from app.services.user_context import CurrentUser, current_user_from_model

_STRATEGY_LOCKS: dict[str, threading.Lock] = {}
_STRATEGY_LOCKS_GUARD = threading.RLock()
logger = logging.getLogger("nova_signal_router.strategy_fanout")


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
    if execution_mode == "real_orders":
        entitlements.require_live_entitlement_for_user(user_id)
        entitlements.require_strategy_entitlement_for_user(user_id)

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
        # Same-transaction strategy-instance mirror (lots/mode/lifecycle). A
        # sync failure rolls back this subscription write too — success is
        # never returned with divergent effective settings.
        from app.services.strategy_instance_service import (
            sync_instance_from_subscription,
        )

        sync_instance_from_subscription(db, row)
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
        # Same-transaction lifecycle mirror onto the strategy instance; a
        # failure rolls back this subscription change too (no silent drift).
        from app.services.strategy_instance_service import (
            sync_instance_from_subscription,
        )

        sync_instance_from_subscription(db, row)
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


def active_routing_user_ids(*, real_orders_only: bool = False) -> list[uuid.UUID]:
    """Return distinct users whose active subscription needs background care."""
    modes = ["real_orders"] if real_orders_only else ["paper_live_data", "real_orders"]
    with session_scope() as db:
        return list(
            db.scalars(
                select(models.StrategySubscription.user_id)
                .where(
                    models.StrategySubscription.active.is_(True),
                    models.StrategySubscription.execution_mode.in_(modes),
                )
                .distinct()
            ).all()
        )


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


def enqueue_strategy_signal(
    strategy_name: str,
    signal: NormalizedSignal,
) -> dict[str, Any]:
    """Atomically claim an inbound alert and enqueue one immutable job per user."""
    strategy_name = canonical_strategy_name(strategy_name)
    try:
        with session_scope() as db:
            signal_row = models.StrategySignal(
                strategy_name=strategy_name,
                signal_id=signal.signal_id,
                status="queued",
            )
            db.add(signal_row)
            db.flush()

            subscriptions = db.scalars(
                select(models.StrategySubscription).where(
                    models.StrategySubscription.strategy_name == strategy_name,
                    models.StrategySubscription.active.is_(True),
                )
            ).all()
            raw_payload = (
                {
                    key: value
                    for key, value in signal.raw_payload.items()
                    if key.lower() != "secret"
                }
                if isinstance(signal.raw_payload, dict)
                else {}
            )
            signal_payload = signal.model_copy(
                update={"secret": "", "raw_payload": raw_payload}
            ).model_dump(mode="json")
            queued_count = 0
            blocked_results: list[dict[str, Any]] = []
            for subscription in subscriptions:
                configuration_mode = setup_configuration.configuration_mode_for_execution(
                    subscription.execution_mode
                )
                strategy_instance_id = db.scalar(
                    select(models.StrategyInstance.id).where(
                        models.StrategyInstance.legacy_subscription_id
                        == subscription.id
                    )
                )
                configuration = (
                    setup_configuration.configuration_for_instance_row(
                        db,
                        subscription.user_id,
                        strategy_instance_id,
                        configuration_mode,
                        lock=True,
                    )
                    if configuration_mode is not None
                    and strategy_instance_id is not None
                    else None
                )
                if configuration_mode is not None and configuration is None:
                    blocked_results.append(
                        {
                            "user_id": str(subscription.user_id),
                            "status": "failed",
                            "reason": "CONFIGURATION_REVISION_REQUIRED",
                        }
                    )
                    db.add(
                        models.StrategyExecutionJob(
                            strategy_signal_id=signal_row.id,
                            user_id=subscription.user_id,
                            strategy_name=strategy_name,
                            signal_id=signal.signal_id,
                            signal_payload=signal_payload,
                            lots=subscription.lots,
                            execution_mode=subscription.execution_mode,
                            status="failed",
                            completed_at=_now(),
                            last_error="CONFIGURATION_REVISION_REQUIRED",
                        )
                    )
                    continue
                configured_lots = (
                    int((configuration.configuration_json or {}).get("lots") or 0)
                    if configuration is not None
                    else subscription.lots
                )
                db.add(
                    models.StrategyExecutionJob(
                        strategy_signal_id=signal_row.id,
                        user_id=subscription.user_id,
                        strategy_name=strategy_name,
                        signal_id=signal.signal_id,
                        signal_payload=signal_payload,
                        lots=configured_lots,
                        execution_mode=subscription.execution_mode,
                        configuration_revision_id=(
                            configuration.id if configuration is not None else None
                        ),
                        configuration_revision=(
                            configuration.revision if configuration is not None else None
                        ),
                        status="queued",
                    )
                )
                queued_count += 1

            if not subscriptions:
                signal_row.status = "completed"
                signal_row.result_summary = {
                    "strategy_name": strategy_name,
                    "signal_id": signal.signal_id,
                    "subscriber_count": 0,
                    "results": [],
                }
            elif queued_count == 0:
                signal_row.status = "completed_with_errors"
                signal_row.result_summary = {
                    "strategy_name": strategy_name,
                    "signal_id": signal.signal_id,
                    "subscriber_count": len(subscriptions),
                    "results": blocked_results,
                }
            db.flush()
            return {
                "accepted": True,
                "strategy_name": strategy_name,
                "signal_id": signal.signal_id,
                "subscriber_count": len(subscriptions),
            }
    except IntegrityError:
        return {
            "accepted": False,
            "strategy_name": strategy_name,
            "signal_id": signal.signal_id,
            "subscriber_count": 0,
        }


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


def _aws_proxy_slot_node(slot: Any) -> dict[str, Any]:
    return {
        "provider": slot.provider,
        "slot_number": slot.slot_number,
        "slot_code": f"aws-slot-{slot.slot_number}",
        "label": f"Nova Static IP {slot.slot_number}",
        "public_ip": slot.public_ip,
        "expected_egress_ip": slot.expected_egress_ip,
        "private_ip": slot.private_ip,
        "proxy_host": slot.proxy_host,
        "proxy_port": slot.proxy_port,
        "proxy_username": slot.proxy_username,
        # Backend-only. Never include this field in frontend/API option output.
        "proxy_url": slot.proxy_url,
    }


def _configured_aws_proxy_slot_nodes() -> list[dict[str, Any]]:
    from app.services.aws_proxy_slots import build_aws_proxy_slots

    return [_aws_proxy_slot_node(slot) for slot in build_aws_proxy_slots(settings)]


def _configured_legacy_manual_egress_nodes() -> list[dict[str, Any]]:
    """Legacy manual egress JSON fallback.

    AWS proxy slots are the preferred Nova Static IP path when enabled. This
    parser remains only for old backend-managed proxy URL deployments.
    """
    try:
        raw_nodes = json.loads(settings.EGRESS_NODES_JSON or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("EGRESS_NODES_JSON is not valid JSON.") from exc
    if not isinstance(raw_nodes, list):
        raise TypeError("EGRESS_NODES_JSON must contain a JSON list.")

    nodes: list[dict[str, str]] = []
    seen_ips: set[str] = set()
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            raise TypeError("Each egress node must be a JSON object.")
        public_ip = _validated_public_ip(str(raw_node.get("public_ip") or ""))
        proxy_url = _validated_proxy_url(str(raw_node.get("proxy_url") or ""))
        if public_ip in seen_ips:
            raise ValueError(f"Duplicate egress node IP: {public_ip}")
        seen_ips.add(public_ip)
        nodes.append({"public_ip": public_ip, "proxy_url": proxy_url})
    return nodes


def configured_egress_nodes() -> list[dict[str, Any]]:
    if settings.AWS_PROXY_SLOTS_ENABLED:
        return _configured_aws_proxy_slot_nodes()
    return _configured_legacy_manual_egress_nodes()


def _safe_node_option(node: dict[str, Any], *, selected: bool) -> dict[str, Any]:
    return {
        "public_ip": node["public_ip"],
        "expected_egress_ip": node.get("expected_egress_ip", node["public_ip"]),
        "provider": node.get("provider"),
        "slot_number": node.get("slot_number"),
        "label": node.get("label") or node["public_ip"],
        "available": selected,
        "selected": selected,
    }


def _node_for_public_ip(nodes: list[dict[str, Any]], public_ip: str | None) -> dict[str, Any] | None:
    if not public_ip:
        return None
    return next((node for node in nodes if node["public_ip"] == public_ip), None)


def _assigned_public_ips(configured_ips: set[str]) -> set[str]:
    if not database_configured() or not configured_ips:
        return set()
    with session_scope() as db:
        rows = db.scalars(
            select(models.UserEgress).where(
                models.UserEgress.active.is_(True),
                models.UserEgress.public_ip.in_(configured_ips),
            )
        ).all()
        return {row.public_ip for row in rows if row.public_ip}


def _assign_first_available_user_egress(user_id: uuid.UUID, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    if not nodes:
        raise ValueError("No Nova Static IP is configured.")

    existing = user_egress_status(user_id)
    existing_node = _node_for_public_ip(nodes, existing.get("public_ip"))
    if existing.get("active") and existing_node:
        return existing

    assigned_ips = _assigned_public_ips({node["public_ip"] for node in nodes})
    candidates = [node for node in nodes if node["public_ip"] not in assigned_ips]
    if not candidates:
        raise ValueError("No Nova Static IP is currently available.")

    last_error: ValueError | None = None
    for node in candidates:
        try:
            return set_user_egress(
                user_id,
                public_ip=node["public_ip"],
                proxy_url=node["proxy_url"],
                active=True,
                allow_proxy_host_mismatch=True,
            )
        except ValueError as exc:
            if "already assigned" not in str(exc):
                raise
            last_error = exc
    raise ValueError("No Nova Static IP is currently available.") from last_error


def user_egress_options(user_id: uuid.UUID) -> dict[str, Any]:
    entitlements.require_static_ip_entitlement_for_user(user_id)
    nodes = configured_egress_nodes()
    if not nodes:
        return {
            "nodes": [],
            "egress": user_egress_status(user_id),
        }

    status = user_egress_status(user_id)
    selected_node = _node_for_public_ip(nodes, status.get("public_ip"))
    if not (status.get("active") and selected_node):
        status = _assign_first_available_user_egress(user_id, nodes)
        status = user_egress_status(user_id)
        selected_node = _node_for_public_ip(nodes, status.get("public_ip"))

    return {
        "nodes": [_safe_node_option(selected_node, selected=True)] if selected_node else [],
        "egress": status,
    }


def select_user_egress(user_id: uuid.UUID, public_ip: str) -> dict[str, Any]:
    entitlements.require_static_ip_entitlement_for_user(user_id)
    public_ip = _validated_public_ip(public_ip)
    nodes = configured_egress_nodes()
    if _node_for_public_ip(nodes, public_ip) is None:
        raise ValueError("That egress IP is not configured.")
    assignment = _assign_first_available_user_egress(user_id, nodes)
    assigned_ip = assignment.get("public_ip")
    if assigned_ip != public_ip:
        raise ValueError("Nova Static IP is assigned server-side.")
    verification = verify_user_egress(user_id)
    return {"assignment": assignment, "verification": verification}


def set_user_egress(
    user_id: uuid.UUID,
    *,
    public_ip: str,
    proxy_url: str,
    active: bool = True,
    allow_proxy_host_mismatch: bool = False,
) -> dict[str, Any]:
    public_ip = _validated_public_ip(public_ip)
    proxy_url = _validated_proxy_url(proxy_url)
    if not allow_proxy_host_mismatch and urlparse(proxy_url).hostname != public_ip:
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
            "expected_egress_ip": row.public_ip,
            "proxy_url": proxy_url,
            "active": row.active,
            "verified": _egress_is_verified(row),
            "last_observed_ip": row.last_observed_ip,
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


def active_user_egress_assignments() -> list[dict[str, Any]]:
    if not database_configured():
        return []
    with session_scope() as db:
        rows = db.scalars(
            select(models.UserEgress).where(models.UserEgress.active.is_(True))
        ).all()
        return [
            {
                "user_id": str(row.user_id),
                "public_ip": row.public_ip,
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
            for row in rows
        ]


def mark_user_egress_unverified(
    user_id: uuid.UUID | str,
    *,
    error: str,
    observed_ip: str | None = None,
) -> dict[str, Any] | None:
    if not database_configured():
        return None
    user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    with session_scope() as db:
        row = db.scalar(
            select(models.UserEgress).where(models.UserEgress.user_id == user_uuid)
        )
        if row is None:
            return None
        row.last_verified_at = None
        if observed_ip is not None:
            row.last_observed_ip = observed_ip
        row.verification_error = error
        row.updated_at = _now()
        return {
            "user_id": str(row.user_id),
            "public_ip": row.public_ip,
            "active": row.active,
            "verified": False,
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
    except Exception as exc:  # noqa: BLE001 - network verification must fail closed
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


def load_user_context(user_id: uuid.UUID) -> CurrentUser | None:
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
            except Exception as exc:  # noqa: BLE001 - isolate one subscriber failure
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
    return dispatch_signal_job(
        user_id=subscription.user_id,
        strategy_name=subscription.strategy_name,
        lots=subscription.lots,
        execution_mode=subscription.execution_mode,
        signal=signal,
    )


def dispatch_signal_job(
    *,
    user_id: uuid.UUID,
    strategy_name: str,
    lots: int,
    execution_mode: str,
    signal: NormalizedSignal,
    signal_enricher: Any | None = None,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one immutable user job using only that user's state and proxy.

    signal_enricher (optional) runs inside the bound user execution context
    just before routing. It may return an updated NormalizedSignal (e.g. with
    a server-resolved ATM contract) or a result dict that replaces execution
    entirely (idempotent no-op / safe failure) — used by private instance
    webhooks (Phase 3A)."""
    user = load_user_context(user_id)
    if user is None:
        return {
            "user_id": str(user_id),
            "status": "skipped",
            "reason": "user_not_found",
        }

    mode = _effective_mode(execution_mode)
    base = {
        "user_id": user.id_str,
        "execution_mode": mode,
        "signal_id": signal.signal_id,
        "configuration_revision_id": (
            configuration.get("id") if configuration is not None else None
        ),
        "configuration_revision": (
            configuration.get("revision") if configuration is not None else None
        ),
    }
    if mode == "signal_only":
        return {**base, "status": "accepted"}

    egress = get_user_egress(user.id) if mode == "real_orders" else None
    if mode == "real_orders":
        if not entitlements.has_live_entitlement_for_user(user.id):
            return {**base, "status": "blocked", "reason": "live_entitlement_required"}
        if not entitlements.has_strategy_entitlement_for_user(user.id):
            return {**base, "status": "blocked", "reason": "strategy_entitlement_required"}
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

    qty = _quantity_for_subscription(lots)
    try:
        risk_decision = strategy_risk.evaluate_and_reserve_order(
            user_id=user.id,
            strategy_name=strategy_name,
            lots=lots,
            qty=qty,
            signal=signal,
            execution_mode=mode,
        )
    except Exception as exc:  # noqa: BLE001 - risk service failure blocks execution
        return {
            **base,
            "status": "blocked",
            "reason": "strategy_risk_unavailable",
            "risk_error": str(exc),
        }
    if not risk_decision.allowed:
        return {
            **base,
            "status": "blocked",
            "reason": risk_decision.reason,
            "risk": risk_decision.as_dict(),
        }

    run_id: str | None = None
    with session_scope() as db:
        run = crud.create_run(
            db,
            user_id=user.id,
            run_type="live" if mode == "real_orders" else "paper",
            strategy_name=strategy_name,
            execution_mode=mode,
            mode_config={
                "lots": lots,
                "signal_id": signal.signal_id,
                "configuration_revision_id": base["configuration_revision_id"],
                "configuration_revision": base["configuration_revision"],
            },
            status="running",
        )
        if configuration is not None:
            run.configuration_revision_id = uuid.UUID(str(configuration["id"]))
            run.configuration_revision = int(configuration["revision"])
            run.strategy_version_id = uuid.UUID(str(configuration["strategy_version_id"]))
        crud.add_audit_log(
            db,
            user_id=user.id,
            action="STRATEGY_SIGNAL_FANOUT",
            metadata={
                "strategy": strategy_name,
                "signal_id": signal.signal_id,
                "execution_mode": mode,
            },
        )
        run_id = str(run.id)

    per_user_signal = signal.model_copy(
        update={"qty": qty}
    )
    try:
        with bind_execution_context(
            user,
            proxy_url=egress.get("proxy_url") if egress else None,
            egress_ip=egress.get("public_ip") if egress else None,
            expected_egress_ip=egress.get("expected_egress_ip") if egress else None,
            observed_egress_ip=egress.get("last_observed_ip") if egress else None,
            egress_verified=bool(egress.get("verified")) if egress else False,
            egress_last_verified_at=egress.get("last_verified_at") if egress else None,
        ):
            init_runtime_files()
            execution_runtime = get_runtime_settings()
            if configuration is not None:
                execution_runtime = {
                    **execution_runtime,
                    **dict(configuration.get("risk") or {}),
                    **dict(configuration.get("configuration") or {}),
                    "configuration_revision_id": configuration["id"],
                    "configuration_revision": int(configuration["revision"]),
                }
            prepared = signal_enricher(per_user_signal) if signal_enricher else per_user_signal
            if isinstance(prepared, dict):
                # Enricher short-circuited (idempotent no-op / safe failure):
                # no broker call is made for this job.
                execution_result = prepared
            else:
                execution_result = (
                    route_signal(prepared, runtime=execution_runtime)
                    if configuration is not None
                    else route_signal(prepared)
                )
    except Exception as exc:  # noqa: BLE001 - execution boundary returns safe failure
        _update_run(run_id, "error", str(exc))
        return {**base, "run_id": run_id, "status": "error", "reason": str(exc)}

    failed = execution_result.get("success") is False or execution_result.get("blocked")
    try:
        strategy_risk.record_strategy_risk_result(
            user_id=user.id,
            strategy_name=strategy_name,
            execution_result=execution_result,
        )
    except Exception:
        logger.exception("Strategy risk result accounting failed.")
    _update_run(
        run_id,
        "error" if failed else "completed",
        execution_result.get("reason") if failed else None,
    )
    return {
        **base,
        "run_id": run_id,
        "status": (
            "blocked"
            if execution_result.get("blocked")
            else "error"
            if execution_result.get("success") is False
            else "completed"
        ),
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
