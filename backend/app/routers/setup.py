from __future__ import annotations

from collections import Counter
from copy import deepcopy
import csv
import json
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.config import BACKEND_DIR, RUNTIME_STATE_DIR, settings
from app.services.audit_logger import log_audit_event
from app.services.credential_vault import (
    VaultError,
    WEBHOOK_SECRET_MIN_LENGTH,
    clear_dhan_credentials,
    dhan_credentials_snapshot,
    dhan_metadata,
    dhan_token_age_metadata,
    get_dhan_credentials,
    get_webhook_secret,
    mask_client_id,
    restore_dhan_credentials_snapshot,
    save_dhan_credentials,
    save_webhook_secret,
    vault_status,
    webhook_secret_strength_error,
    webhook_secret_metadata,
)
from app.services.dhan_client import DhanFundsResult, MockDhanClient, RealDhanClient
from app.services.dhan_debugger import get_outgoing_ip
from app.services.dhan_error_interpreter import interpret_dhan_error
from app.services.state_store import (
    default_wallet_snapshot,
    get_app_state,
    get_runtime_settings,
    get_wallet_snapshot,
    set_wallet_snapshot,
    update_app_state,
    update_runtime_settings,
    utc_now,
)


router = APIRouter()

DHAN_CONNECT_RATE_LIMIT_MAX_ATTEMPTS = 5
DHAN_CONNECT_RATE_LIMIT_WINDOW_SECONDS = 5 * 60
RISK_OPTION_SL_MAX_PERCENT = 80.0
RISK_OPTION_TP_MAX_PERCENT = 500.0
_DHAN_CONNECT_RATE_LIMIT: dict[str, list[float]] = {}
_DHAN_CONNECT_RATE_LIMIT_LOCK = threading.RLock()
_NIFTY_LOT_SIZE_CACHE: dict[str, Any] = {"key": None, "lot_size": None}


def _validate_sl_percent(value: float | None) -> float | None:
    if value is None:
        return value
    if not 0 < float(value) < RISK_OPTION_SL_MAX_PERCENT:
        raise ValueError(f"Option SL percent must be greater than 0 and less than {RISK_OPTION_SL_MAX_PERCENT:g}.")
    return value


def _validate_tp_percent(value: float | None) -> float | None:
    if value is None:
        return value
    if not 0 < float(value) < RISK_OPTION_TP_MAX_PERCENT:
        raise ValueError(f"Option TP percent must be greater than 0 and less than {RISK_OPTION_TP_MAX_PERCENT:g}.")
    return value


def _scrip_master_candidates() -> list[Path]:
    configured = Path(settings.DHAN_SCRIP_MASTER_PATH)
    target = configured if configured.is_absolute() else BACKEND_DIR / configured
    return [target, RUNTIME_STATE_DIR / "api-scrip-master-detailed.csv"]


def _current_nifty_lot_size_from_scrip_master() -> int | None:
    candidates = [path for path in _scrip_master_candidates() if path.exists()]
    cache_key = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in candidates)
    if _NIFTY_LOT_SIZE_CACHE.get("key") == cache_key:
        return _NIFTY_LOT_SIZE_CACHE.get("lot_size")

    lot_size: int | None = None
    for path in candidates:
        counts: Counter[int] = Counter()
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    underlying = str(row.get("UNDERLYING_SYMBOL") or row.get("SEM_SMST_SECURITY_NAME") or "").upper()
                    symbol_name = str(row.get("SYMBOL_NAME") or row.get("SM_SYMBOL_NAME") or "").upper()
                    instrument = str(row.get("INSTRUMENT") or row.get("INSTRUMENT_TYPE") or row.get("SEM_INSTRUMENT_NAME") or "").upper()
                    option_type = str(row.get("OPTION_TYPE") or row.get("SEM_OPTION_TYPE") or "").upper()
                    if "NIFTY" != underlying and symbol_name != "NIFTY":
                        continue
                    if "OPT" not in instrument and option_type not in {"CE", "PE"}:
                        continue
                    raw_lot = row.get("LOT_SIZE") or row.get("SEM_LOT_UNITS") or row.get("LOT_UNITS")
                    try:
                        parsed_lot = int(float(str(raw_lot or "").strip()))
                    except (TypeError, ValueError):
                        continue
                    if parsed_lot > 0:
                        counts[parsed_lot] += 1
            if counts:
                lot_size = counts.most_common(1)[0][0]
                break
        except OSError:
            continue

    _NIFTY_LOT_SIZE_CACHE.update({"key": cache_key, "lot_size": lot_size})
    return lot_size


