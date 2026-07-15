"""Worker-side execution for private strategy-instance webhook jobs (Phase 3A).

The durable job worker hands private jobs (strategy_name ``instance:{id}``)
here. This module revalidates the instance at processing time (status, owner,
lots, execution mode), performs the idempotent position no-op checks against
the JSON position authority, resolves the NIFTY ATM contract server-side, and
then routes through the EXISTING execution pipeline
(strategy_fanout.dispatch_signal_job -> execution_router.route_signal), which
owns risk checks, order placement, reversal sequencing, and position updates.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any

from sqlalchemy import select

from app.config import DEFAULT_EXCHANGE_SEGMENT, settings
from app.db import models
from app.db.engine import session_scope
from app.domain.strategy_instance_state_machine import InstanceState
from app.schemas.signal import NormalizedSignal
from app.services.private_webhook_service import PRIVATE_STRATEGY_PREFIX

# Instance execution modes that reach a broker (paper or real).
_MONEY_MODES = {"paper_live_data", "real_orders"}


def is_private_job(strategy_name: str | None) -> bool:
    return str(strategy_name or "").startswith(PRIVATE_STRATEGY_PREFIX)


def _result(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, **extra}


def _no_op(reason: str) -> dict[str, Any]:
    return {
        "blocked": False,
        "success": True,
        "no_op": True,
        "status": "NO_OP",
        "reason": reason,
    }


def _failed(reason: str, message: str) -> dict[str, Any]:
    return {
        "blocked": True,
        "success": False,
        "status": "FAILED",
        "reason": message,
        "block_code": reason,
    }


@contextmanager
def _revalidate_instance(instance_id: uuid.UUID, job_user_id: Any, action: str):
    """Fresh instance state at claim time — lots/mode changes since enqueue
    apply; paused entries and all stopped/archived actions fail closed."""
    with session_scope() as db:
        instance = db.scalar(
            select(models.StrategyInstance)
            .where(models.StrategyInstance.id == instance_id)
            .with_for_update()
        )
        if instance is None:
            yield {"error": _result("rejected", "INACTIVE_INSTANCE")}
            return
        if str(instance.user_id) != str(job_user_id):
            # Structurally impossible (enqueue derives user from instance);
            # fail closed anyway.
            yield {"error": _result("rejected", "TENANT_MISMATCH")}
            return
        if instance.status == InstanceState.PAUSED.value and action == "ENTRY":
            yield {"error": _no_op("INSTANCE_PAUSED_ENTRIES_BLOCKED")}
            return
        in_verification = bool(getattr(instance, "verification_mode", False))
        if instance.status not in {InstanceState.ACTIVE.value, InstanceState.PAUSED.value} and not in_verification:
            # A strategy in controlled verification is not yet ACTIVE but must be
            # able to produce genuine paper entry/exit evidence.
            yield {"error": _result("rejected", "INACTIVE_INSTANCE")}
            return
        if in_verification and instance.execution_mode != "paper_live_data":
            # Verification is paper-only; a real-orders instance can never execute
            # a verification signal. This is the hard guarantee against live orders.
            yield {"error": _result("rejected", "LIVE_EXECUTION_SAFETY_BLOCK")}
            return
        lots = int(instance.current_lots or 0)
        if lots < 1:
            yield {"error": _result("rejected", "INVALID_LOTS")}
            return
        yield {"lots": lots, "execution_mode": instance.execution_mode}


def _resolve_entry_contract(option_side: str, lots: int) -> dict[str, Any] | None:
    """Server-side NIFTY ATM contract resolution: trusted spot -> ATM strike ->
    nearest valid weekly expiry (same-day allowed) -> exact Dhan security id.
    Returns None when no trusted contract can be resolved — never guesses."""
    from app.services.atm_ltp_service import get_atm_option_snapshot
    from app.services.security_id_resolver import suggest_option_contract

    atm = get_atm_option_snapshot(option_side=option_side, lots=lots, allow_rest_fallback=True)
    options = atm.get("options") if isinstance(atm.get("options"), dict) else {}
    option = options.get(option_side) if isinstance(options, dict) else None
    if isinstance(option, dict) and option.get("securityId"):
        strike = option.get("strike")
        try:
            strike = float(strike) if strike not in (None, "") else None
        except (TypeError, ValueError):
            strike = None
        return {
            "security_id": str(option["securityId"]),
            "trading_symbol": str(option.get("tradingSymbol") or "") or None,
            "strike": strike,
            "expiry": str(option.get("expiry") or "") or None,
        }

    reference = atm.get("niftySpot")
    if reference in (None, ""):
        # No trusted spot -> no ATM strike. Do not fall back to an arbitrary
        # strike; the job fails with CONTRACT_RESOLUTION_FAILED.
        return None
    suggestion = suggest_option_contract(
        symbol="NIFTY",
        option_side=option_side,
        exchange_segment=DEFAULT_EXCHANGE_SEGMENT,
        reference_price=float(reference),
    )
    if suggestion is None:
        return None
    return {
        "security_id": suggestion.security_id,
        "trading_symbol": suggestion.trading_symbol,
        "strike": float(suggestion.strike),
        "expiry": suggestion.expiry,
    }


def _make_enricher(lots: int):
    """Runs inside the owner's bound execution context, immediately before
    route_signal. Reads position state from the existing JSON authority."""
    from app.services.state_store import get_open_position

    def enrich(signal: NormalizedSignal) -> NormalizedSignal | dict[str, Any]:
        position = get_open_position()
        has_open = bool(position.get("has_open_position"))
        if signal.action == "EXIT":
            if not has_open:
                return _no_op("ALREADY_FLAT")
            return signal
        current_side = str(position.get("option_side") or "").upper()
        if has_open and current_side == (signal.option_side or ""):
            return _no_op(f"ALREADY_IN_{current_side}")
        contract = _resolve_entry_contract(signal.option_side or "CE", lots)
        if contract is None:
            return _failed(
                "CONTRACT_RESOLUTION_FAILED",
                "NIFTY ATM contract could not be resolved from trusted market data; no order was sent.",
            )
        return signal.model_copy(update=contract)

    return enrich


def execute_private_job(job: dict[str, Any], signal: NormalizedSignal) -> dict[str, Any]:
    from app.services import strategy_fanout
    from app.services.execution_context import bind_user_execution_context
    from app.services.state_store import get_open_position, init_runtime_files

    try:
        instance_id = uuid.UUID(str(job["strategy_name"]).removeprefix(PRIVATE_STRATEGY_PREFIX))
    except (ValueError, TypeError):
        return _result("rejected", "INACTIVE_INSTANCE")

    with _revalidate_instance(instance_id, job["user_id"], signal.action) as revalidated:
        if "error" in revalidated:
            return revalidated["error"]
        lots = revalidated["lots"]
        execution_mode = revalidated["execution_mode"]

        if (
            execution_mode == "real_orders"
            and not settings.PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED
        ):
            return _result("blocked", "PRIVATE_WEBHOOK_LIVE_EXECUTION_DISABLED")

        user = strategy_fanout.load_user_context(job["user_id"])
        if user is None:
            return _result("skipped", "user_not_found")

        # Idempotent no-op pre-check before any risk reservation or run row.
        # Position state is checked again by the enricher immediately before
        # routing while this instance row lock is still held.
        if execution_mode in _MONEY_MODES:
            with bind_user_execution_context(user):
                init_runtime_files()
                position = get_open_position()
                has_open = bool(position.get("has_open_position"))
                current_side = str(position.get("option_side") or "").upper()
            if signal.action == "EXIT" and not has_open:
                return _no_op("ALREADY_FLAT")
            if signal.action == "ENTRY" and has_open and current_side == (signal.option_side or ""):
                return _no_op(f"ALREADY_IN_{current_side}")

        return strategy_fanout.dispatch_signal_job(
            user_id=job["user_id"],
            strategy_name=job["strategy_name"],
            lots=lots,
            execution_mode=execution_mode,
            signal=signal,
            signal_enricher=_make_enricher(lots),
        )
