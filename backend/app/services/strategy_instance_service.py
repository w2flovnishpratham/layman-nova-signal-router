"""Strategy-instance control plane (Phase 1).

Owner-scoped CRUD/lifecycle for StrategyInstance plus private webhook
credential issue/rotate/revoke.

Transition authority model: the legacy strategy_subscriptions row remains
EXECUTION-AUTHORITATIVE (the fan-out reads subscription.active/lots); the
strategy instance is the future authority. The two are kept transactionally
synchronized in BOTH directions — lots, execution_mode, and lifecycle
eligibility (instance active <=> subscription active) — inside the same
database transaction. A sync failure rolls the whole mutation back; it is
never swallowed. detect_drift() reports (and, only when explicitly asked,
repairs) any divergence.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import crud, models
from app.db.engine import session_scope
from app.domain.strategy_instance_state_machine import (
    InstanceState,
    StateTransitionError,
    assert_transition,
)

MIN_LOTS = 1
MAX_LOTS = 1000  # matches SubscribePayload bounds
TOKEN_PREFIX = "nwk_"
_PREFIX_DISPLAY_CHARS = 10

ALLOWED_CREATE_JOURNEYS = {"NOVA_SHARED", "PERSONAL_TRADINGVIEW"}
WEBHOOK_JOURNEYS = {"PERSONAL_TRADINGVIEW"}

# Safe, user-facing blocker codes for the first failing activation gate.
_BLOCKER_CODES = {
    "paper_mode": "PAPER_MODE_REQUIRED",
    "valid_lots": "INVALID_LOTS",
    "active_credential": "CREDENTIAL_INACTIVE",
    "connection_tested": "HOLD_NOT_VERIFIED",
    "approved_version": "PINE_NOT_APPROVED",
    "installation_confirmed": "TRADINGVIEW_NOT_INSTALLED",
    "hold_verified": "HOLD_NOT_VERIFIED",
    "paper_entry_verified": "PAPER_ENTRY_NOT_VERIFIED",
    "paper_exit_verified": "PAPER_EXIT_NOT_VERIFIED",
}


def _first_blocker(readiness: dict[str, Any], setup: "models.TradingViewSetup | None") -> str | None:
    if setup is not None and setup.status in {"BLOCKED", "RETIRED"}:
        return "SETUP_BLOCKED"
    for key, ready in readiness.items():
        if key == "can_activate":
            continue
        if not ready:
            return _BLOCKER_CODES.get(key)
    return None


class InstanceError(ValueError):
    """Domain error with an HTTP-friendly status code."""

    def __init__(self, message: str, status_code: int = 400, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _instance_public(row: models.StrategyInstance, *, strategy: models.StrategyCatalog | None = None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "strategy_id": str(row.strategy_id),
        "strategy_version_id": str(row.strategy_version_id),
        "strategy_code": strategy.code if strategy else None,
        "strategy_display_name": strategy.display_name if strategy else None,
        "source_journey": row.source_journey,
        "label": row.label,
        "status": row.status,
        "status_reason": row.status_reason,
        "execution_mode": row.execution_mode,
        "current_lots": row.current_lots,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
    }


def _decorate_personal_instance(db, row: models.StrategyInstance, payload: dict[str, Any]) -> dict[str, Any]:
    if row.source_journey != "PERSONAL_TRADINGVIEW":
        return payload
    from app.routers.setup import current_nifty_lot_size
    from app.services.private_webhook_service import instance_strategy_name

    credential = _active_credential(db, row.id)
    latest_credential = db.scalar(
        select(models.StrategyInstanceWebhookCredential)
        .where(models.StrategyInstanceWebhookCredential.strategy_instance_id == row.id)
        .order_by(models.StrategyInstanceWebhookCredential.created_at.desc())
    )
    latest = db.scalar(
        select(models.StrategySignal)
        .where(models.StrategySignal.strategy_name == instance_strategy_name(row.id))
        .order_by(models.StrategySignal.created_at.desc())
    )
    tested = db.scalar(
        select(models.StrategySignal.id).where(
            models.StrategySignal.strategy_name == instance_strategy_name(row.id),
            models.StrategySignal.signal_id.like("connection-test-%"),
            models.StrategySignal.status == "completed",
        )
    ) is not None
    lot_size = current_nifty_lot_size()

    paper_mode = row.execution_mode in {"signal_only", "paper_live_data"}
    valid_lots = MIN_LOTS <= row.current_lots <= MAX_LOTS
    active_credential = credential is not None

    # An instance running an owner's approved *personal Pine* must clear the
    # full server-observed TradingView paper gate (genuine HOLD + confirmed
    # paper entry + confirmed paper exit), never a HOLD connection test alone.
    # NOVA catalog strategies (owner_user_id IS NULL, e.g. supertrend) keep the
    # lighter connection-test gate — closing the "linked-but-no-setup" loophole
    # without changing the raw personal-webhook journey.
    catalog = db.get(models.StrategyCatalog, row.strategy_id)
    is_personal_pine = catalog is not None and catalog.owner_user_id is not None
    setup = db.scalar(
        select(models.TradingViewSetup).where(
            models.TradingViewSetup.strategy_instance_id == row.id
        )
    )

    if is_personal_pine:
        version = db.get(models.StrategyVersion, row.strategy_version_id)
        readiness = {
            "paper_mode": paper_mode,
            "valid_lots": valid_lots,
            "active_credential": active_credential,
            "approved_version": version is not None and version.status == "approved",
            "installation_confirmed": setup is not None and setup.installation_confirmed_at is not None,
            "hold_verified": setup is not None and setup.hold_verified_at is not None,
            "paper_entry_verified": setup is not None and setup.paper_entry_verified_at is not None,
            "paper_exit_verified": setup is not None and setup.paper_exit_verified_at is not None,
        }
        blocked_setup = setup is not None and setup.status in {"BLOCKED", "RETIRED"}
        readiness["can_activate"] = all(readiness.values()) and not blocked_setup
    else:
        readiness = {
            "paper_mode": paper_mode,
            "valid_lots": valid_lots,
            "active_credential": active_credential,
            "connection_tested": tested,
        }
        readiness["can_activate"] = all(readiness.values())

    payload.update(
        {
            "webhook_credential": _credential_public(credential) if credential else None,
            "credential_status": "active" if credential else "revoked" if latest_credential else "missing",
            "readiness": readiness,
            "blocking_code": _first_blocker(readiness, setup),
            "setup_type": setup.setup_type if setup else None,
            "requires_managed_setup": bool(setup and setup.setup_type == "NOVA_MANAGED_TRADINGVIEW"),
            "lot_size": lot_size,
            "estimated_quantity": row.current_lots * lot_size,
            "last_signal_time": latest.created_at.isoformat() if latest and latest.created_at else None,
            "last_execution_status": latest.status if latest else None,
        }
    )
    return payload


def _credential_public(row: models.StrategyInstanceWebhookCredential) -> dict[str, Any]:
    """Public view — never includes the token or its hash."""
    return {
        "id": str(row.id),
        "strategy_instance_id": str(row.strategy_instance_id),
        "token_prefix": row.token_prefix,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


def _owned_instance(
    db,
    user_id: uuid.UUID,
    instance_id: uuid.UUID | str,
    *,
    for_update: bool = False,
) -> models.StrategyInstance:
    query = select(models.StrategyInstance).where(
        models.StrategyInstance.id == uuid.UUID(str(instance_id))
    )
    row = db.scalar(query.with_for_update() if for_update else query)
    if row is None or row.user_id != user_id:
        # Same response whether missing or foreign: no tenant probing.
        raise InstanceError("Strategy instance not found.", status_code=404)
    return row


def _audit(db, user_id: uuid.UUID, action: str, metadata: dict[str, Any]) -> None:
    try:
        crud.add_audit_log(db, user_id=user_id, action=action, metadata=metadata)
    except Exception:
        pass


def _approved_version(db, strategy: models.StrategyCatalog) -> models.StrategyVersion:
    version = db.scalar(
        select(models.StrategyVersion)
        .where(
            models.StrategyVersion.strategy_id == strategy.id,
            models.StrategyVersion.status == "approved",
        )
        .order_by(models.StrategyVersion.created_at.desc())
    )
    if version is None:
        raise InstanceError("Strategy has no approved version.", status_code=409)
    return version


# ---------------------------------------------------------------------------
# CRUD / lifecycle
# ---------------------------------------------------------------------------

def list_instances(user_id: uuid.UUID, *, include_archived: bool = False) -> list[dict[str, Any]]:
    with session_scope() as db:
        query = (
            select(models.StrategyInstance, models.StrategyCatalog)
            .join(models.StrategyCatalog, models.StrategyCatalog.id == models.StrategyInstance.strategy_id)
            .where(models.StrategyInstance.user_id == user_id)
            .order_by(models.StrategyInstance.created_at.desc())
        )
        if not include_archived:
            query = query.where(models.StrategyInstance.status != InstanceState.ARCHIVED.value)
        # ponytail: one small decoration query set per personal instance; batch
        # when a single user can realistically own hundreds of strategies.
        return [
            _decorate_personal_instance(db, row, _instance_public(row, strategy=strategy))
            for row, strategy in db.execute(query).all()
        ]


def get_instance(user_id: uuid.UUID, instance_id: uuid.UUID | str) -> dict[str, Any]:
    with session_scope() as db:
        row = _owned_instance(db, user_id, instance_id)
        strategy = db.get(models.StrategyCatalog, row.strategy_id)
        payload = _instance_public(row, strategy=strategy)
        payload = _decorate_personal_instance(db, row, payload)
        if row.source_journey == "PERSONAL_TRADINGVIEW":
            payload["has_open_position"] = _position_is_open(user_id, row.execution_mode)
        return payload


def admin_list_instances(*, user_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    with session_scope() as db:
        query = (
            select(models.StrategyInstance, models.StrategyCatalog)
            .join(models.StrategyCatalog, models.StrategyCatalog.id == models.StrategyInstance.strategy_id)
            .order_by(models.StrategyInstance.created_at.desc())
        )
        if user_id is not None:
            query = query.where(models.StrategyInstance.user_id == user_id)
        results = []
        for row, strategy in db.execute(query).all():
            payload = _instance_public(row, strategy=strategy)
            payload["user_id"] = str(row.user_id)
            results.append(payload)
        return results


def create_instance(
    user_id: uuid.UUID,
    *,
    strategy_code: str,
    source_journey: str,
    label: str | None = None,
    lots: int = 1,
    execution_mode: str = "signal_only",
) -> dict[str, Any]:
    from app.services import entitlements, live_engine

    journey = (source_journey or "").strip().upper()
    if journey not in ALLOWED_CREATE_JOURNEYS:
        raise InstanceError(f"source_journey must be one of {sorted(ALLOWED_CREATE_JOURNEYS)}.")
    if execution_mode not in live_engine.EXECUTION_MODES:
        raise InstanceError("Invalid execution_mode.")
    if not MIN_LOTS <= lots <= MAX_LOTS:
        raise InstanceError(f"lots must be between {MIN_LOTS} and {MAX_LOTS}.")
    if execution_mode == "real_orders":
        entitlements.require_live_entitlement_for_user(user_id)
        entitlements.require_strategy_entitlement_for_user(user_id)

    with session_scope() as db:
        strategy = db.scalar(
            select(models.StrategyCatalog).where(
                models.StrategyCatalog.code == (strategy_code or "").strip().lower(),
                models.StrategyCatalog.owner_user_id.is_(None),
            )
        )
        if strategy is None or strategy.status not in {"active", "beta"}:
            raise InstanceError("Strategy not found or not available.", status_code=404)
        version = _approved_version(db, strategy)
        row = models.StrategyInstance(
            user_id=user_id,
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            source_journey=journey,
            label=(label or "").strip() or strategy.display_name,
            status=InstanceState.READY.value,
            execution_mode=execution_mode,
            current_lots=lots,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError as exc:
            raise InstanceError("An instance with this label already exists.", status_code=409) from exc
        _audit(db, user_id, "STRATEGY_INSTANCE_CREATED", {
            "instance_id": str(row.id), "strategy_code": strategy.code,
            "source_journey": journey, "lots": lots, "execution_mode": execution_mode,
        })
        return _instance_public(row, strategy=strategy)


def clone_instance(user_id: uuid.UUID, instance_id: uuid.UUID | str, *, label: str | None = None) -> dict[str, Any]:
    with session_scope() as db:
        source = _owned_instance(db, user_id, instance_id)
        strategy = db.get(models.StrategyCatalog, source.strategy_id)
        row = models.StrategyInstance(
            user_id=user_id,
            strategy_id=source.strategy_id,
            strategy_version_id=source.strategy_version_id,
            source_journey=source.source_journey,
            label=(label or "").strip() or f"{source.label} (copy)",
            status=InstanceState.READY.value,
            execution_mode=source.execution_mode,
            current_lots=source.current_lots,
            risk_config=dict(source.risk_config) if source.risk_config else None,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError as exc:
            raise InstanceError("An instance with this label already exists.", status_code=409) from exc
        _audit(db, user_id, "STRATEGY_INSTANCE_CLONED", {
            "instance_id": str(row.id), "cloned_from": str(source.id),
        })
        return _instance_public(row, strategy=strategy)


_STATUS_TIMESTAMPS = {
    InstanceState.PAUSED: "paused_at",
    InstanceState.STOPPED: "stopped_at",
    InstanceState.ARCHIVED: "archived_at",
}

# Legal multi-hop paths used when mirroring legacy subscription reactivation
# back onto an instance (every hop still goes through the state machine).
_PATHS_TO_ACTIVE: dict[str, tuple[InstanceState, ...]] = {
    InstanceState.READY.value: (InstanceState.ACTIVE,),
    InstanceState.PAUSED.value: (InstanceState.ACTIVE,),
    InstanceState.DEGRADED.value: (InstanceState.ACTIVE,),
    InstanceState.STOPPED.value: (InstanceState.READY, InstanceState.ACTIVE),
    InstanceState.ERROR.value: (InstanceState.STOPPED, InstanceState.READY, InstanceState.ACTIVE),
}


def _apply_status(row: models.StrategyInstance, target: InstanceState) -> None:
    """Single choke point for status assignment: machine-checked + timestamped."""
    assert_transition(row.status, target)
    row.status = target.value
    timestamp_field = _STATUS_TIMESTAMPS.get(target)
    if timestamp_field:
        setattr(row, timestamp_field, _now())
    row.updated_at = _now()


def _mirror_lifecycle_to_subscription(db, row: models.StrategyInstance, target: InstanceState) -> dict[str, Any] | None:
    """Instance lifecycle drives the executing subscription's eligibility.
    Same transaction: a paused/stopped/archived instance can never leave its
    legacy subscription eligible for fan-out."""
    if row.legacy_subscription_id is None:
        return None
    subscription = db.get(models.StrategySubscription, row.legacy_subscription_id)
    if subscription is None:
        return None
    desired_active = target is InstanceState.ACTIVE
    changed = subscription.active != desired_active
    if changed:
        subscription.active = desired_active
        subscription.updated_at = _now()
    return {"subscription_id": str(subscription.id), "active": desired_active, "changed": changed}


def _set_status(
    user_id: uuid.UUID,
    instance_id: uuid.UUID | str,
    target: InstanceState,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    if target is InstanceState.ACTIVE:
        # Re-validate before re-enabling execution eligibility, same gates as
        # subscribing (reads happen outside the mutation transaction).
        from app.services import entitlements

        instance_mode = _instance_execution_mode(user_id, instance_id)
        if instance_mode == "real_orders":
            entitlements.require_live_entitlement_for_user(user_id)
            entitlements.require_strategy_entitlement_for_user(user_id)
    with session_scope() as db:
        row = _owned_instance(db, user_id, instance_id, for_update=True)
        if target is InstanceState.ACTIVE and row.source_journey == "PERSONAL_TRADINGVIEW":
            decorated = _decorate_personal_instance(db, row, {})
            readiness = decorated["readiness"]
            if not readiness["can_activate"]:
                missing = [key.replace("_", " ") for key, ready in readiness.items() if key != "can_activate" and not ready]
                raise InstanceError(
                    f"Personal strategy is not ready: {', '.join(missing)}.",
                    status_code=409,
                    code=decorated.get("blocking_code"),
                )
        if (
            target is InstanceState.STOPPED
            and row.source_journey == "PERSONAL_TRADINGVIEW"
            and _position_is_open(user_id, row.execution_mode)
        ):
            raise InstanceError(
                "Close the open position before stopping this strategy. "
                "You may pause it now to block new entries while keeping exits available.",
                status_code=409,
                code="POSITION_OPEN",
            )
        previous = row.status
        try:
            _apply_status(row, target)
        except (StateTransitionError, ValueError) as exc:
            raise InstanceError(str(exc), status_code=409) from exc
        row.status_reason = reason
        mirrored = _mirror_lifecycle_to_subscription(db, row, target)
        if target is InstanceState.ARCHIVED and row.legacy_subscription_id is not None:
            # Archived instances are immutable history: stop mirroring so a
            # future re-subscribe creates a fresh instance instead.
            row.legacy_subscription_id = None
        _audit(db, user_id, "STRATEGY_INSTANCE_STATUS", {
            "instance_id": str(row.id), "from": previous, "to": target.value, "reason": reason,
            "subscription_sync": mirrored,
        })
        strategy = db.get(models.StrategyCatalog, row.strategy_id)
        return _instance_public(row, strategy=strategy)


def _instance_execution_mode(user_id: uuid.UUID, instance_id: uuid.UUID | str) -> str:
    with session_scope() as db:
        return _owned_instance(db, user_id, instance_id).execution_mode


def _position_is_open(user_id: uuid.UUID, execution_mode: str) -> bool:
    """Read the owner's JSON execution authority for the requested mode."""
    if execution_mode == "signal_only":
        return False
    from app.services import strategy_fanout
    from app.services.execution_context import bind_user_execution_context
    from app.services.state_store import (
        get_live_open_position,
        get_paper_position,
        init_runtime_files,
    )

    user = strategy_fanout.load_user_context(user_id)
    if user is None:
        raise InstanceError("Position state is unavailable.", status_code=503)
    with bind_user_execution_context(user):
        init_runtime_files()
        position = (
            get_paper_position()
            if execution_mode == "paper_live_data"
            else get_live_open_position()
        )
    return bool(position.get("has_open_position"))


