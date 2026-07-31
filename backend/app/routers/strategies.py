"""Strategy subscriptions, shared TradingView webhook, and egress assignment."""
from __future__ import annotations

import hmac
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.auth.dependencies import get_current_user, require_admin
from app.config import settings
from app.db import models
from app.db.engine import database_configured, session_scope
from app.services import (
    entitlements,
    live_engine,
    runtime_reliability,
    setup_configuration,
    strategy_catalog_service,
    strategy_fanout,
    strategy_instance_service,
    strategy_risk,
    webhook_replay_store,
)
from app.services.execution_context import bind_user_execution_context
from app.services.signal_validator import validate_signal
from app.services.user_context import CurrentUser
from app.workers.strategy_job_worker import wake_strategy_job_worker

router = APIRouter(tags=["Strategies"])
_WEBHOOK_REQUESTS: dict[str, list[float]] = {}
_WEBHOOK_RATE_LOCK = threading.RLock()
_PRODUCTION_WEBHOOK_WINDOW_SECONDS = 300


def _webhook_rate_limited(client_host: str) -> bool:
    limit = max(int(settings.WEBHOOK_RATE_LIMIT_PER_MINUTE), 1)
    now = time.monotonic()
    cutoff = now - 60
    with _WEBHOOK_RATE_LOCK:
        recent = [
            timestamp
            for timestamp in _WEBHOOK_REQUESTS.get(client_host, [])
            if timestamp >= cutoff
        ]
        if len(recent) >= limit:
            _WEBHOOK_REQUESTS[client_host] = recent
            return True
        recent.append(now)
        _WEBHOOK_REQUESTS[client_host] = recent
        return False


def _parse_webhook_timestamp(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.timestamp()


def _production_freshness_error(body: dict, *, now: float | None = None) -> str | None:
    if body.get("timestamp") in (None, ""):
        return "Webhook timestamp is required."
    timestamp = _parse_webhook_timestamp(body.get("timestamp"))
    if timestamp is None:
        return "Invalid webhook timestamp."
    current_time = time.time() if now is None else now
    if abs(current_time - timestamp) > _PRODUCTION_WEBHOOK_WINDOW_SECONDS:
        return "Webhook timestamp is outside the allowed window."
    if not str(body.get("nonce") or "").strip():
        return "Webhook nonce is required."
    return None


def _claim_production_webhook_nonce(
    *,
    path_strategy: str,
    nonce: str,
    raw_body: bytes,
    timestamp: Any,
) -> JSONResponse | None:
    if not database_configured():
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "Webhook replay store unavailable."},
        )
    try:
        nonce_claim = webhook_replay_store.claim_webhook_event(
            provider=f"strategy:{path_strategy}:nonce",
            event_id=nonce,
            raw_body=raw_body,
            signature_ok=True,
            metadata={
                "strategy_name": path_strategy,
                "timestamp": str(timestamp),
            },
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "Webhook replay store unavailable."},
        )
    if nonce_claim.get("status") in {"duplicate", "tampered"}:
        return JSONResponse(
            status_code=409,
            content={"ok": False, "error": "Duplicate webhook signal."},
        )
    if nonce_claim.get("status") != "fresh":
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "Webhook replay store unavailable."},
        )
    return None


class SubscribePayload(BaseModel):
    strategy_name: str = Field(min_length=1, max_length=120)
    lots: int = Field(default=1, ge=1, le=1000)
    execution_mode: str = Field(default="signal_only")


class RiskControlPatch(BaseModel):
    kill_switch: bool | None = None
    max_lots_per_order: int | None = Field(default=None, ge=0)
    max_notional_per_trade: str | None = None
    max_notional_per_trade_paise: int | None = Field(default=None, ge=0)
    max_orders_per_day: int | None = Field(default=None, ge=0)
    max_loss_per_day: str | None = None
    max_loss_per_day_paise: int | None = Field(default=None, ge=0)

    def normalized_changes(self) -> dict[str, Any]:
        data = self.model_dump(exclude_unset=True)
        if data.get("max_notional_per_trade_paise") is None and data.get("max_notional_per_trade") is not None:
            data["max_notional_per_trade_paise"] = strategy_risk.money_to_paise(data["max_notional_per_trade"])
        if data.get("max_loss_per_day_paise") is None and data.get("max_loss_per_day") is not None:
            data["max_loss_per_day_paise"] = strategy_risk.money_to_paise(data["max_loss_per_day"])
        data.pop("max_notional_per_trade", None)
        data.pop("max_loss_per_day", None)
        return data


