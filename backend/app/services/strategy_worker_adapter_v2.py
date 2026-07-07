"""Phase 2C-1 dry-run adapter for v2 strategy execution jobs."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterator

from pydantic import ValidationError

from app.config import (
    DEFAULT_EXCHANGE_SEGMENT,
    DEFAULT_ORDER_TYPE,
    DEFAULT_PRODUCT_TYPE,
    DEFAULT_NIFTY_LOT_SIZE,
)
from app.core.enums import StrategyExecutionMode
from app.db import models
from app.schemas.signal import NormalizedSignal
from app.services.strategy_execution_queue_v2 import (
    V2_COMPLETED_DRY_RUN,
    V2_FAILED_DRY_RUN,
    claim_v2_job_for_test_or_future_worker,
    mark_v2_job_completed_dry_run,
    mark_v2_job_failed_dry_run,
)


V2_COMPLETED_PAPER = "v2_completed_paper"
V2_FAILED_PAPER = "v2_failed_paper"

MISSING_NORMALIZED_SIGNAL_ID = "MISSING_NORMALIZED_SIGNAL_ID"
MISSING_NORMALIZED_SIGNAL = "MISSING_NORMALIZED_SIGNAL"
MISSING_STRATEGY_SIGNAL = "MISSING_STRATEGY_SIGNAL"
MISSING_INSTANCE = "MISSING_INSTANCE"
UNSUPPORTED_EXECUTION_MODE = "UNSUPPORTED_EXECUTION_MODE"
UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
UNSUPPORTED_OPTION_SIDE = "UNSUPPORTED_OPTION_SIDE"
QTY_UNRESOLVED = "QTY_UNRESOLVED"
ROUTE_SIGNAL_INCOMPATIBLE = "ROUTE_SIGNAL_INCOMPATIBLE"
V2_LIVE_BLOCKED = "V2_LIVE_BLOCKED"
V2_PAPER_MODE_UNAVAILABLE = "V2_PAPER_MODE_UNAVAILABLE"
V2_ROUTE_SIGNAL_FAILED = "V2_ROUTE_SIGNAL_FAILED"
V2_INTERNAL_TRUST_MARKER = "v2_internal"

SUPPORTED_EXECUTION_MODES = {
    StrategyExecutionMode.SIGNAL_ONLY.value,
    StrategyExecutionMode.PAPER_LIVE_DATA.value,
    StrategyExecutionMode.REAL_ORDERS.value,
}


@dataclass
class V2DryRunResult:
    ok: bool
    job_id: str
    status: str
    normalized_signal_preview: dict[str, Any] | None = None
    error_code: str | None = None
    user_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "job_id": self.job_id,
            "status": self.status,
            "normalized_signal_preview": self.normalized_signal_preview,
            "error_code": self.error_code,
            "user_message": self.user_message,
        }


class V2DryRunBuildError(ValueError):
    def __init__(self, error_code: str, user_message: str) -> None:
        super().__init__(user_message)
        self.error_code = error_code
        self.user_message = user_message


@dataclass
class V2PaperExecutionResult:
    ok: bool
    job_id: str
    status: str
    route_result_summary: dict[str, Any] | None = None
    error_code: str | None = None
    user_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "job_id": self.job_id,
            "status": self.status,
            "route_result_summary": self.route_result_summary,
            "error_code": self.error_code,
            "user_message": self.user_message,
        }


def build_normalized_signal_from_v2_job(
    db: Any,
    job: models.StrategyExecutionJob,
) -> NormalizedSignal:
    """Build the existing execution-router signal shape without executing it."""
    strategy_signal = db.get(models.StrategySignal, job.strategy_signal_id)
    if strategy_signal is None:
        raise V2DryRunBuildError(
            MISSING_STRATEGY_SIGNAL,
            "The v2 job does not reference an existing strategy signal.",
        )
    if job.normalized_signal_id is None:
        raise V2DryRunBuildError(
            MISSING_NORMALIZED_SIGNAL_ID,
            "The v2 job does not reference a normalized option signal.",
        )
    normalized = db.get(models.NormalizedOptionSignal, job.normalized_signal_id)
    if normalized is None:
        raise V2DryRunBuildError(
            MISSING_NORMALIZED_SIGNAL,
            "The normalized option signal row was not found.",
        )
    if job.instance_id is None:
        raise V2DryRunBuildError(
            MISSING_INSTANCE,
            "The v2 job does not reference a strategy instance.",
        )
    instance = db.get(models.UserStrategyInstance, job.instance_id)
    if instance is None:
        raise V2DryRunBuildError(
            MISSING_INSTANCE,
            "The user strategy instance row was not found.",
        )
    if str(job.execution_mode or "") not in SUPPORTED_EXECUTION_MODES:
        raise V2DryRunBuildError(
            UNSUPPORTED_EXECUTION_MODE,
            "The v2 job execution mode is not supported by the dry-run adapter.",
        )

    action, side = _execution_action_and_side(normalized.action)
    option_side = _option_side(normalized.option_side)
    qty = _resolved_qty(normalized)
    raw_payload = _raw_payload(job, strategy_signal, instance, normalized)
    try:
        return NormalizedSignal(
            payload_format="NOVA",
            secret="",
            signal_id=job.signal_id,
            strategy_code=job.strategy_name or strategy_signal.strategy_name,
            action=action,
            side=side,
            symbol=normalized.symbol,
            instrument_type=normalized.instrument_type,
            exchange_segment=DEFAULT_EXCHANGE_SEGMENT,
            security_id=None,
            trading_symbol=None,
            option_side=option_side,
            strike=normalized.resolved_strike,
            expiry=normalized.resolved_expiry.isoformat() if normalized.resolved_expiry else None,
            qty=qty,
            order_type=normalized.order_type or DEFAULT_ORDER_TYPE,
            product_type=normalized.product_type or DEFAULT_PRODUCT_TYPE,
            source=_source_from_normalized_signal(normalized),
            raw_payload=raw_payload,
        )
    except ValidationError as exc:
        raise V2DryRunBuildError(
            ROUTE_SIGNAL_INCOMPATIBLE,
            f"The v2 job cannot build a route-compatible NormalizedSignal: {exc.errors()}",
        ) from exc


def process_v2_job_dry_run(
    db: Any,
    job: models.StrategyExecutionJob,
    worker_id: str,
) -> V2DryRunResult:
    """Validate one claimed v2 job and mark it dry-run completed or failed."""
    try:
        signal = build_normalized_signal_from_v2_job(db, job)
    except V2DryRunBuildError as exc:
        mark_v2_job_failed_dry_run(
            db,
            job.id,
            exc.user_message,
            category=exc.error_code,
        )
        return V2DryRunResult(
            ok=False,
            job_id=str(job.id),
            status=V2_FAILED_DRY_RUN,
            error_code=exc.error_code,
            user_message=exc.user_message,
        )

    preview = _signal_preview(signal)
    mark_v2_job_completed_dry_run(
        db,
        job.id,
        {
            "worker_id": worker_id,
            "dry_run_adapter": True,
            "normalized_signal_preview": preview,
        },
    )
    return V2DryRunResult(
        ok=True,
        job_id=str(job.id),
        status=V2_COMPLETED_DRY_RUN,
        normalized_signal_preview=preview,
    )


def process_next_v2_job_dry_run(
    db: Any,
    worker_id: str,
) -> V2DryRunResult | None:
    """Claim and dry-run the next v2 job. This is not registered in startup."""
    job = claim_v2_job_for_test_or_future_worker(db, worker_id)
    if job is None:
        return None
    return process_v2_job_dry_run(db, job, worker_id)


def process_v2_job_paper_once(
    db: Any,
    job: models.StrategyExecutionJob,
    worker_id: str,
) -> V2PaperExecutionResult:
    """Manually execute one v2 paper job through route_signal.

    This is intentionally not registered in startup or the legacy worker. Paper
    mode is forced with state_store.set_engine_mode("paper") because the existing
    execution router selects the paper routing path only through get_engine_mode().
    """
    mode = str(job.execution_mode or "").strip().lower()
    if mode != StrategyExecutionMode.PAPER_LIVE_DATA.value:
        error_code = V2_LIVE_BLOCKED if mode in {"real_orders", "live"} else UNSUPPORTED_EXECUTION_MODE
        message = "V2 paper bridge only supports paper_live_data jobs."
        _mark_v2_job_failed_paper(db, job.id, message, category=error_code)
        return V2PaperExecutionResult(
            ok=False,
            job_id=str(job.id),
            status=V2_FAILED_PAPER,
            error_code=error_code,
            user_message=message,
        )

    try:
        signal = build_normalized_signal_from_v2_job(db, job)
    except V2DryRunBuildError as exc:
        _mark_v2_job_failed_paper(db, job.id, exc.user_message, category=exc.error_code)
        return V2PaperExecutionResult(
            ok=False,
            job_id=str(job.id),
            status=V2_FAILED_PAPER,
            error_code=exc.error_code,
            user_message=exc.user_message,
        )

    try:
        with _force_paper_mode() as paper_guard:
            route_result = _route_signal(signal)
            paper_guard["route_completed"] = True
    except V2DryRunBuildError as exc:
        _mark_v2_job_failed_paper(db, job.id, exc.user_message, category=exc.error_code)
        return V2PaperExecutionResult(
            ok=False,
            job_id=str(job.id),
            status=V2_FAILED_PAPER,
            error_code=exc.error_code,
            user_message=exc.user_message,
        )
    except Exception as exc:
        message = f"V2 paper route_signal failed: {exc}"
        _mark_v2_job_failed_paper(db, job.id, message, category=V2_ROUTE_SIGNAL_FAILED)
        return V2PaperExecutionResult(
            ok=False,
            job_id=str(job.id),
            status=V2_FAILED_PAPER,
            error_code=V2_ROUTE_SIGNAL_FAILED,
            user_message=message,
        )

    summary = {
        "route_result": _sanitize_route_result(route_result),
        "normalized_signal_preview": _signal_preview(signal),
        "paper_mode": _jsonable(paper_guard),
        "worker_id": worker_id,
        "manual_test_only": True,
    }
    route_ok = bool(route_result.get("success", True)) and not bool(route_result.get("blocked"))
    if not route_ok:
        reason = str(
            route_result.get("reason")
            or route_result.get("error")
            or route_result.get("status")
            or "route_signal returned an unsuccessful paper result."
        )
        _mark_v2_job_failed_paper(
            db,
            job.id,
            reason,
            category=V2_ROUTE_SIGNAL_FAILED,
            route_result_summary=summary["route_result"],
        )
        return V2PaperExecutionResult(
            ok=False,
            job_id=str(job.id),
            status=V2_FAILED_PAPER,
            route_result_summary=summary["route_result"],
            error_code=V2_ROUTE_SIGNAL_FAILED,
            user_message=reason,
        )

    _mark_v2_job_completed_paper(db, job.id, summary)
    return V2PaperExecutionResult(
        ok=True,
        job_id=str(job.id),
        status=V2_COMPLETED_PAPER,
        route_result_summary=summary["route_result"],
    )


def process_next_v2_job_paper_once(
    db: Any,
    worker_id: str,
) -> V2PaperExecutionResult | None:
    """Claim one v2 job and run the manual paper-only bridge."""
    job = claim_v2_job_for_test_or_future_worker(db, worker_id)
    if job is None:
        return None
    return process_v2_job_paper_once(db, job, worker_id)


def _execution_action_and_side(action: str) -> tuple[str, str]:
    normalized = str(action or "").upper()
    if normalized == "ENTRY":
        return "ENTRY", "BUY"
    if normalized == "EXIT":
        return "EXIT", "SELL"
    raise V2DryRunBuildError(
        UNSUPPORTED_ACTION,
        f"The v2 dry-run adapter cannot map action '{action}' to NormalizedSignal.",
    )


def _option_side(value: str | None) -> str | None:
    normalized = str(value or "").upper()
    if normalized in {"CE", "PE"}:
        return normalized
    if normalized in {"", "NONE"}:
        return None
    raise V2DryRunBuildError(
        UNSUPPORTED_OPTION_SIDE,
        f"The v2 dry-run adapter cannot map option_side '{value}'.",
    )


def _resolved_qty(normalized: models.NormalizedOptionSignal) -> int:
    if normalized.quantity and int(normalized.quantity) > 0:
        return int(normalized.quantity)
    if str(normalized.qty_mode or "").upper() == "LOTS":
        lots = max(int(normalized.lots or 0), 1)
        lot_size = _current_lot_size()
        qty = lots * lot_size
        if qty > 0:
            return qty
    raise V2DryRunBuildError(
        QTY_UNRESOLVED,
        "The v2 dry-run adapter could not resolve a positive order quantity.",
    )


def _current_lot_size() -> int:
    try:
        from app.routers.setup import current_nifty_lot_size

        lot_size = int(current_nifty_lot_size())
    except Exception:
        lot_size = DEFAULT_NIFTY_LOT_SIZE
    return lot_size if lot_size > 0 else DEFAULT_NIFTY_LOT_SIZE


def _raw_payload(
    job: models.StrategyExecutionJob,
    strategy_signal: models.StrategySignal,
    instance: models.UserStrategyInstance,
    normalized: models.NormalizedOptionSignal,
) -> dict[str, Any]:
    job_payload = job.signal_payload if isinstance(job.signal_payload, dict) else {}
    return {
        V2_INTERNAL_TRUST_MARKER: True,
        "dry_run_adapter": True,
        "adapter_phase": "phase2c_1",
        "v2_job_id": str(job.id),
        "job_signal_id": job.signal_id,
        "source_signal_id": strategy_signal.signal_id,
        "strategy_signal_id": str(strategy_signal.id),
        "strategy_version_id": str(job.strategy_version_id or strategy_signal.strategy_version_id or ""),
        "instance_id": str(instance.id),
        "normalized_signal_id": str(normalized.id),
        "user_id": str(job.user_id),
        "execution_mode": job.execution_mode,
        "payload_version": strategy_signal.payload_version,
        "source_type": strategy_signal.source_type,
        "needs_resolution": bool(job_payload.get("needs_resolution")),
        "normalized_action": normalized.action,
        "normalized_intent": normalized.intent,
        "normalized_option_side": normalized.option_side,
        "strike_mode": normalized.strike_mode,
        "expiry_mode": normalized.expiry_mode,
        "queue_contract": job_payload.get("queue_contract"),
    }


def _source_from_normalized_signal(normalized: models.NormalizedOptionSignal) -> str:
    return "strategy_worker_adapter_v2"


def _signal_preview(signal: NormalizedSignal) -> dict[str, Any]:
    return _sanitize_signal_preview(signal.model_dump(mode="json"))


_SIGNAL_PREVIEW_REDACTED_KEYS = {
    "access_token",
    "headers",
    "proxy_url",
    "raw_response",
    "request",
    "response_json",
    "response_text",
    "secret",
}

_SAFE_RAW_PAYLOAD_PREVIEW_KEYS = {
    V2_INTERNAL_TRUST_MARKER,
    "adapter_phase",
    "dry_run_adapter",
    "execution_mode",
    "expiry_mode",
    "instance_id",
    "job_signal_id",
    "needs_resolution",
    "normalized_action",
    "normalized_intent",
    "normalized_option_side",
    "normalized_signal_id",
    "payload_version",
    "queue_contract",
    "source_signal_id",
    "source_type",
    "strategy_signal_id",
    "strategy_version_id",
    "strike_mode",
    "user_id",
    "v2_job_id",
}


def _sanitize_signal_preview(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _SIGNAL_PREVIEW_REDACTED_KEYS:
                continue
            if lowered == "raw_payload":
                sanitized["raw_payload"] = _safe_raw_payload_preview(item)
                continue
            sanitized[str(key)] = _sanitize_signal_preview(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_signal_preview(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_signal_preview(item) for item in value]
    return _jsonable(value)


def _safe_raw_payload_preview(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: _sanitize_signal_preview(value[key])
        for key in _SAFE_RAW_PAYLOAD_PREVIEW_KEYS
        if key in value
    }


def _route_signal(signal: NormalizedSignal) -> dict[str, Any]:
    from app.services.execution_router import route_signal

    return route_signal(signal)


@contextmanager
def _force_paper_mode() -> Iterator[dict[str, Any]]:
    from app.services import state_store

    previous_explicit_mode = state_store.get_engine_mode(legacy_fallback=False)
    guard = {
        "forced": True,
        "previous_explicit_mode": previous_explicit_mode,
        "active_mode": None,
        "route_completed": False,
        "restored": False,
        "restore_error": None,
    }
    try:
        state_store.set_engine_mode("paper")
        active_mode = state_store.get_engine_mode(legacy_fallback=False)
        guard["active_mode"] = active_mode
        if active_mode != "paper":
            raise V2DryRunBuildError(
                V2_PAPER_MODE_UNAVAILABLE,
                "Could not force explicit paper mode for v2 paper execution.",
            )
    except ValueError as exc:
        raise V2DryRunBuildError(
            V2_PAPER_MODE_UNAVAILABLE,
            f"Could not force paper mode for v2 paper execution: {exc}",
        ) from exc
    try:
        yield guard
    finally:
        if previous_explicit_mode != "paper":
            try:
                state_store.set_engine_mode(previous_explicit_mode)
                guard["restored"] = True
            except Exception as exc:
                # If paper route created a position, restoring may be refused.
                # Leaving explicit paper mode is safer than risking live routing.
                guard["restore_error"] = str(exc)


def _mark_v2_job_completed_paper(
    db: Any,
    job_id: uuid.UUID | str,
    details: dict[str, Any],
) -> models.StrategyExecutionJob | None:
    job = db.get(models.StrategyExecutionJob, _coerce_uuid(job_id))
    if job is None:
        return None
    job.status = V2_COMPLETED_PAPER
    job.completed_at = models.utcnow()
    job.result_summary = {
        "status": V2_COMPLETED_PAPER,
        "details": _jsonable(details),
    }
    job.updated_at = models.utcnow()
    db.flush()
    return job


def _mark_v2_job_failed_paper(
    db: Any,
    job_id: uuid.UUID | str,
    reason: str,
    *,
    category: str | None = None,
    route_result_summary: dict[str, Any] | None = None,
) -> models.StrategyExecutionJob | None:
    job = db.get(models.StrategyExecutionJob, _coerce_uuid(job_id))
    if job is None:
        return None
    job.status = V2_FAILED_PAPER
    job.completed_at = models.utcnow()
    job.last_error = reason
    job.dead_letter_reason = category
    job.result_summary = {
        "status": V2_FAILED_PAPER,
        "reason": reason,
        "category": category,
        "route_result_summary": _sanitize_route_result(route_result_summary or {}),
    }
    job.updated_at = models.utcnow()
    db.flush()
    return job


def _sanitize_route_result(value: Any) -> Any:
    redacted_keys = {
        "access_token",
        "secret",
        "proxy_url",
        "raw_response",
        "response_text",
        "response_json",
        "request",
        "headers",
    }
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in redacted_keys:
                continue
            sanitized[str(key)] = _sanitize_route_result(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_route_result(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_route_result(item) for item in value]
    return _jsonable(value)


def _coerce_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value