def activate_instance(user_id: uuid.UUID, instance_id: uuid.UUID | str) -> dict[str, Any]:
    return _set_status(user_id, instance_id, InstanceState.ACTIVE)


def pause_instance(user_id: uuid.UUID, instance_id: uuid.UUID | str, *, reason: str | None = None) -> dict[str, Any]:
    return _set_status(user_id, instance_id, InstanceState.PAUSED, reason=reason)


def resume_instance(user_id: uuid.UUID, instance_id: uuid.UUID | str) -> dict[str, Any]:
    return _set_status(user_id, instance_id, InstanceState.ACTIVE)


def stop_instance(user_id: uuid.UUID, instance_id: uuid.UUID | str, *, reason: str | None = None) -> dict[str, Any]:
    return _set_status(user_id, instance_id, InstanceState.STOPPED, reason=reason)


def archive_instance(user_id: uuid.UUID, instance_id: uuid.UUID | str) -> dict[str, Any]:
    return _set_status(user_id, instance_id, InstanceState.ARCHIVED)


def update_lots(user_id: uuid.UUID, instance_id: uuid.UUID | str, lots: int) -> dict[str, Any]:
    if not MIN_LOTS <= int(lots) <= MAX_LOTS:
        raise InstanceError(f"lots must be between {MIN_LOTS} and {MAX_LOTS}.")
    with session_scope() as db:
        row = _owned_instance(db, user_id, instance_id)
        if row.status == InstanceState.ARCHIVED.value:
            raise InstanceError("Archived instances cannot be modified.", status_code=409)
        previous = row.current_lots
        row.current_lots = int(lots)
        row.updated_at = _now()
        # Dual-write: keep the executing legacy subscription in lockstep so
        # there is never a second effective lot value during the transition.
        if row.legacy_subscription_id is not None:
            subscription = db.get(models.StrategySubscription, row.legacy_subscription_id)
            if subscription is not None:
                subscription.lots = int(lots)
                subscription.updated_at = _now()
        _audit(db, user_id, "STRATEGY_INSTANCE_LOTS_UPDATED", {
            "instance_id": str(row.id), "from": previous, "to": int(lots),
        })
        strategy = db.get(models.StrategyCatalog, row.strategy_id)
        return _instance_public(row, strategy=strategy)