class CatalogSelectionPayload(BaseModel):
    strategy_key: str = Field(min_length=1, max_length=120)


class ConfigurationPayload(BaseModel):
    """One save for both halves of a configuration.

    Owner identity is never accepted from the browser - it comes from the auth
    layer. ``expected_revision`` is the revision the client last read; omit it
    only for a first save.
    """

    strategy_key: str = Field(min_length=1, max_length=120)
    mode: str = Field(min_length=4, max_length=5)
    setup: dict[str, Any]
    risk: dict[str, Any]
    expected_revision: int | None = Field(default=None, ge=0)


class CatalogSetupPayload(BaseModel):
    strategy_key: str = Field(min_length=1, max_length=120)
    mode: str = Field(min_length=4, max_length=5)
    values: dict[str, Any]


def _catalog_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, strategy_instance_service.InstanceError):
        content: dict[str, Any] = {"ok": False, "error": str(exc)}
        if exc.code:
            content["reason"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content)
    if isinstance(exc, ValueError):
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    raise exc


def _owner_runtime(user: CurrentUser) -> dict[str, Any]:
    with bind_user_execution_context(user):
        return runtime_reliability.runtime_status(user)


@router.get("/api/strategies/catalog")
def unified_strategy_catalog(user: CurrentUser = Depends(get_current_user)) -> dict:
    runtime = _owner_runtime(user)
    return {
        "ok": True,
        **strategy_catalog_service.get_catalog(
            user.id,
            runtime_state=runtime["engine"]["state"],
        ),
    }


@router.get("/api/strategies/catalog/{strategy_key}/setup-schema")
def strategy_setup_schema(
    strategy_key: str,
    user: CurrentUser = Depends(get_current_user),
):
    catalog = strategy_catalog_service.get_catalog(user.id)
    strategy = next(
        (item for item in catalog["strategies"] if item["strategy_key"] == strategy_key),
        None,
    )
    if strategy is None:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Strategy not found."})
    return {"ok": True, "strategy_key": strategy_key, "setup_schema": strategy["setup_schema"]}


