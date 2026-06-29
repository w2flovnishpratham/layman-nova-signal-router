"""Per-user live-mode engine: safety gating + run lifecycle.

This module is the safety-critical chokepoint between a logged-in user and real
money. It NEVER places a real order unless every guard passes:

  1. user is authenticated
  2. user has saved Dhan credentials
  3. those credentials decrypt successfully from the vault
  4. a basic Dhan token validation passes
  5. ENABLE_LIVE_ORDERS=true            (only when execution_mode == real_orders)
  6. WEBHOOK_TRADING_ENABLED=true       (only when webhook trading is used)
  7. a per-user webhook secret exists    (when WEBHOOK_HMAC_REQUIRED=true)

execution_mode values:
  signal_only      -> compute signals, place NO orders               (safe)
  paper_live_data  -> live market data, orders routed to paper broker (safe)
  real_orders      -> REAL orders via the user's Dhan credentials      (gated)

Runs are tracked in an in-memory per-process registry and, when a database is
configured, persisted to the user_runs table. Each user can only see and stop
their own runs.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.db import crud
from app.db.engine import database_configured, session_scope
from app.services import user_credential_vault as vault
from app.services.dhan_client import RealDhanClient
from app.services.execution_context import (
    LiveEgressGuardError,
    bind_user_execution_context,
    current_execution_context,
    require_verified_live_egress,
)
from app.services.user_context import CurrentUser, user_runtime_log_dir, user_runtime_state_dir

EXECUTION_MODES = {"signal_only", "paper_live_data", "real_orders"}

_REGISTRY: dict[str, dict[str, "RunHandle"]] = {}  # user_id_str -> {run_id_str -> handle}
_LOCK = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RunHandle:
    run_id: str
    user_id: str
    run_type: str
    strategy_name: str | None
    execution_mode: str
    status: str
    config: dict[str, Any]
    created_at: str
    started_at: str | None = None
    stopped_at: str | None = None
    log: list[dict[str, Any]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_type": self.run_type,
            "strategy_name": self.strategy_name,
            "execution_mode": self.execution_mode,
            "status": self.status,
            "config": self.config,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
        }


# ---------------------------------------------------------------------------
# Safety checklist
# ---------------------------------------------------------------------------
def _basic_token_validation(creds) -> bool:
    """Offline shape check used before optional live Dhan profile validation."""
    if creds is None:
        return False
    return bool(creds.client_id and creds.access_token and len(creds.access_token) >= 20)


def _validate_dhan_token_for_real_orders(user: CurrentUser, creds) -> dict[str, Any]:
    """Validate a live-order token with a read-only Dhan profile call."""
    if creds is None:
        return {"ok": False, "status_code": None, "message": "Dhan token is missing or expired."}

    def _validate_with_bound_egress() -> dict[str, Any]:
        context = require_verified_live_egress()
        expected_ip = context.expected_egress_ip or context.egress_ip
        validation = RealDhanClient(
            proxy_url=context.proxy_url,
            expected_egress_ip=expected_ip,
        ).validate_token(
            client_id=creds.client_id,
            access_token=creds.access_token,
        )
        if not validation.success:
            return {
                "ok": False,
                "status_code": validation.status_code,
                "message": "Dhan token validation failed.",
            }
        return {
            "ok": True,
            "status_code": validation.status_code,
            "message": "Dhan token validation passed.",
        }

    try:
        context = current_execution_context()
        if context is not None and context.user.id == user.id:
            return _validate_with_bound_egress()
        with bind_user_execution_context(user):
            return _validate_with_bound_egress()
    except LiveEgressGuardError:
        return {"ok": False, "status_code": None, "message": "Verified egress is required for live readiness."}
    except Exception:
        return {"ok": False, "status_code": None, "message": "Live readiness validation failed."}


def evaluate_live_readiness(user: CurrentUser, execution_mode: str, *, uses_webhook: bool = False) -> dict[str, Any]:
    execution_mode = (execution_mode or "signal_only").strip().lower()
    creds = None
    decrypt_ok = False
    try:
        creds = vault.get_user_dhan_credentials(user.id)
        decrypt_ok = creds is not None
    except Exception:
        decrypt_ok = False

    has_creds = creds is not None
    token_ok = _basic_token_validation(creds)
    webhook_secret_present = False
    try:
        webhook_secret_present = bool(vault.get_user_webhook_secret(user.id))
    except Exception:
        webhook_secret_present = False
    credential_status = vault.user_credential_status(user.id)
    token_saved_at = credential_status.get("dhan_token_saved_at")
    token_age_hours: float | None = None
    if token_saved_at:
        try:
            saved_at = datetime.fromisoformat(str(token_saved_at).replace("Z", "+00:00"))
            if saved_at.tzinfo is None:
                saved_at = saved_at.replace(tzinfo=timezone.utc)
            token_age_hours = max((_now() - saved_at).total_seconds() / 3600, 0)
        except (TypeError, ValueError):
            token_age_hours = None

    egress_status: dict[str, Any] = {}
    subscriptions: list[dict[str, Any]] = []
    worker_status: dict[str, Any] = {}
    try:
        from app.services import strategy_fanout
        from app.workers.strategy_job_worker import strategy_job_worker_status

        egress_status = strategy_fanout.user_egress_status(user.id)
        subscriptions = strategy_fanout.list_user_subscriptions(user.id)
        worker_status = strategy_job_worker_status()
    except Exception:
        pass

    checks = {
        "logged_in": True,
        "has_saved_credentials": has_creds,
        "vault_decrypt_ok": decrypt_ok,
        "dhan_token_basic_valid": token_ok,
        "dhan_token_profile_valid": None,
        "dhan_token_validation_method": None,
        "dhan_token_validation_status_code": None,
        "enable_live_orders_env": bool(settings.ENABLE_LIVE_ORDERS),
        "webhook_trading_enabled_env": bool(settings.WEBHOOK_TRADING_ENABLED),
        "webhook_hmac_required": bool(settings.WEBHOOK_HMAC_REQUIRED),
        "webhook_secret_present": webhook_secret_present,
        "dhan_mode": settings.DHAN_MODE.upper(),
        "dhan_read_only_real_data": bool(settings.DHAN_READ_ONLY_REAL_DATA),
        "execution_node_routing_enabled": bool(settings.EXECUTION_NODE_ROUTING_ENABLED),
        "egress_assigned": bool(egress_status.get("public_ip")),
        "egress_verified": bool(egress_status.get("verified")),
        "strategy_job_worker_enabled": bool(worker_status.get("enabled")),
        "strategy_job_worker_running": bool(worker_status.get("running")),
        "token_age_hours": round(token_age_hours, 2) if token_age_hours is not None else None,
    }

    blockers: list[str] = []
    if not has_creds:
        blockers.append("No saved Dhan credentials for this user.")
    if has_creds and not decrypt_ok:
        blockers.append("Credential vault could not be decrypted.")
    if has_creds and not token_ok:
        blockers.append("Dhan token failed basic validation.")
    if token_age_hours is not None and token_age_hours >= settings.TOKEN_MAX_AGE_HOURS:
        blockers.append("Saved Dhan token is too old; generate and save a fresh token.")

    if execution_mode == "real_orders":
        if not settings.ENABLE_LIVE_ORDERS:
            blockers.append("ENABLE_LIVE_ORDERS is not true; real orders are blocked.")
        if settings.DHAN_MODE.upper() != "REAL":
            blockers.append("DHAN_MODE must be REAL for real orders.")
        if settings.DHAN_READ_ONLY_REAL_DATA:
            blockers.append("DHAN_READ_ONLY_REAL_DATA=true blocks real order placement.")
        if not settings.EXECUTION_NODE_ROUTING_ENABLED:
            blockers.append("EXECUTION_NODE_ROUTING_ENABLED is not true.")
        if not egress_status.get("public_ip"):
            blockers.append("No Dhan static-IP execution node is assigned to this user.")
        elif not egress_status.get("verified"):
            blockers.append("The assigned Dhan static-IP execution node is not verified.")
        if not worker_status.get("enabled"):
            blockers.append("The durable strategy execution worker is disabled.")
        if settings.is_production and not worker_status.get("running"):
            blockers.append("The durable strategy execution worker is not running.")

    if uses_webhook:
        if not settings.WEBHOOK_TRADING_ENABLED:
            blockers.append("WEBHOOK_TRADING_ENABLED is not true.")
        if settings.WEBHOOK_HMAC_REQUIRED and not webhook_secret_present:
            blockers.append("WEBHOOK_HMAC_REQUIRED=true but no webhook secret saved for this user.")

    if execution_mode == "real_orders" and not blockers:
        validation = _validate_dhan_token_for_real_orders(user, creds)
        checks["dhan_token_profile_valid"] = bool(validation.get("ok"))
        checks["dhan_token_validation_method"] = "dhan_profile"
        checks["dhan_token_validation_status_code"] = validation.get("status_code")
        if not validation.get("ok"):
            blockers.append(str(validation.get("message") or "Dhan token validation failed."))

    return {
        "execution_mode": execution_mode,
        "checks": checks,
        "blockers": blockers,
        "ready": len(blockers) == 0,
        "real_orders_allowed": execution_mode == "real_orders" and len(blockers) == 0,
        "egress": egress_status,
        "subscriptions": subscriptions,
        "worker": worker_status,
    }


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------
def _append_log(handle: RunHandle, message: str, **extra: Any) -> None:
    handle.log.append({"ts": _now().isoformat(), "message": message, **extra})


def start_run(
    user: CurrentUser,
    *,
    strategy_name: str | None,
    execution_mode: str,
    config: dict[str, Any],
    uses_webhook: bool = False,
) -> dict[str, Any]:
    execution_mode = (execution_mode or "signal_only").strip().lower()
    if execution_mode not in EXECUTION_MODES:
        raise ValueError(f"Invalid execution_mode '{execution_mode}'.")

    readiness = evaluate_live_readiness(user, execution_mode, uses_webhook=uses_webhook)
    if not readiness["ready"]:
        return {"ok": False, "readiness": readiness, "run": None}

    run_id = uuid.uuid4()
    run_type = "live"
    # Establish isolated runtime folders for this user (side effect: mkdir).
    user_runtime_state_dir(user)
    user_runtime_log_dir(user)

    if database_configured() and not user.is_dev:
        with session_scope() as db:
            run = crud.create_run(
                db,
                user_id=user.id,
                run_type=run_type,
                strategy_name=strategy_name,
                execution_mode=execution_mode,
                mode_config=config,
                status="running",
            )
            crud.add_audit_log(
                db,
                user_id=user.id,
                action="LIVE_RUN_STARTED",
                metadata={"run_id": str(run.id), "execution_mode": execution_mode},
            )
            run_id = run.id

    handle = RunHandle(
        run_id=str(run_id),
        user_id=user.id_str,
        run_type=run_type,
        strategy_name=strategy_name,
        execution_mode=execution_mode,
        status="running",
        config=config,
        created_at=_now().isoformat(),
        started_at=_now().isoformat(),
    )
    _append_log(handle, f"Live run started in {execution_mode} mode.", execution_mode=execution_mode)
    if execution_mode != "real_orders":
        _append_log(handle, "Order placement is disabled for this execution mode (safe).")
    with _LOCK:
        _REGISTRY.setdefault(user.id_str, {})[handle.run_id] = handle

    return {"ok": True, "readiness": readiness, "run": handle.public()}


def stop_run(user: CurrentUser, run_id: str, *, reason: str = "user_request") -> dict[str, Any]:
    with _LOCK:
        user_runs = _REGISTRY.get(user.id_str, {})
        handle = user_runs.get(str(run_id))
        if handle is not None:
            handle.status = "stopped"
            handle.stopped_at = _now().isoformat()
            _append_log(handle, f"Run stopped ({reason}).")

    persisted = False
    if database_configured() and not user.is_dev:
        with session_scope() as db:
            run = crud.get_run(db, run_id)
            # Ownership enforcement: never touch another user's run.
            if run is not None and str(run.user_id) == user.id_str:
                crud.update_run_status(db, run, status="stopped")
                crud.add_audit_log(db, user_id=user.id, action="LIVE_RUN_STOPPED", metadata={"run_id": str(run_id), "reason": reason})
                persisted = True

    if handle is None and not persisted:
        return {"ok": False, "error": "Run not found for this user."}
    return {"ok": True, "run_id": str(run_id), "status": "stopped"}


def stop_all_user_runs(user: CurrentUser, *, reason: str = "user_request") -> int:
    count = 0
    with _LOCK:
        for handle in _REGISTRY.get(user.id_str, {}).values():
            if handle.status == "running":
                handle.status = "stopped"
                handle.stopped_at = _now().isoformat()
                _append_log(handle, f"Run stopped ({reason}).")
                count += 1
    if database_configured() and not user.is_dev:
        try:
            with session_scope() as db:
                run = crud.get_active_run_for_user(db, user.id, run_type="live")
                while run is not None:
                    crud.update_run_status(db, run, status="stopped")
                    crud.add_audit_log(db, user_id=user.id, action="LIVE_RUN_STOPPED", metadata={"reason": reason})
                    run = crud.get_active_run_for_user(db, user.id, run_type="live")
        except Exception:
            pass
    return count


def get_user_status(user: CurrentUser) -> dict[str, Any]:
    with _LOCK:
        runs = [h.public() for h in _REGISTRY.get(user.id_str, {}).values()]
    active = [r for r in runs if r["status"] == "running"]
    return {
        "user_id": user.id_str,
        "active_run_count": len(active),
        "runs": sorted(runs, key=lambda r: r["created_at"], reverse=True),
    }


def get_user_logs(user: CurrentUser, run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with _LOCK:
        user_runs = _REGISTRY.get(user.id_str, {})
        if run_id is not None:
            handle = user_runs.get(str(run_id))
            return list(handle.log[-limit:]) if handle else []
        merged: list[dict[str, Any]] = []
        for handle in user_runs.values():
            merged.extend(handle.log)
    merged.sort(key=lambda r: r.get("ts") or "")
    return merged[-limit:]
