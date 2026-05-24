from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.services.audit_logger import log_audit_event
from app.services.credential_vault import (
    VaultError,
    clear_dhan_credentials,
    dhan_metadata,
    dhan_token_age_metadata,
    get_dhan_credentials,
    get_webhook_secret,
    mask_client_id,
    save_dhan_credentials,
    save_webhook_secret,
    vault_status,
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


class DhanConnectRequest(BaseModel):
    client_id: str = Field(..., min_length=1)
    access_token: str = Field(..., min_length=1)


class WebhookSecretRequest(BaseModel):
    webhook_secret: str = Field(..., min_length=8)


class RiskSetupRequest(BaseModel):
    max_qty_per_order: int = Field(default=1, ge=1)
    max_trades_per_day: int = Field(default=1, ge=1)
    daily_loss_limit: float = Field(default=500, gt=0)
    allow_entry: bool = True
    allow_exit: bool = True


def public_base_url() -> str:
    return settings.BACKEND_PUBLIC_BASE_URL.rstrip("/")


def tradingview_webhook_url() -> str:
    base = public_base_url()
    return f"{base}/webhook/tradingview" if base else ""


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
        "webhook_secret_set": bool(webhook_meta["set"]),
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
def connect_dhan(body: DhanConnectRequest) -> dict[str, Any]:
    client_id = body.client_id.strip()
    access_token = body.access_token.strip()
    vault = vault_status()
    if not vault["ready"] and not vault.get("local_mock_allowed"):
        message = f"Dhan connection failed: {vault['error']}"
        log_audit_event("DHAN_CONNECT_BLOCKED", message, severity="WARNING")
        raise HTTPException(status_code=400, detail=message)
    ok, message, funds, details = validate_dhan_credentials(client_id, access_token)
    if not ok:
        log_audit_event("DHAN_CONNECT_FAILED", message, severity="WARNING", metadata=details)
        raise HTTPException(status_code=400, detail={"message": message, **details})

    try:
        save_dhan_credentials(client_id, access_token)
    except VaultError as exc:
        log_audit_event("DHAN_CONNECT_BLOCKED", str(exc), severity="WARNING")
        raise HTTPException(status_code=400, detail=f"Dhan connection failed: {exc}") from exc

    wallet = set_wallet_snapshot(_wallet_from_funds(funds, get_wallet_snapshot())) if funds else get_wallet_snapshot()
    token_meta = dhan_token_age_metadata()
    outgoing = get_outgoing_ip(timeout=3.0)
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
    saved = update_runtime_settings(
        max_qty_per_order=body.max_qty_per_order,
        max_trades_per_day=body.max_trades_per_day,
        daily_loss_limit=body.daily_loss_limit,
        allow_entry=body.allow_entry,
        allow_exit=body.allow_exit,
    )
    log_audit_event("RISK_SETTINGS_UPDATED", "Risk settings updated.", metadata=saved)
    return {"success": True, "settings": saved}


# ---------------------------------------------------------------------------
# Scrip Master / Instrument List
# ---------------------------------------------------------------------------

DHAN_SCRIP_MASTER_URLS = [
    "https://images.dhan.co/api-data/api-scrip-master.csv",
    "https://images.dhan.co/api-data/api-scrip-master-detailed.csv",
]
_scrip_master_last_download: dict[str, Any] = {"downloaded_at": None, "ok": None, "error": None, "path": None}


@router.post("/setup/scrip-master/refresh")
def refresh_scrip_master() -> dict[str, Any]:
    """
    Download the Dhan instrument / scrip master CSV and save to the configured path.
    Should be called manually on setup or once daily — NOT on every request.

    Official Dhan URLs:
      https://images.dhan.co/api-data/api-scrip-master.csv
      https://images.dhan.co/api-data/api-scrip-master-detailed.csv
    """
    import httpx as _httpx
    from app.config import settings as _s, BACKEND_DIR, RUNTIME_STATE_DIR
    from pathlib import Path

    # Resolve target path
    configured = Path(_s.DHAN_SCRIP_MASTER_PATH)
    target = configured if configured.is_absolute() else BACKEND_DIR / configured

    results = []
    success = False
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
            # Also save a copy to runtime_state for the resolver fallback path
            fallback = RUNTIME_STATE_DIR / "api-scrip-master-detailed.csv"
            if "detailed" in url:
                fallback.write_bytes(content)
            downloaded_at = utc_now()
            _scrip_master_last_download.update({
                "downloaded_at": downloaded_at,
                "ok": True,
                "error": None,
                "path": str(target),
                "url": url,
                "size_bytes": len(content),
            })
            log_audit_event("SCRIP_MASTER_REFRESHED", f"Downloaded from {url}", metadata={"path": str(target), "size_bytes": len(content)})
            results.append({"url": url, "ok": True, "size_bytes": len(content), "path": str(target)})
            success = True
            break  # Use first successful URL
        except Exception as exc:
            results.append({"url": url, "ok": False, "error": str(exc)})

    if not success:
        return {"success": False, "results": results, "message": "All Dhan scrip master download URLs failed."}

    return {
        "success": True,
        "path": str(target),
        "downloaded_at": _scrip_master_last_download["downloaded_at"],
        "size_bytes": _scrip_master_last_download.get("size_bytes"),
        "results": results,
        "message": "Dhan scrip master downloaded successfully.",
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