def _create_instance_for_subscription(db, subscription: models.StrategySubscription) -> bool:
    """Create the NOVA_SHARED instance mirroring one legacy subscription.
    Returns False when the strategy is not (yet) in the catalog."""
    strategy = db.scalar(
        select(models.StrategyCatalog).where(
            models.StrategyCatalog.code == subscription.strategy_name,
            models.StrategyCatalog.owner_user_id.is_(None),
        )
    )
    if strategy is None:
        return False
    version = db.scalar(
        select(models.StrategyVersion)
        .where(
            models.StrategyVersion.strategy_id == strategy.id,
            models.StrategyVersion.status == "approved",
        )
        .order_by(models.StrategyVersion.created_at.desc())
    )
    if version is None:
        return False
    label = strategy.display_name
    label_taken = db.scalar(
        select(models.StrategyInstance.id).where(
            models.StrategyInstance.user_id == subscription.user_id,
            models.StrategyInstance.strategy_id == strategy.id,
            models.StrategyInstance.label == label,
        )
    )
    if label_taken is not None:  # e.g. an archived predecessor kept the name
        label = f"{strategy.display_name} ({str(subscription.id)[:8]})"
    db.add(
        models.StrategyInstance(
            user_id=subscription.user_id,
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            source_journey="NOVA_SHARED",
            label=label,
            status=(InstanceState.ACTIVE if subscription.active else InstanceState.PAUSED).value,
            execution_mode=subscription.execution_mode,
            current_lots=subscription.lots,
            legacy_subscription_id=subscription.id,
        )
    )
    return True