@router.put("/api/strategies/catalog/selection")
def select_catalog_strategy(
    payload: CatalogSelectionPayload,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        current = strategy_catalog_service.get_catalog(user.id)
        changing = current["selected_strategy_key"] != payload.strategy_key
        if changing:
            runtime = _owner_runtime(user)
            if runtime["engine"]["state"] != "STOPPED" or runtime["position"]["has_open_position"]:
                raise strategy_instance_service.InstanceError(
                    "Stop the engine and confirm the tracked position is flat before changing strategy.",
                    status_code=409,
                    code="RECONFIGURE_REQUIRED",
                )
        return {
            "ok": True,
            **strategy_catalog_service.select_strategy(user.id, payload.strategy_key),
        }
    except Exception as exc:  # noqa: BLE001
        return _catalog_error(exc)


@router.put("/api/strategies/catalog/setup")
def save_catalog_setup(
    payload: CatalogSetupPayload,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        runtime = _owner_runtime(user)
        if runtime["engine"]["state"] != "STOPPED":
            raise strategy_instance_service.InstanceError(
                "Stop the engine before changing configuration.",
                status_code=409,
                code="RECONFIGURE_REQUIRED",
            )
        if runtime["position"]["has_open_position"]:
            raise strategy_instance_service.InstanceError(
                "Configuration cannot change while a tracked position is open.",
                status_code=409,
                code="OPEN_POSITION",
            )
        return {
            "ok": True,
            **strategy_catalog_service.save_setup(
                user.id,
                strategy_key=payload.strategy_key,
                mode=payload.mode,
                values=payload.values,
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _catalog_error(exc)


@router.put("/api/setup/configuration")
def save_configuration(
    payload: ConfigurationPayload,
    user: CurrentUser = Depends(get_current_user),
):
    """Commit strategy setup and risk settings as one revision."""
    from app.routers.setup import normalize_risk_values

    try:
        runtime = _owner_runtime(user)
        if runtime["engine"]["state"] != "STOPPED":
            raise strategy_instance_service.InstanceError(
                "Stop the engine before changing configuration.",
                status_code=409,
                code="RECONFIGURE_REQUIRED",
            )
        if runtime["position"]["has_open_position"]:
            raise strategy_instance_service.InstanceError(
                "Configuration cannot change while a tracked position is open.",
                status_code=409,
                code="OPEN_POSITION",
            )
        return setup_configuration.save_configuration(
            user.id,
            strategy_key=payload.strategy_key,
            mode=payload.mode,
            setup_values=payload.setup,
            risk_values=payload.risk,
            expected_revision=payload.expected_revision,
            normalize_risk=normalize_risk_values,
        )
    except ValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": "Risk settings are out of range.", "code": "INVALID_RISK",
                     "detail": [{"field": list(e.get("loc") or ["risk"])[-1], "message": e.get("msg")} for e in exc.errors()]},
        )
    except setup_configuration.ConfigurationConflict as exc:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": exc.message,
                "code": exc.code,
                "expected_revision": exc.expected,
                "current_revision": exc.current,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _catalog_error(exc)


@router.get("/api/setup/configuration")
def read_configuration(
    mode: str = "paper",
    user: CurrentUser = Depends(get_current_user),
):
    """The revision a save must echo back, and whether both halves agree."""
    return {"ok": True, **setup_configuration.current_revision(user.id, mode)}


@router.get("/api/trading/configurations")
def list_trading_configurations(
    mode: str | None = Query(default=None, pattern="^(paper|live)$"),
    strategy_instance_id: uuid.UUID | None = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
):
    return {
        "ok": True,
        "items": setup_configuration.list_configurations(
            user.id,
            mode=mode,
            strategy_instance_id=strategy_instance_id,
        ),
        "selected": setup_configuration.selected_configuration(user.id, mode),
    }


@router.get("/api/trading/configurations/{configuration_id}")
def read_trading_configuration(
    configuration_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
):
    configuration = setup_configuration.get_configuration(user.id, configuration_id)
    if configuration is None:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Configuration not found."})
    return {"ok": True, "configuration": configuration}


def _manual_defaults(user_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    defaults: dict[str, dict[str, Any]] = {}
    for mode in ("paper", "live"):
        revision = setup_configuration.selected_configuration(user_id, mode)
        values = dict(revision.get("configuration") or {}) if revision else {}
        defaults[mode] = {
            "available": revision is not None,
            "lots": values.get("lots"),
            "stop_loss_percent": values.get("stop_loss_percent"),
            "take_profit_percent": values.get("take_profit_percent"),
            "order_type": "MARKET" if revision is not None else None,
            "configuration_revision_id": revision.get("id") if revision else None,
            "configuration_revision": revision.get("revision") if revision else None,
        }
    return defaults


@router.get("/api/trading/bootstrap")
def trading_bootstrap(user: CurrentUser = Depends(get_current_user)):
    """One owner-scoped read model for setup restoration and terminal startup."""
    from app.services import automations_overview, risk_overview, user_preferences

    if not database_configured():
        raise HTTPException(status_code=503, detail="Trading database is not configured.")

    runtime = _owner_runtime(user)
    selected_configuration = setup_configuration.selected_configuration(user.id)
    mode = (
        selected_configuration["mode"]
        if selected_configuration is not None
        else None
    )

    catalog = strategy_catalog_service.get_catalog(
        user.id,
        runtime_state=runtime["engine"]["state"],
    )
    selection = strategy_instance_service.trading_selection_state(user.id)
    current_run = None
    pending_operation = None
    if database_configured():
        with session_scope() as db:
            run = db.scalar(
                select(models.UserRun)
                .where(models.UserRun.user_id == user.id)
                .order_by(models.UserRun.created_at.desc())
            )
            if run is not None:
                current_run = {
                    "id": str(run.id),
                    "status": run.status,
                    "mode": run.run_type,
                    "execution_mode": run.execution_mode,
                    "strategy_name": run.strategy_name,
                    "strategy_version_id": (
                        str(run.strategy_version_id)
                        if run.strategy_version_id
                        else None
                    ),
                    "configuration_revision_id": (
                        str(run.configuration_revision_id)
                        if run.configuration_revision_id
                        else None
                    ),
                    "configuration_revision": run.configuration_revision,
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "stopped_at": run.stopped_at.isoformat() if run.stopped_at else None,
                }
            operation = db.scalar(
                select(models.EngineStartOperation)
                .where(
                    models.EngineStartOperation.user_id == user.id,
                    models.EngineStartOperation.status == "pending",
                )
                .order_by(models.EngineStartOperation.created_at.desc())
            )
            if operation is not None:
                pending_operation = {
                    "id": str(operation.id),
                    "type": "engine_start",
                    "status": operation.status,
                    "mode": operation.mode,
                    "configuration_revision_id": str(
                        operation.configuration_revision_id
                    ),
                    "configuration_revision": operation.configuration_revision,
                    "created_at": operation.created_at.isoformat(),
                }

    if (
        mode is None
        and current_run is not None
        and current_run["status"] == "running"
        and current_run["mode"] in {"paper", "live"}
    ):
        mode = current_run["mode"]
    if (
        mode is None
        and (
            runtime["engine"]["state"] != "STOPPED"
            or runtime["position"].get("has_open_position")
        )
    ):
        runtime_mode = runtime["engine"].get("mode")
        mode = runtime_mode if runtime_mode in {"paper", "live"} else None

    preferences = user_preferences.get_preferences(user.id)
    setup_state = catalog.get("setup_progress")
    position_version = runtime["position"].get(
        "position_version",
        runtime["position"].get("version"),
    )
    return {
        # Every field runtime_status() produces (pnl, config, account, safety,
        # exit, owner_user_id) belongs in this response too: RuntimeStatus is
        # the frontend's shared contract for both /api/runtime/status and this
        # endpoint, and every consumer (Header, EngineConfigCard, ...) trusts
        # it's always complete.
        **runtime,
        # strategy_catalog/eligible_strategies/selected_strategy: the setup
        # conversation's strategy-picker step reads these directly off the
        # bootstrap payload; without them the strategy list silently renders
        # empty. Mirrors _hydrated_runtime_status's already-correct pattern.
        **selection,
        "strategy_catalog": catalog,
        "ok": True,
        "mode": mode,
        "setup": {
            "state": setup_state,
            "saved_complete": selected_configuration is not None,
            "mode_selected": mode in {"paper", "live"},
        },
        "setup_state": setup_state,
        "selected_strategy_key": catalog.get("selected_strategy_key"),
        "compatible_configurations": setup_configuration.list_configurations(user.id, mode=mode),
        "selected_configuration": selected_configuration,
        "current_run": current_run,
        "live_readiness": live_engine.evaluate_live_readiness(
            user,
            "real_orders",
            uses_webhook=True,
        ),
        "pending_operation": pending_operation,
        "engine": runtime["engine"],
        "position": runtime["position"],
        "risk_usage": risk_overview.build_risk_overview(user.id),
        "manual_defaults": _manual_defaults(user.id),
        "automation_settings": automations_overview.build_automations_overview(
            user.id
        ),
        "notification_preferences": preferences.get("notification_preferences", {}),
        "chart_preferences": {
            "default_timeframe": preferences.get("default_chart_timeframe", "5m"),
        },
        "terminal_state": {
            "engine_state": runtime["engine"]["state"],
            "position_version": position_version,
            "reconciliation_status": (
                "pending" if pending_operation is not None else "resolved"
            ),
        },
        "terminal_capabilities": {
            "max_open_positions": 1,
            "paper_add_quantity": True,
            "live_add_quantity": False,
            "paper_partial_exit": True,
            "live_partial_exit": False,
            "paper_modify_protection": True,
            "live_modify_super_order_protection": True,
            "manual_market_orders": True,
            "manual_limit_orders": False,
        },
    }


@router.get("/api/strategies/subscriptions")
def my_subscriptions(user: CurrentUser = Depends(get_current_user)) -> dict:
    return {"subscriptions": strategy_fanout.list_user_subscriptions(user.id)}


@router.post("/api/strategies/subscribe")
def subscribe(
    payload: SubscribePayload,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    if payload.execution_mode not in live_engine.EXECUTION_MODES:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid execution_mode"},
        )
    if payload.execution_mode == "real_orders":
        entitlement_response = _require_real_order_strategy_entitlements(user)
        if entitlement_response is not None:
            return entitlement_response
    subscription = strategy_fanout.subscribe_user(
        user.id,
        payload.strategy_name,
        lots=payload.lots,
        execution_mode=payload.execution_mode,
    )
    return {"ok": True, "subscription": subscription}


@router.delete("/api/strategies/subscribe")
def unsubscribe(
    strategy_name: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    return {
        "ok": strategy_fanout.unsubscribe_user(user.id, strategy_name),
    }


@router.get("/api/strategies/egress/status")
def egress_status(user: CurrentUser = Depends(get_current_user)) -> dict:
    return strategy_fanout.user_egress_status(user.id)


@router.post("/api/strategies/egress/verify")
def verify_current_user_egress(user: CurrentUser = Depends(get_current_user)) -> dict:
    entitlement_response = _require_static_ip_entitlement(user)
    if entitlement_response is not None:
        return entitlement_response
    result = strategy_fanout.verify_user_egress(user.id)
    return {"ok": bool(result.get("ok")), "egress": result}


@router.get("/api/strategies/risk/{strategy_name}")
def get_strategy_risk(
    strategy_name: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    return {"ok": True, **strategy_risk.get_effective_controls(user.id, strategy_name)}


@router.patch("/api/strategies/risk/user")
def patch_user_risk(
    payload: RiskControlPatch,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    control = strategy_risk.set_user_risk_control(user.id, **payload.normalized_changes())
    return {"ok": True, "user": control}


@router.patch("/api/strategies/risk/{strategy_name}")
def patch_strategy_risk(
    strategy_name: str,
    payload: RiskControlPatch,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    control = strategy_risk.set_user_strategy_risk_control(
        user.id,
        strategy_name,
        **payload.normalized_changes(),
    )
    return {"ok": True, "strategy": control}


@router.post("/api/webhook/strategy/{strategy_name}")
async def strategy_webhook(
    strategy_name: str,
    request: Request,
) -> JSONResponse:
    secret = (settings.STRATEGY_WEBHOOK_SECRET or "").strip()
    if not secret:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "Strategy webhook secret not configured."},
        )
    client_host = request.client.host if request.client else "unknown"
    if _webhook_rate_limited(client_host):
        return JSONResponse(
            status_code=429,
            content={"ok": False, "error": "Webhook rate limit exceeded."},
        )
    try:
        raw_body = await request.body()
        body = json.loads(raw_body.decode("utf-8", errors="replace"))
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Invalid JSON."},
        )
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Payload must be an object."},
        )
    if not hmac.compare_digest(str(body.get("secret") or ""), secret):
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "Invalid secret."},
        )

    path_strategy = strategy_fanout.canonical_strategy_name(strategy_name)
    if settings.is_production:
        freshness_error = _production_freshness_error(body)
        if freshness_error:
            return JSONResponse(
                status_code=401,
                content={"ok": False, "error": freshness_error},
            )
        nonce_response = _claim_production_webhook_nonce(
            path_strategy=path_strategy,
            nonce=str(body.get("nonce") or "").strip(),
            raw_body=raw_body,
            timestamp=body.get("timestamp"),
        )
        if nonce_response is not None:
            return nonce_response

    action = str(body.get("action") or "").strip().upper()
    if action not in strategy_fanout.BROADCAST_ACTIONS:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": f"Unsupported action: {action or 'missing'}."},
        )
    signal_id = str(body.get("signal_id") or "").strip()
    if not signal_id:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": "signal_id is required."},
        )
    signal_time = str(body.get("signal_time") or "")

    if action == "HOLD":
        # Connectivity-only acknowledgment: proves the secret and URL are
        # correct without touching any subscriber's account or position.
        return JSONResponse(
            status_code=200,
            content={"ok": True, "signal_id": signal_id, "status": "NO_OP", "reason": "HOLD"},
        )

    signal = strategy_fanout.build_broadcast_signal(path_strategy, action, signal_id, signal_time)

    valid, error = validate_signal(signal)
    if not valid:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": error or "Invalid signal."},
        )
    event_provider = f"strategy:{path_strategy}"
    event_claimed = False
    if database_configured():
        try:
            event_claim = webhook_replay_store.claim_webhook_event(
                provider=event_provider,
                event_id=signal.signal_id,
                raw_body=raw_body,
                signature_ok=True,
                metadata={
                    "strategy_name": path_strategy,
                    "payload_format": signal.payload_format,
                    "action": signal.action,
                    "side": signal.side,
                },
            )
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"ok": False, "error": "Webhook replay store unavailable."},
            )
        if event_claim.get("status") == "tampered":
            error_message = (
                "Duplicate webhook signal."
                if settings.is_production
                else "Duplicate signal has a different body."
            )
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "error": error_message,
                    "signal_id": signal.signal_id,
                },
            )
        if event_claim.get("status") == "duplicate":
            error_message = "Duplicate webhook signal." if settings.is_production else "Duplicate signal."
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "error": error_message,
                    "signal_id": signal.signal_id,
                },
            )
        event_claimed = event_claim.get("status") == "fresh"

    queued = strategy_fanout.enqueue_strategy_signal(path_strategy, signal)
    if not queued["accepted"]:
        if event_claimed:
            try:
                webhook_replay_store.update_webhook_event(
                    provider=event_provider,
                    event_id=signal.signal_id,
                    processed_status="rejected",
                    error="duplicate_strategy_signal",
                )
            except Exception:
                pass
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": "Duplicate signal.",
                "signal_id": signal.signal_id,
            },
        )

    wake_strategy_job_worker()
    if event_claimed:
        try:
            webhook_replay_store.update_webhook_event(
                provider=event_provider,
                event_id=signal.signal_id,
                processed_status="queued",
                metadata={"subscriber_count": queued["subscriber_count"]},
            )
        except Exception:
            pass
    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "signal_id": signal.signal_id,
            "strategy_name": path_strategy,
            "subscriber_count": queued["subscriber_count"],
            "status": "queued",
        },
    )


