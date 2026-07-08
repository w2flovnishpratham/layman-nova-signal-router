"""Strategy subscriptions, shared TradingView webhook, and egress assignment."""
from __future__ import annotations

import hmac
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.dependencies import get_current_user, require_admin
from app.config import settings
from app.db import models
from app.db.engine import database_configured, session_scope
from app.services import entitlements, live_engine, strategy_fanout
from app.services import webhook_replay_store
from app.services import strategy_risk
from app.services.signal_parser import PayloadParseError, parse_webhook_payload
from app.services.signal_validator import validate_signal
from app.services.user_context import CurrentUser
from app.workers.strategy_job_worker import wake_strategy_job_worker


router = APIRouter(tags=["Strategies"])
_WEBHOOK_REQUESTS: dict[str, list[float]] = {}
_WEBHOOK_RATE_LOCK = threading.RLock()
_FANOUT_QTY_PLACEHOLDER = 1
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


def _fanout_parse_body(body: dict) -> dict:
    """Add parser-only defaults that are replaced before per-user execution."""
    if body.get("qty") in (None, ""):
        body = dict(body)
        body["qty"] = _FANOUT_QTY_PLACEHOLDER
    return body


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


def _require_internal_strategy_status_user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.is_admin or user.is_dev:
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Internal strategy status access required.",
    )


@router.get("/api/strategies/subscriptions")
def my_subscriptions(user: CurrentUser = Depends(get_current_user)) -> dict:
    return {"subscriptions": strategy_fanout.list_user_subscriptions(user.id)}


@router.get("/api/strategies/catalog")
def strategy_catalog(
    user: CurrentUser = Depends(_require_internal_strategy_status_user),
) -> dict:
    if not database_configured():
        return {"ok": True, "database_configured": False, "catalog": []}
    with session_scope() as db:
        catalog_rows = db.scalars(
            select(models.StrategyCatalog)
            .order_by(models.StrategyCatalog.name.asc(), models.StrategyCatalog.code.asc())
        ).all()
        version_rows = db.scalars(
            select(models.StrategyVersion)
            .order_by(models.StrategyVersion.created_at.asc(), models.StrategyVersion.version.asc())
        ).all()
        versions_by_strategy: dict[uuid.UUID, list[models.StrategyVersion]] = {}
        for version in version_rows:
            versions_by_strategy.setdefault(version.strategy_id, []).append(version)
        return {
            "ok": True,
            "database_configured": True,
            "catalog": [
                _strategy_catalog_public(row, versions_by_strategy.get(row.id, []))
                for row in catalog_rows
            ],
            "viewer": _strategy_status_viewer(user),
        }


@router.get("/api/strategies/instances")
def strategy_instances(
    user: CurrentUser = Depends(_require_internal_strategy_status_user),
) -> dict:
    if not database_configured():
        return {"ok": True, "database_configured": False, "instances": []}
    with session_scope() as db:
        rows = db.execute(
            select(
                models.UserStrategyInstance,
                models.StrategyCatalog,
                models.StrategyVersion,
            )
            .join(
                models.StrategyCatalog,
                models.StrategyCatalog.id == models.UserStrategyInstance.strategy_id,
            )
            .outerjoin(
                models.StrategyVersion,
                models.StrategyVersion.id == models.UserStrategyInstance.strategy_version_id,
            )
            .where(models.UserStrategyInstance.user_id == user.id)
            .order_by(models.UserStrategyInstance.created_at.desc())
        ).all()
        return {
            "ok": True,
            "database_configured": True,
            "instances": [
                _strategy_instance_public(instance, catalog, version)
                for instance, catalog, version in rows
            ],
            "viewer": _strategy_status_viewer(user),
        }


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

    try:
        signal = parse_webhook_payload(_fanout_parse_body(body))
    except PayloadParseError as exc:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": str(exc)},
        )

    payload_strategy = strategy_fanout.canonical_strategy_name(
        signal.strategy_code
    )
    if path_strategy != payload_strategy:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": "Strategy path does not match payload."},
        )

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


def _strategy_status_viewer(user: CurrentUser) -> dict[str, Any]:
    return {
        "id": user.id_str,
        "is_admin": user.is_admin,
        "is_dev": user.is_dev,
    }


def _strategy_catalog_public(
    row: models.StrategyCatalog,
    versions: list[models.StrategyVersion],
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "code": row.code,
        "name": row.name,
        "description": row.description,
        "source_type": row.source_type,
        "status": row.status,
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
        "versions": [_strategy_version_public(version) for version in versions],
    }


def _strategy_version_public(row: models.StrategyVersion) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "strategy_id": str(row.strategy_id),
        "version": row.version,
        "status": row.status,
        "payload_version": row.payload_version,
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _strategy_instance_public(
    instance: models.UserStrategyInstance,
    catalog: models.StrategyCatalog,
    version: models.StrategyVersion | None,
) -> dict[str, Any]:
    return {
        "id": str(instance.id),
        "strategy_id": str(catalog.id),
        "strategy_code": catalog.code,
        "strategy_name": catalog.name,
        "strategy_status": catalog.status,
        "strategy_source_type": catalog.source_type,
        "strategy_version_id": str(version.id) if version else None,
        "strategy_version": version.version if version else None,
        "payload_version": version.payload_version if version else None,
        "version_status": version.status if version else None,
        "instance_label": instance.instance_label,
        "source_type": instance.source_type,
        "status": instance.status,
        "execution_mode": instance.execution_mode,
        "lots": instance.lots,
        "side_preference": instance.side_preference,
        "created_at": _dt(instance.created_at),
        "updated_at": _dt(instance.updated_at),
    }


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


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