def _mirror_subscription_state_onto_instance(row: models.StrategyInstance, subscription: models.StrategySubscription) -> None:
    """Copy lots/mode and walk the instance's lifecycle to match the
    subscription's eligibility. Every hop goes through the state machine."""
    row.current_lots = subscription.lots
    row.execution_mode = subscription.execution_mode
    if subscription.active:
        if row.status != InstanceState.ACTIVE.value:
            path = _PATHS_TO_ACTIVE.get(row.status)
            if path is None:
                raise StateTransitionError(
                    f"Cannot mirror subscription reactivation onto instance in status '{row.status}'."
                )
            for step in path:
                _apply_status(row, step)
    elif row.status == InstanceState.ACTIVE.value:
        # paused/stopped/ready/error are all already non-eligible states.
        _apply_status(row, InstanceState.PAUSED)
    row.updated_at = _now()


def sync_instance_from_subscription(db, subscription: models.StrategySubscription) -> None:
    """Mirror a legacy subscription mutation onto its instance, creating the
    instance when missing (called inside the SAME session/transaction as the
    subscription write). Raises on failure so the whole mutation rolls back —
    success must never be returned with divergent effective settings."""
    row = db.scalar(
        select(models.StrategyInstance).where(
            models.StrategyInstance.legacy_subscription_id == subscription.id
        )
    )
    if row is None:
        _create_instance_for_subscription(db, subscription)
        return
    if row.status == InstanceState.ARCHIVED.value:
        # Archived instances are immutable history: detach and mirror into a
        # fresh instance for the re-activated subscription.
        row.legacy_subscription_id = None
        db.flush()
        _create_instance_for_subscription(db, subscription)
        return
    _mirror_subscription_state_onto_instance(row, subscription)