def _minimum_real_nifty_order_qty() -> int | None:
    if settings.DHAN_MODE.upper() != "REAL":
        return None
    return _current_nifty_lot_size_from_scrip_master()


def _validate_max_qty_per_order(value: int | None) -> int | None:
    if value is None:
        return value
    minimum = _minimum_real_nifty_order_qty()
    if minimum and int(value) < minimum:
        raise ValueError(
            f"Max quantity per order must be at least current NIFTY lot size ({minimum}) in REAL mode. "
            f"Signal qty is absolute Dhan quantity, not lot count."
        )
    return value


class DhanConnectRequest(BaseModel):
    client_id: str | None = None
    access_token: str | None = None


class WebhookSecretRequest(BaseModel):
    webhook_secret: str = Field(..., min_length=WEBHOOK_SECRET_MIN_LENGTH)


class RiskSetupRequest(BaseModel):
    max_qty_per_order: int = Field(default=1, ge=1)
    max_trades_per_day: int = Field(default=1, ge=1)
    daily_loss_limit: float = Field(default=500, gt=0)
    server_side_exit_enabled: bool = True
    marketfeed_ws_enabled: bool = True
    option_ltp_source: str = "AUTO"
    option_exit_mode: str = "DHAN_SUPER"
    option_ws_stale_seconds: float = Field(default=5.0, ge=1.0)
    option_rest_fallback_enabled: bool = True
    option_rest_fallback_cooldown_seconds: float = Field(default=15.0, ge=1.0)
    option_sl_percent: float = Field(default=10.0, gt=0)
    option_tp_percent: float = Field(default=20.0, gt=0)
    option_ltp_poll_seconds: float = Field(default=1.0, ge=1.0)
    allow_entry: bool = True
    allow_exit: bool = True

    _max_qty_business_rule = field_validator("max_qty_per_order")(_validate_max_qty_per_order)
    _sl_business_rule = field_validator("option_sl_percent")(_validate_sl_percent)
    _tp_business_rule = field_validator("option_tp_percent")(_validate_tp_percent)


class RiskSettingsPatchRequest(BaseModel):
    max_qty_per_order: int | None = Field(default=None, ge=1)
    max_trades_per_day: int | None = Field(default=None, ge=1)
    daily_loss_limit: float | None = Field(default=None, gt=0)
    server_side_exit_enabled: bool | None = None
    marketfeed_ws_enabled: bool | None = None
    option_ltp_source: str | None = None
    option_exit_mode: str | None = None
    option_ws_stale_seconds: float | None = Field(default=None, ge=1.0)
    option_rest_fallback_enabled: bool | None = None
    option_rest_fallback_cooldown_seconds: float | None = Field(default=None, ge=1.0)
    option_sl_percent: float | None = Field(default=None, gt=0)
    option_tp_percent: float | None = Field(default=None, gt=0)
    option_ltp_poll_seconds: float | None = Field(default=None, ge=1.0)
    allow_entry: bool | None = None
    allow_exit: bool | None = None

    _max_qty_business_rule = field_validator("max_qty_per_order")(_validate_max_qty_per_order)
    _sl_business_rule = field_validator("option_sl_percent")(_validate_sl_percent)
    _tp_business_rule = field_validator("option_tp_percent")(_validate_tp_percent)


def public_base_url() -> str:
    return settings.BACKEND_PUBLIC_BASE_URL.rstrip("/")


def tradingview_webhook_url() -> str:
    base = public_base_url()
    return f"{base}/webhook/tradingview" if base else ""