class EgressPayload(BaseModel):
    user_id: str
    public_ip: str = Field(min_length=3, max_length=64)
    proxy_url: str = Field(min_length=3, max_length=512)
    active: bool = True


class EgressSelectionPayload(BaseModel):
    public_ip: str = Field(min_length=3, max_length=64)


@router.get("/api/strategies/egress/options")
def egress_options(user: CurrentUser = Depends(get_current_user)) -> dict:
    entitlement_response = _require_static_ip_entitlement(user)
    if entitlement_response is not None:
        return entitlement_response
    try:
        return strategy_fanout.user_egress_options(user.id)
    except ValueError as exc:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": str(exc)},
        )


@router.post("/api/strategies/egress/select")
def select_egress(
    payload: EgressSelectionPayload,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    entitlement_response = _require_static_ip_entitlement(user)
    if entitlement_response is not None:
        return entitlement_response
    try:
        result = strategy_fanout.select_user_egress(user.id, payload.public_ip)
    except ValueError as exc:
        status_code = 409 if "already assigned" in str(exc) else 400
        return JSONResponse(
            status_code=status_code,
            content={"ok": False, "error": str(exc)},
        )
    return {"ok": True, **result}


def _entitlement_error(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"ok": False, "error": message},
    )


def _require_static_ip_entitlement(user: CurrentUser) -> JSONResponse | None:
    try:
        entitlements.require_static_ip_entitlement_for_user(user.id)
    except entitlements.EntitlementError:
        return _entitlement_error("Static IP entitlement is required.")
    return None


def _require_real_order_strategy_entitlements(user: CurrentUser) -> JSONResponse | None:
    try:
        entitlements.require_live_entitlement_for_user(user.id)
        entitlements.require_strategy_entitlement_for_user(user.id)
    except entitlements.EntitlementError as exc:
        message = str(exc) or "Live entitlement is required."
        if "Strategy" not in message:
            message = "Live entitlement is required."
        return _entitlement_error(message)
    return None


@router.post("/api/admin/egress")
def assign_egress(
    payload: EgressPayload,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    try:
        user_id = uuid.UUID(payload.user_id)
        status = strategy_fanout.set_user_egress(
            user_id,
            public_ip=payload.public_ip,
            proxy_url=payload.proxy_url,
            active=payload.active,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": str(exc)},
        )
    return {"ok": True, "egress": status}


@router.post("/api/admin/egress/{user_id}/verify")
def verify_egress(
    user_id: uuid.UUID,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    result = strategy_fanout.verify_user_egress(user_id)
    return {"ok": bool(result.get("ok")), "egress": result}