# ---------------------------------------------------------------------------
# Webhook credentials
# ---------------------------------------------------------------------------

def _active_credential(db, instance_id: uuid.UUID) -> models.StrategyInstanceWebhookCredential | None:
    return db.scalar(
        select(models.StrategyInstanceWebhookCredential).where(
            models.StrategyInstanceWebhookCredential.strategy_instance_id == instance_id,
            models.StrategyInstanceWebhookCredential.revoked_at.is_(None),
        )
    )


def _issue_credential(db, row: models.StrategyInstance) -> tuple[models.StrategyInstanceWebhookCredential, str]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    # Ownership is derived through the instance — no duplicated user_id.
    credential = models.StrategyInstanceWebhookCredential(
        strategy_instance_id=row.id,
        token_hash=_hash_token(token),
        token_prefix=token[:_PREFIX_DISPLAY_CHARS],
    )
    db.add(credential)
    db.flush()
    return credential, token


def _reset_setup_connectivity(db, instance_id: uuid.UUID) -> None:
    """A new/revoked secret requires a fresh server-observed HOLD alert."""
    setup = db.scalar(select(models.TradingViewSetup).where(
        models.TradingViewSetup.strategy_instance_id == instance_id
    ))
    if setup is not None:
        setup.hold_signal_id = None
        setup.hold_verified_at = None
        setup.status = "ALERT_TEST_PENDING"
        setup.blocking_reason = "Webhook credential changed; send a new HOLD alert."
        setup.updated_at = _now()