def _request_client_ip(request: Request) -> str:
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip.split(",")[0].strip()
    forwarded_for = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _enforce_dhan_connect_rate_limit(request: Request) -> None:
    client_ip = _request_client_ip(request)
    now = time.monotonic()
    cutoff = now - DHAN_CONNECT_RATE_LIMIT_WINDOW_SECONDS
    with _DHAN_CONNECT_RATE_LIMIT_LOCK:
        attempts = [timestamp for timestamp in _DHAN_CONNECT_RATE_LIMIT.get(client_ip, []) if timestamp >= cutoff]
        if len(attempts) >= DHAN_CONNECT_RATE_LIMIT_MAX_ATTEMPTS:
            retry_after = max(1, int(DHAN_CONNECT_RATE_LIMIT_WINDOW_SECONDS - (now - attempts[0])))
            _DHAN_CONNECT_RATE_LIMIT[client_ip] = attempts
            log_audit_event(
                "DHAN_CONNECT_RATE_LIMITED",
                "Dhan connect attempt rate-limited.",
                severity="WARNING",
                metadata={"client_ip": client_ip, "retry_after_seconds": retry_after},
            )
            raise HTTPException(
                status_code=429,
                detail=f"Too many Dhan connect attempts. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )
        attempts.append(now)
        _DHAN_CONNECT_RATE_LIMIT[client_ip] = attempts


def _rollback_dhan_connect(previous_dhan: dict[str, Any] | None, previous_wallet: dict[str, Any], reason: str) -> None:
    try:
        restore_dhan_credentials_snapshot(previous_dhan)
        set_wallet_snapshot(previous_wallet)
        log_audit_event(
            "DHAN_CONNECT_ROLLED_BACK",
            "Dhan connect state rolled back after setup failure.",
            severity="WARNING",
            metadata={"reason": reason},
        )
    except Exception as rollback_exc:  # pragma: no cover - last-resort observability.
        log_audit_event(
            "DHAN_CONNECT_ROLLBACK_FAILED",
            "Dhan connect rollback failed; manual credential check required.",
            severity="ERROR",
            metadata={"reason": reason, "rollback_error": str(rollback_exc)},
        )


def _model_dump(model: BaseModel, **kwargs: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)


def _normalize_risk_changes(changes: dict[str, Any]) -> dict[str, Any]:
    normalized = {key: value for key, value in changes.items() if value is not None}
    if "option_ltp_source" in normalized:
        normalized["option_ltp_source"] = str(normalized["option_ltp_source"]).upper()
    if "option_exit_mode" in normalized:
        normalized["option_exit_mode"] = str(normalized["option_exit_mode"]).upper()
    return normalized


def _save_risk_settings(changes: dict[str, Any]) -> dict[str, Any]:
    saved = update_runtime_settings(**_normalize_risk_changes(changes))
    log_audit_event("RISK_SETTINGS_UPDATED", "Risk settings updated.", metadata=saved)
    return saved


def _wallet_from_funds(result: DhanFundsResult, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or default_wallet_snapshot()
    available = result.available_balance
    session_start = previous.get("session_start_balance")
    if session_start is None and available is not None:
        session_start = available
    session_pnl = None
    if session_start is not None and available is not None:
        session_pnl = round(float(available) - float(session_start), 2)

    snapshot = default_wallet_snapshot()
    snapshot.update(
        {
            "success": result.success,
            "message": result.message,
            "client_id": mask_client_id(result.client_id),
            "available_balance": available,
            "withdrawable_balance": result.withdrawable_balance,
            "utilized_amount": result.utilized_amount,
            "sod_limit": result.sod_limit,
            "collateral_amount": result.collateral_amount,
            "blocked_payout_amount": result.blocked_payout_amount,
            "session_start_balance": session_start,
            "session_pnl": session_pnl,
            "last_checked_at": utc_now(),
            "raw_response": result.raw_response,
        }
    )
    return snapshot


def _connection_failure_kind(message: str, status_code: int | None, raw_response: Any = None) -> str:
    text = " ".join([message, json.dumps(raw_response, default=str) if raw_response is not None else ""]).lower()
    if "belongs to client id" in text or "client id" in text and "not configured" in text:
        return "client ID mismatch"
    if any(term in text for term in ("static ip", "whitelist", "white list", "unauthorized ip", "unauthorised ip", "invalid ip")):
        return "IP issue"
    if status_code in (401, 403) or any(term in text for term in ("token", "auth", "unauthorized", "unauthorised")):
        return "token invalid"
    return "unknown"


def validate_dhan_credentials(client_id: str, access_token: str) -> tuple[bool, str, DhanFundsResult | None, dict[str, Any]]:
    if settings.DHAN_MODE.upper() == "REAL":
        validation = RealDhanClient().validate_token(client_id=client_id, access_token=access_token)
        if not validation.success:
            kind = _connection_failure_kind(validation.message, validation.status_code, validation.raw_response)
            interpreted = interpret_dhan_error(validation.status_code, validation.raw_response or validation.message)
            return (
                False,
                f"Dhan connection failed: {kind}. {validation.message}",
                None,
                {
                    "status_code": validation.status_code,
                    "error_kind": kind,
                    "interpreted_error": interpreted,
                },
            )
        funds = RealDhanClient().get_fund_limit(client_id=client_id, access_token=access_token)
        return True, "Dhan connected successfully.", funds, {"status_code": validation.status_code}

    validation = MockDhanClient().validate_token(client_id=client_id, access_token="")
    funds = MockDhanClient().get_fund_limit(client_id=client_id, access_token="")
    return validation.success, "Dhan connected successfully.", funds, {"mock": True}


def risk_settings_valid(runtime: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
    runtime = runtime or get_runtime_settings()
    issues: list[str] = []
    if int(runtime.get("max_qty_per_order") or 0) <= 0:
        issues.append("Max quantity per order must be greater than zero.")
    if int(runtime.get("max_trades_per_day") or 0) <= 0:
        issues.append("Max trades per day must be greater than zero.")
    if float(runtime.get("daily_loss_limit") or 0) <= 0:
        issues.append("Daily loss limit must be greater than zero.")
    if float(runtime.get("option_sl_percent") or 0) <= 0:
        issues.append("Option SL percent must be greater than zero.")
    elif float(runtime.get("option_sl_percent") or 0) >= RISK_OPTION_SL_MAX_PERCENT:
        issues.append(f"Option SL percent must be less than {RISK_OPTION_SL_MAX_PERCENT:g}.")
    if float(runtime.get("option_tp_percent") or 0) <= 0:
        issues.append("Option TP percent must be greater than zero.")
    elif float(runtime.get("option_tp_percent") or 0) >= RISK_OPTION_TP_MAX_PERCENT:
        issues.append(f"Option TP percent must be less than {RISK_OPTION_TP_MAX_PERCENT:g}.")
    if float(runtime.get("option_ltp_poll_seconds") or 0) < 1:
        issues.append("Option LTP poll seconds must be at least 1.")
    if float(runtime.get("option_ws_stale_seconds") or 0) < 1:
        issues.append("Option WebSocket stale seconds must be at least 1.")
    if float(runtime.get("option_rest_fallback_cooldown_seconds") or 0) < 1:
        issues.append("Option REST fallback cooldown seconds must be at least 1.")
    if str(runtime.get("option_ltp_source") or "WEBSOCKET").upper() not in {"WEBSOCKET", "REST", "AUTO"}:
        issues.append("Option LTP source must be WEBSOCKET, REST, or AUTO.")
    if str(runtime.get("option_exit_mode") or "DHAN_SUPER").upper() not in {"DHAN_SUPER", "SERVER"}:
        issues.append("Option exit mode must be DHAN_SUPER or SERVER.")
    minimum_qty = _minimum_real_nifty_order_qty()
    if minimum_qty and int(runtime.get("max_qty_per_order") or 0) < minimum_qty:
        issues.append(
            f"Max quantity per order must be at least current NIFTY lot size ({minimum_qty}) in REAL mode."
        )
    return not issues, issues


def setup_readiness(*, check_dhan_ping: bool = False) -> dict[str, Any]:
    runtime = get_runtime_settings()
    creds = get_dhan_credentials()
    webhook_secret = get_webhook_secret()
    risk_ok, risk_issues = risk_settings_valid(runtime)
    base_url = public_base_url()
    issues: list[str] = []
    warnings: list[str] = []

    if not creds:
        issues.append("Dhan credentials are not connected.")
    if not webhook_secret:
        issues.append("Webhook secret is not set.")
    else:
        secret_strength_error = webhook_secret_strength_error(webhook_secret)
        if secret_strength_error:
            issues.append(secret_strength_error)
    if not base_url:
        issues.append("BACKEND_PUBLIC_BASE_URL is not set.")
    elif "yourdomain.com" in base_url:
        issues.append("BACKEND_PUBLIC_BASE_URL still uses the placeholder domain.")
    if bool(runtime.get("emergency_stop")):
        issues.append("Emergency stop is active.")
    if bool(runtime.get("global_kill_switch")):
        issues.append("Global kill switch is active.")
    issues.extend(risk_issues)

    dhan_ping: dict[str, Any] | None = None
    if check_dhan_ping and creds:
        ok, message, _funds, details = validate_dhan_credentials(creds.client_id, creds.access_token)
        dhan_ping = {"ok": ok, "message": message, **details}
        if not ok:
            issues.append(message)

    if settings.DHAN_MODE.upper() == "REAL" and not settings.ENABLE_LIVE_ORDERS:
        warnings.append("REAL mode is configured, but ENABLE_LIVE_ORDERS=false. Alerts will be parsed and blocked before Dhan order placement.")
    if settings.DHAN_MODE.upper() == "REAL" and settings.ENABLE_LIVE_ORDERS:
        warnings.append("LIVE ORDERS ENABLED - real money orders can be placed after risk checks.")

    return {
        "ready": not issues,
        "issues": issues,
        "warnings": warnings,
        "dhan_ping": dhan_ping,
        "risk_configured": risk_ok,
    }


def setup_status_payload(*, include_outgoing_ip: bool = True) -> dict[str, Any]:
    runtime = get_runtime_settings()
    app_state = get_app_state()
    meta = dhan_metadata()
    webhook_meta = webhook_secret_metadata()
    outgoing = get_outgoing_ip(timeout=2.0) if include_outgoing_ip else {"outgoing_ip": None, "ok": False, "error": None}
    readiness = setup_readiness(check_dhan_ping=False)
    wallet = get_wallet_snapshot()

    token_meta = dhan_token_age_metadata()
    return {
        "dhan_connected": bool(meta["connected"]),
        "dhan_client_id_masked": meta["client_id_masked"],
        "access_token_present": meta["access_token_present"],
        "access_token_masked": meta["access_token_masked"],
        "webhook_secret_set": bool(webhook_meta["set"]),
        "webhook_secret_masked": webhook_meta["masked"],
        "risk_configured": bool(readiness["risk_configured"]),
        "engine_started": bool(app_state.get("webhook_trading_enabled")),
        "wallet": wallet,
        "backend_public_base_url": public_base_url(),
        "webhook_url": tradingview_webhook_url(),
        "outgoing_ip": outgoing.get("outgoing_ip"),
        "outgoing_ip_check": outgoing,
        "static_ip_note": "Dhan orders will be sent from backend server IP. Make sure this IP is whitelisted in Dhan.",
        "token_age": token_meta,
        "mode": {
            "dhan_mode": settings.DHAN_MODE.upper(),
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
        },
        "settings": {
            "max_qty_per_order": runtime.get("max_qty_per_order"),
            "max_trades_per_day": runtime.get("max_trades_per_day"),
            "daily_loss_limit": runtime.get("daily_loss_limit"),
            "server_side_exit_enabled": runtime.get("server_side_exit_enabled"),
            "marketfeed_ws_enabled": runtime.get("marketfeed_ws_enabled"),
            "option_ltp_source": runtime.get("option_ltp_source"),
            "option_exit_mode": runtime.get("option_exit_mode"),
            "option_ws_stale_seconds": runtime.get("option_ws_stale_seconds"),
            "option_rest_fallback_enabled": runtime.get("option_rest_fallback_enabled"),
            "option_rest_fallback_cooldown_seconds": runtime.get("option_rest_fallback_cooldown_seconds"),
            "option_sl_percent": runtime.get("option_sl_percent"),
            "option_tp_percent": runtime.get("option_tp_percent"),
            "option_ltp_poll_seconds": runtime.get("option_ltp_poll_seconds"),
            "allow_entry": runtime.get("allow_entry"),
            "allow_exit": runtime.get("allow_exit"),
            "emergency_stop": bool(runtime.get("emergency_stop")),
            "global_kill_switch": bool(runtime.get("global_kill_switch")),
        },
        "app_state": app_state,
        "readiness": readiness,
        "debug_enabled": settings.DEBUG_ENABLED,
        "vault": vault_status(),
        "qty_mode_note": "Signal qty is treated as ABSOLUTE Dhan quantity (not lots). Ensure your signal qty is the correct number of contracts, not lot count.",
    }


@router.get("/setup/status")
def setup_status() -> dict[str, Any]:
    return setup_status_payload()


@router.post("/setup/dhan/connect")
def connect_dhan(body: DhanConnectRequest, request: Request) -> dict[str, Any]:
    _enforce_dhan_connect_rate_limit(request)
    existing = get_dhan_credentials()
    submitted_client_id = (body.client_id or "").strip()
    submitted_access_token = (body.access_token or "").strip()
    client_id = submitted_client_id or (existing.client_id if existing else "")
    access_token = submitted_access_token or (existing.access_token if existing else "")
    has_credential_changes = bool(submitted_client_id or submitted_access_token)
    if not client_id:
        raise HTTPException(status_code=400, detail="Dhan Client ID is required.")
    if not access_token:
        raise HTTPException(status_code=400, detail="Dhan Access Token is required.")
    vault = vault_status()
    if not vault["ready"] and not vault.get("local_mock_allowed"):
        message = f"Dhan connection failed: {vault['error']}"
        log_audit_event("DHAN_CONNECT_BLOCKED", message, severity="WARNING")
        raise HTTPException(status_code=400, detail=message)
    ok, message, funds, details = validate_dhan_credentials(client_id, access_token)
    if not ok:
        log_audit_event("DHAN_CONNECT_FAILED", message, severity="WARNING", metadata=details)
        raise HTTPException(status_code=400, detail={"message": message, **details})

    previous_dhan = dhan_credentials_snapshot()
    previous_wallet = get_wallet_snapshot()
    try:
        if has_credential_changes:
            save_dhan_credentials(client_id, access_token)
        wallet = set_wallet_snapshot(_wallet_from_funds(funds, get_wallet_snapshot())) if funds else get_wallet_snapshot()
        token_meta = dhan_token_age_metadata()
        outgoing = get_outgoing_ip(timeout=3.0)
    except VaultError as exc:
        if has_credential_changes:
            _rollback_dhan_connect(previous_dhan, previous_wallet, str(exc))
        log_audit_event("DHAN_CONNECT_BLOCKED", str(exc), severity="WARNING")
        raise HTTPException(status_code=400, detail=f"Dhan connection failed: {exc}") from exc
    except Exception as exc:
        if has_credential_changes:
            _rollback_dhan_connect(previous_dhan, previous_wallet, str(exc))
        log_audit_event("DHAN_CONNECT_BLOCKED", str(exc), severity="ERROR")
        raise HTTPException(status_code=400, detail=f"Dhan connection failed: {exc}") from exc

    log_audit_event(
        "DHAN_CONNECTED",
        "Dhan connected successfully.",
        metadata={
            "client_id_masked": mask_client_id(client_id),
            "dhan_mode": settings.DHAN_MODE.upper(),
            "outgoing_ip": outgoing.get("outgoing_ip"),
        },
    )
    return {
        "success": True,
        "message": message,
        "dhan_connected": True,
        "dhan_client_id_masked": mask_client_id(client_id),
        "access_token_present": True,
        "wallet": wallet,
        "outgoing_ip": outgoing.get("outgoing_ip"),
        "ip_whitelist": {
            "checked": outgoing.get("ok", False),
            "backend_ip": outgoing.get("outgoing_ip"),
            "warning": (
                "Confirm this backend IP is whitelisted in your Dhan account. "
                "Dhan order placement requires static IP whitelisting."
            ),
        },
        "token": {
            "saved_at": token_meta.get("token_saved_at"),
            "age_minutes": token_meta.get("token_age_minutes"),
            "expires_in_hours_estimate": settings.TOKEN_MAX_AGE_HOURS,
            "warn_at_hours": settings.TOKEN_WARN_AGE_HOURS,
            "estimated_expiry_at": token_meta.get("token_estimated_expiry_at"),
        },
        "details": details,
    }


@router.post("/setup/dhan/disconnect")
def disconnect_dhan() -> dict[str, Any]:
    try:
        clear_dhan_credentials()
    except VaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    set_wallet_snapshot(default_wallet_snapshot())
    update_app_state(
        state="ENGINE_STOPPED",
        engine_started=False,
        webhook_trading_enabled=False,
        last_message="Dhan disconnected. Engine stopped.",
    )
    log_audit_event("DHAN_DISCONNECTED", "Dhan credentials cleared and engine stopped.", severity="WARNING")
    return {"success": True, "message": "Dhan disconnected. Engine stopped."}


@router.post("/setup/webhook-secret")
def configure_webhook_secret(body: WebhookSecretRequest) -> dict[str, Any]:
    try:
        save_webhook_secret(body.webhook_secret)
    except VaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_audit_event("WEBHOOK_SECRET_SET", "Webhook secret saved.")
    return {"success": True, "webhook_secret_set": True}


@router.post("/setup/risk")
def configure_risk(body: RiskSetupRequest) -> dict[str, Any]:
    saved = _save_risk_settings(_model_dump(body, exclude_unset=True))
    return {"success": True, "settings": saved}


@router.patch("/setup/risk")
def patch_risk(body: RiskSettingsPatchRequest) -> dict[str, Any]:
    changes = _model_dump(body, exclude_unset=True, exclude_none=True)
    if not changes:
        return {"success": True, "settings": get_runtime_settings()}
    saved = _save_risk_settings(changes)
    return {"success": True, "settings": saved}


# ---------------------------------------------------------------------------
# Scrip Master / Instrument List
# ---------------------------------------------------------------------------

DHAN_SCRIP_MASTER_URLS = [
    "https://images.dhan.co/api-data/api-scrip-master.csv",
    "https://images.dhan.co/api-data/api-scrip-master-detailed.csv",
]
_scrip_master_last_download: dict[str, Any] = {"downloaded_at": None, "ok": None, "error": None, "path": None}
_scrip_master_job_lock = threading.RLock()
_scrip_master_refresh_job: dict[str, Any] = {
    "job_id": None,
    "status": "IDLE",
    "started_at": None,
    "finished_at": None,
    "success": None,
    "message": None,
    "error": None,
    "path": None,
    "size_bytes": None,
    "results": [],
}


def _scrip_master_job_snapshot() -> dict[str, Any]:
    with _scrip_master_job_lock:
        return deepcopy(_scrip_master_refresh_job)


def _download_scrip_master_sync() -> dict[str, Any]:
    import httpx as _httpx
    from app.config import settings as _s, BACKEND_DIR, RUNTIME_STATE_DIR
    from pathlib import Path

    configured = Path(_s.DHAN_SCRIP_MASTER_PATH)
    target = configured if configured.is_absolute() else BACKEND_DIR / configured

    results = []
    for url in DHAN_SCRIP_MASTER_URLS:
        try:
            response = _httpx.get(url, timeout=60.0, follow_redirects=True)
            response.raise_for_status()
            content = response.content
            if not content or len(content) < 100:
                results.append({"url": url, "ok": False, "error": "Response too small; not a valid CSV."})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            fallback = RUNTIME_STATE_DIR / "api-scrip-master-detailed.csv"
            if "detailed" in url:
                fallback.parent.mkdir(parents=True, exist_ok=True)
                fallback.write_bytes(content)
            downloaded_at = utc_now()
            log_audit_event("SCRIP_MASTER_REFRESHED", f"Downloaded from {url}", metadata={"path": str(target), "size_bytes": len(content)})
            results.append({"url": url, "ok": True, "size_bytes": len(content), "path": str(target)})
            return {
                "success": True,
                "path": str(target),
                "downloaded_at": downloaded_at,
                "size_bytes": len(content),
                "results": results,
                "message": "Dhan scrip master downloaded successfully.",
                "url": url,
            }
        except Exception as exc:
            results.append({"url": url, "ok": False, "error": str(exc)})

    return {
        "success": False,
        "path": str(target),
        "downloaded_at": utc_now(),
        "size_bytes": None,
        "results": results,
        "message": "All Dhan scrip master download URLs failed.",
        "error": "All Dhan scrip master download URLs failed.",
    }


def _run_scrip_master_refresh_job(job_id: str) -> None:
    try:
        result = _download_scrip_master_sync()
    except Exception as exc:  # pragma: no cover - defensive guard around the worker.
        result = {
            "success": False,
            "path": None,
            "downloaded_at": utc_now(),
            "size_bytes": None,
            "results": [],
            "message": "Dhan scrip master refresh failed.",
            "error": str(exc),
        }

    finished_at = utc_now()
    success = bool(result.get("success"))
    with _scrip_master_job_lock:
        if _scrip_master_refresh_job.get("job_id") != job_id:
            return
        _scrip_master_refresh_job.update(
            {
                "status": "SUCCEEDED" if success else "FAILED",
                "finished_at": finished_at,
                "success": success,
                "message": result.get("message"),
                "error": result.get("error"),
                "path": result.get("path"),
                "size_bytes": result.get("size_bytes"),
                "results": result.get("results") or [],
            }
        )
        _scrip_master_last_download.update(
            {
                "downloaded_at": result.get("downloaded_at") or finished_at,
                "ok": success,
                "error": result.get("error"),
                "path": result.get("path"),
                "url": result.get("url"),
                "size_bytes": result.get("size_bytes"),
                "job_id": job_id,
            }
        )

    if not success:
        log_audit_event(
            "SCRIP_MASTER_REFRESH_FAILED",
            str(result.get("message") or "Dhan scrip master refresh failed."),
            severity="WARNING",
            metadata={"job_id": job_id, "results": result.get("results") or []},
        )


@router.post("/setup/scrip-master/refresh")
def refresh_scrip_master() -> dict[str, Any]:
    """
    Download the Dhan instrument / scrip master CSV and save to the configured path.
    Should be called manually on setup or once daily — NOT on every request.

    Official Dhan URLs:
      https://images.dhan.co/api-data/api-scrip-master.csv
      https://images.dhan.co/api-data/api-scrip-master-detailed.csv
    """
    with _scrip_master_job_lock:
        if _scrip_master_refresh_job.get("status") == "RUNNING":
            job = deepcopy(_scrip_master_refresh_job)
            return {
                "success": True,
                "accepted": False,
                "job_id": job.get("job_id"),
                "status": job.get("status"),
                "refresh_job": job,
                "message": "Scrip master refresh is already running.",
            }

        job_id = uuid.uuid4().hex
        started_at = utc_now()
        _scrip_master_refresh_job.update(
            {
                "job_id": job_id,
                "status": "RUNNING",
                "started_at": started_at,
                "finished_at": None,
                "success": None,
                "message": "Dhan scrip master refresh started.",
                "error": None,
                "path": None,
                "size_bytes": None,
                "results": [],
            }
        )
        job = deepcopy(_scrip_master_refresh_job)

    thread = threading.Thread(target=_run_scrip_master_refresh_job, args=(job_id,), name=f"scrip-master-refresh-{job_id[:8]}", daemon=True)
    thread.start()
    log_audit_event("SCRIP_MASTER_REFRESH_STARTED", "Dhan scrip master refresh job started.", metadata={"job_id": job_id})
    return {
        "success": True,
        "accepted": True,
        "job_id": job_id,
        "status": "RUNNING",
        "refresh_job": job,
        "message": "Dhan scrip master refresh started. Poll /api/setup/scrip-master/status for completion.",
    }


@router.get("/setup/scrip-master/status")
def scrip_master_status() -> dict[str, Any]:
    """Return scrip master file status and last download info."""
    from app.config import settings as _s, BACKEND_DIR, RUNTIME_STATE_DIR
    from pathlib import Path

    configured = Path(_s.DHAN_SCRIP_MASTER_PATH)
    target = configured if configured.is_absolute() else BACKEND_DIR / configured
    fallback = RUNTIME_STATE_DIR / "api-scrip-master-detailed.csv"

    def _file_info(path: Path) -> dict[str, Any]:
        if path.exists():
            stat = path.stat()
            return {"exists": True, "path": str(path), "size_bytes": stat.st_size}
        return {"exists": False, "path": str(path)}

    return {
        "configured_path": _file_info(target),
        "fallback_path": _file_info(fallback),
        "auto_resolve_security_id": _s.AUTO_RESOLVE_SECURITY_ID,
        "allow_default_security_id": _s.ALLOW_DEFAULT_SECURITY_ID,
        "last_download": _scrip_master_last_download,
        "refresh_job": _scrip_master_job_snapshot(),
        "download_urls": DHAN_SCRIP_MASTER_URLS,
    }


@router.get("/setup/security-id/resolve")
def debug_resolve_security_id(
    symbol: str,
    expiry: str,
    strike: float,
    option_side: str,
    exchange_segment: str = "NSE_FNO",
) -> dict[str, Any]:
    """
    Debug endpoint: resolve a security ID from the scrip master without placing an order.
    GET /api/setup/security-id/resolve?symbol=NIFTY&expiry=2026-05-28&strike=22500&option_side=CE

    Use this to verify scrip master lookup before enabling live orders.
    """
    from app.services.security_id_resolver import resolve_security_id_for_contract

    result = resolve_security_id_for_contract(
        symbol=symbol.upper(),
        expiry=expiry,
        strike=strike,
        option_side=option_side.upper(),
        exchange_segment=exchange_segment.upper(),
    )
    return {
        "input": {
            "symbol": symbol.upper(),
            "expiry": expiry,
            "strike": strike,
            "option_side": option_side.upper(),
            "exchange_segment": exchange_segment.upper(),
        },
        **result.model_dump(),
    }