def generate_webhook_credential(user_id: uuid.UUID, instance_id: uuid.UUID | str) -> dict[str, Any]:
    with session_scope() as db:
        row = _owned_instance(db, user_id, instance_id)
        if row.source_journey not in WEBHOOK_JOURNEYS:
            raise InstanceError("Webhook credentials are only available for personal TradingView instances.", status_code=409)
        if _active_credential(db, row.id) is not None:
            raise InstanceError("An active webhook credential already exists. Rotate it instead.", status_code=409)
        credential, token = _issue_credential(db, row)
        _audit(db, user_id, "INSTANCE_WEBHOOK_CREDENTIAL_CREATED", {
            "instance_id": str(row.id), "credential_id": str(credential.id),
        })
        payload = _credential_public(credential)
        payload["token"] = token  # plaintext shown exactly once
        return payload


def rotate_webhook_credential(user_id: uuid.UUID, instance_id: uuid.UUID | str) -> dict[str, Any]:
    with session_scope() as db:
        row = _owned_instance(db, user_id, instance_id)
        current = _active_credential(db, row.id)
        if current is None:
            raise InstanceError("No active webhook credential to rotate.", status_code=404)
        current.revoked_at = _now()
        current.revoked_reason = "rotated"
        db.flush()  # release the partial-unique slot before inserting the new one
        credential, token = _issue_credential(db, row)
        current.replaced_by_id = credential.id
        _reset_setup_connectivity(db, row.id)
        _audit(db, user_id, "INSTANCE_WEBHOOK_CREDENTIAL_ROTATED", {
            "instance_id": str(row.id), "old_credential_id": str(current.id),
            "new_credential_id": str(credential.id),
        })
        payload = _credential_public(credential)
        payload["token"] = token
        return payload


def revoke_webhook_credential(user_id: uuid.UUID, instance_id: uuid.UUID | str, *, reason: str = "user_request") -> dict[str, Any]:
    with session_scope() as db:
        row = _owned_instance(db, user_id, instance_id)
        current = _active_credential(db, row.id)
        if current is None:
            raise InstanceError("No active webhook credential to revoke.", status_code=404)
        current.revoked_at = _now()
        current.revoked_reason = (reason or "user_request")[:120]
        _reset_setup_connectivity(db, row.id)
        _audit(db, user_id, "INSTANCE_WEBHOOK_CREDENTIAL_REVOKED", {
            "instance_id": str(row.id), "credential_id": str(current.id), "reason": reason,
        })
        return _credential_public(current)


def admin_generate_managed_credential(
    admin_id: uuid.UUID, setup_id: uuid.UUID | str, *, rotate: bool = False
) -> dict[str, Any]:
    """Issue the private-webhook credential for a NOVA-managed setup on behalf
    of the owning instance. The non-Premium user never handles this secret; the
    plaintext is returned exactly once to the authorized admin installer and is
    stored only as a hash. Rotation revokes the previous credential."""
    with session_scope() as db:
        setup = db.scalar(
            select(models.TradingViewSetup)
            .where(models.TradingViewSetup.id == uuid.UUID(str(setup_id)))
            .with_for_update()
        )
        if setup is None:
            raise InstanceError("TradingView setup not found.", status_code=404)
        if setup.setup_type != "NOVA_MANAGED_TRADINGVIEW":
            raise InstanceError(
                "Managed credentials are issued only for NOVA-managed setups.",
                status_code=409,
                code="NOT_MANAGED_SETUP",
            )
        instance = db.get(models.StrategyInstance, setup.strategy_instance_id)
        if instance is None:
            raise InstanceError("Strategy instance not found.", status_code=404)
        current = _active_credential(db, instance.id)
        if current is not None:
            if not rotate:
                raise InstanceError(
                    "An active managed credential already exists. Rotate it instead.",
                    status_code=409,
                    code="CREDENTIAL_EXISTS",
                )
            current.revoked_at = _now()
            current.revoked_reason = "managed_rotated"
            db.flush()  # release the partial-unique active slot before reissue
        credential, token = _issue_credential(db, instance)
        if current is not None:
            current.replaced_by_id = credential.id
        _reset_setup_connectivity(db, instance.id)  # new secret ⇒ fresh HOLD required
        _audit(db, admin_id, "MANAGED_TRADINGVIEW_CREDENTIAL_ISSUED", {
            "setup_id": str(setup.id), "instance_id": str(instance.id),
            "credential_id": str(credential.id), "rotated": current is not None,
        })
        payload = _credential_public(credential)
        payload["token"] = token  # plaintext shown exactly once, to the admin installer
        return payload


def list_engine_strategies(user_id: uuid.UUID) -> dict[str, Any]:
    """Owner-scoped engine strategy picker.

    Returns this user's runnable strategy instances (personal Pine and NOVA
    shared) with a single, unified eligibility verdict. A private strategy is
    ``selectable`` only when it clears the same activation gate the lifecycle
    controls enforce — no separate readiness standard. Never leaks credentials,
    other users, or admin/broker internals.
    """
    with session_scope() as db:
        query = (
            select(models.StrategyInstance, models.StrategyCatalog)
            .join(models.StrategyCatalog, models.StrategyCatalog.id == models.StrategyInstance.strategy_id)
            .where(
                models.StrategyInstance.user_id == user_id,
                models.StrategyInstance.status != InstanceState.ARCHIVED.value,
            )
            .order_by(models.StrategyInstance.created_at.desc())
        )
        strategies = []
        for row, catalog in db.execute(query).all():
            decorated = _decorate_personal_instance(db, row, _instance_public(row, strategy=catalog))
            readiness = decorated.get("readiness")
            not_runnable = row.status in {InstanceState.STOPPED.value, InstanceState.ARCHIVED.value}
            if readiness is not None:  # PERSONAL_TRADINGVIEW
                selectable = bool(readiness["can_activate"]) and not not_runnable
                blocking = decorated.get("blocking_code") or (
                    "STRATEGY_STOPPED" if row.status == InstanceState.STOPPED.value else None
                )
            else:  # NOVA_SHARED
                selectable = not not_runnable
                blocking = "STRATEGY_STOPPED" if row.status == InstanceState.STOPPED.value else None
            strategies.append({
                "instance_id": str(row.id),
                "display_name": row.label,
                "source_type": row.source_journey,
                "setup_type": decorated.get("setup_type"),
                "status": "READY" if selectable else (blocking or row.status.upper()),
                "instance_status": row.status,
                "selectable": selectable,
                "blocking_reason": blocking,
                "owner": "self",
            })
        return {"strategies": strategies}


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def backfill_instances_from_subscriptions() -> dict[str, int]:
    """Create one NOVA_SHARED instance per existing subscription whose strategy
    resolves in the catalog. Idempotent via the unique legacy_subscription_id."""
    created = 0
    skipped = 0
    with session_scope() as db:
        subscriptions = db.scalars(select(models.StrategySubscription)).all()
        for subscription in subscriptions:
            existing = db.scalar(
                select(models.StrategyInstance.id).where(
                    models.StrategyInstance.legacy_subscription_id == subscription.id
                )
            )
            if existing is not None:
                skipped += 1
                continue
            if _create_instance_for_subscription(db, subscription):
                created += 1
            else:
                skipped += 1
        db.flush()
    return {"created": created, "skipped": skipped}


# ---------------------------------------------------------------------------
# Drift detection (report-only by default; repair is explicit and audited)
# ---------------------------------------------------------------------------

def detect_drift(*, repair: bool = False) -> dict[str, Any]:
    """Compare every linked instance/subscription pair.

    Findings: lots_mismatch, execution_mode_mismatch, lifecycle_mismatch,
    missing_subscription (dangling link), missing_instance.
    With repair=True the subscription — the execution authority during the
    transition — wins: its values are copied onto the instance, and missing
    instances are created. missing_subscription is always report-only.
    Every repair is audit-logged.
    """
    findings: list[dict[str, Any]] = []
    repaired = 0
    with session_scope() as db:
        subscriptions = {s.id: s for s in db.scalars(select(models.StrategySubscription)).all()}
        linked = db.scalars(
            select(models.StrategyInstance).where(
                models.StrategyInstance.legacy_subscription_id.is_not(None)
            )
        ).all()
        linked_subscription_ids = set()
        for row in linked:
            subscription = subscriptions.get(row.legacy_subscription_id)
            if subscription is None:
                findings.append({
                    "type": "missing_subscription",
                    "instance_id": str(row.id),
                    "user_id": str(row.user_id),
                    "repairable": False,
                })
                continue
            linked_subscription_ids.add(subscription.id)
            drift_types = []
            if row.current_lots != subscription.lots:
                drift_types.append("lots_mismatch")
            if row.execution_mode != subscription.execution_mode:
                drift_types.append("execution_mode_mismatch")
            instance_eligible = row.status == InstanceState.ACTIVE.value
            if instance_eligible != bool(subscription.active):
                drift_types.append("lifecycle_mismatch")
            for drift_type in drift_types:
                findings.append({
                    "type": drift_type,
                    "instance_id": str(row.id),
                    "subscription_id": str(subscription.id),
                    "user_id": str(row.user_id),
                    "instance": {"lots": row.current_lots, "execution_mode": row.execution_mode, "status": row.status},
                    "subscription": {"lots": subscription.lots, "execution_mode": subscription.execution_mode, "active": subscription.active},
                    "repairable": True,
                })
            if repair and drift_types:
                _mirror_subscription_state_onto_instance(row, subscription)
                repaired += 1
                _audit(db, row.user_id, "INSTANCE_DRIFT_REPAIRED", {
                    "instance_id": str(row.id),
                    "subscription_id": str(subscription.id),
                    "drift": drift_types,
                    "authority": "subscription",
                })
        for subscription in subscriptions.values():
            if subscription.id in linked_subscription_ids:
                continue
            already_linked = db.scalar(
                select(models.StrategyInstance.id).where(
                    models.StrategyInstance.legacy_subscription_id == subscription.id
                )
            )
            if already_linked is not None:
                continue
            findings.append({
                "type": "missing_instance",
                "subscription_id": str(subscription.id),
                "user_id": str(subscription.user_id),
                "repairable": True,
            })
            if repair and _create_instance_for_subscription(db, subscription):
                repaired += 1
                _audit(db, subscription.user_id, "INSTANCE_DRIFT_REPAIRED", {
                    "subscription_id": str(subscription.id),
                    "drift": ["missing_instance"],
                    "authority": "subscription",
                })
        db.flush()
    return {"findings": findings, "repaired": repaired, "repair": repair}
