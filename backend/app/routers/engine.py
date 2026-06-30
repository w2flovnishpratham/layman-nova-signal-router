from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.routers.setup import setup_readiness, setup_status_payload
from app.services.audit_logger import log_audit_event
from app.services.credential_vault import dhan_token_age_metadata
from app.services.dhan_debugger import get_outgoing_ip
from app.services.paper_portfolio import get_paper_portfolio
from app.services.state_store import get_app_state, get_engine_mode, get_open_position, set_engine_mode, update_app_state


router = APIRouter()


class StartEngineRequest(BaseModel):
    confirm_live_orders: bool = False
    engine_mode: Literal["paper", "live"] | None = None


def _build_engine_readiness_checks(
    readiness: dict[str, Any],
    token_meta: dict[str, Any],
    outgoing_ip: str | None,
    dhan_ping: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Build a structured per-check list for the engine start readiness response.
    Each check has: name, ok (bool), message (str), severity ("error"|"warning"|"ok").
    """
    checks: list[dict[str, Any]] = []
    engine_mode = readiness.get("engine_mode")
    checks.append({
        "name": "mode_selected",
        "ok": engine_mode in {"paper", "live"},
        "message": f"{str(engine_mode).title()} mode selected." if engine_mode else "Select Paper or Live mode.",
        "severity": "ok" if engine_mode else "error",
    })

    # 1. Dhan connected
    creds_ok = "Dhan credentials are not connected." not in readiness.get("issues", [])
    checks.append({
        "name": "dhan_connected",
        "ok": creds_ok,
        "message": "Dhan credentials connected." if creds_ok else "Dhan credentials are not connected.",
        "severity": "ok" if creds_ok else "error",
    })

    # 2. Dhan token validation (live ping result)
    if dhan_ping is not None:
        ping_ok = bool(dhan_ping.get("ok"))
        checks.append({
            "name": "dhan_token_valid",
            "ok": ping_ok,
            "message": dhan_ping.get("message", ""),
            "severity": "ok" if ping_ok else "error",
        })

    # 3. Token age
    token_expired = token_meta.get("token_expired")
    token_warn = token_meta.get("token_warn")
    age_minutes = token_meta.get("token_age_minutes")
    if token_expired is True:
        age_msg = f"Token is expired (age: {age_minutes} min). Reconnect Dhan before starting."
        token_age_ok = False
        token_age_severity = "error"
    elif token_warn is True:
        age_msg = f"Token is approaching expiry (age: {age_minutes} min / {settings.TOKEN_WARN_AGE_HOURS}h warn). Consider reconnecting Dhan."
        token_age_ok = True  # warn only, not hard block
        token_age_severity = "warning"
    elif token_expired is None:
        age_msg = "Token age unknown (credentials not yet connected)."
        token_age_ok = creds_ok  # if no creds, creds check already covers it
        token_age_severity = "warning" if creds_ok else "ok"
    else:
        age_msg = f"Token age: {age_minutes} min (within {settings.TOKEN_MAX_AGE_HOURS}h limit)."
        token_age_ok = True
        token_age_severity = "ok"
    checks.append({
        "name": "token_age",
        "ok": token_age_ok,
        "message": age_msg,
        "severity": token_age_severity,
        "age_minutes": age_minutes,
        "estimated_expiry_at": token_meta.get("token_estimated_expiry_at"),
    })

    # 4. Webhook secret
    secret_ok = "Webhook secret is not set." not in readiness.get("issues", [])
    checks.append({
        "name": "webhook_secret_set",
        "ok": secret_ok,
        "message": "Webhook secret configured." if secret_ok else "Webhook secret is not set.",
        "severity": "ok" if secret_ok else "error",
    })

    # 5. Risk limits — surface the *specific* blocker, not a generic label.
    risk_ok = readiness.get("risk_configured", False)
    risk_issues = readiness.get("risk_issues") or []
    if risk_ok:
        risk_message = "Risk limits configured."
    elif risk_issues:
        risk_message = "Risk limits not configured: " + " ".join(risk_issues)
    else:
        risk_message = "Risk limits not configured."
    checks.append({
        "name": "risk_limits",
        "ok": risk_ok,
        "message": risk_message,
        "severity": "ok" if risk_ok else "error",
    })

    # 6. Backend public URL
    url_issue = next((i for i in readiness.get("issues", []) if "BACKEND_PUBLIC_BASE_URL" in i), None)
    url_ok = url_issue is None
    checks.append({
        "name": "backend_public_url",
        "ok": url_ok,
        "message": "Backend public URL configured." if url_ok else url_issue or "Backend public URL not set.",
        "severity": "ok" if url_ok else "error",
    })

    # 7. Emergency stop / kill switch
    es_ok = "Emergency stop is active." not in readiness.get("issues", [])
    ks_ok = "Global kill switch is active." not in readiness.get("issues", [])
    checks.append({
        "name": "emergency_stop",
        "ok": es_ok,
        "message": "Emergency stop inactive." if es_ok else "Emergency stop is active — engine cannot start.",
        "severity": "ok" if es_ok else "error",
    })
    checks.append({
        "name": "global_kill_switch",
        "ok": ks_ok,
        "message": "Global kill switch inactive." if ks_ok else "Global kill switch is active.",
        "severity": "ok" if ks_ok else "error",
    })

    # 8. Static IP check (warn if outgoing IP unknown; real safety depends on Dhan whitelist)
    if engine_mode == "live" and settings.DHAN_MODE.upper() == "REAL" and settings.ENABLE_LIVE_ORDERS:
        if outgoing_ip:
            ip_msg = (
                f"Backend outgoing IP: {outgoing_ip}. "
                "Confirm this IP is whitelisted in your Dhan account before placing live orders."
            )
            ip_severity = "warning"  # always warn — we can't verify Dhan's whitelist programmatically
        else:
            ip_msg = "Could not determine backend outgoing IP. Dhan order placement requires static IP whitelisting."
            ip_severity = "warning"
        checks.append({
            "name": "static_ip",
            "ok": True,  # non-blocking warning — operator must verify manually
            "message": ip_msg,
            "severity": ip_severity,
            "outgoing_ip": outgoing_ip,
        })
    else:
        checks.append({
            "name": "static_ip",
            "ok": True,
            "message": "Static IP check skipped (not in REAL+live mode).",
            "severity": "ok",
            "outgoing_ip": outgoing_ip,
        })

    # 9. Security ID resolver readiness
    from app.config import settings as _s
    resolver_ok = _s.AUTO_RESOLVE_SECURITY_ID or _s.ALLOW_DEFAULT_SECURITY_ID
    checks.append({
        "name": "security_id_resolver",
        "ok": resolver_ok or True,  # non-blocking; resolver fails per-signal
        "message": (
            "Security ID resolver active (scrip master lookup enabled)."
            if _s.AUTO_RESOLVE_SECURITY_ID
            else "AUTO_RESOLVE_SECURITY_ID=false. securityId must be provided in every signal."
        ),
        "severity": "ok" if _s.AUTO_RESOLVE_SECURITY_ID else "warning",
    })

    # 10. Live-order gate status
    if engine_mode == "live" and settings.ENABLE_LIVE_ORDERS and settings.DHAN_MODE.upper() == "REAL":
        checks.append({
            "name": "live_order_gate",
            "ok": True,
            "message": "LIVE ORDERS ENABLED — real money orders will be placed after risk checks.",
            "severity": "warning",
        })
    else:
        checks.append({
            "name": "live_order_gate",
            "ok": True,
            "message": "Paper mode active. No real Dhan orders will be placed.",
            "severity": "ok",
        })

    return checks


@router.post("/engine/start")
def start_engine(body: StartEngineRequest | None = None) -> dict[str, Any]:
    body = body or StartEngineRequest()
    if body.engine_mode == "paper" and not settings.PAPER_MODE_ENABLED:
        raise HTTPException(status_code=409, detail="Paper mode is disabled on this server.")
    if body.engine_mode is not None:
        try:
            set_engine_mode(body.engine_mode)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    engine_mode = get_engine_mode(legacy_fallback=False)
    if engine_mode is None:
        raise HTTPException(status_code=400, detail={"message": "Select Paper or Live mode before starting the engine."})

    from app.services.shared_market_data import shared_market_data_configured

    # Paper mode on the shared market-data account needs no per-user Dhan token,
    # so the user's (possibly stale) token must not gate paper start.
    paper_uses_shared_data = engine_mode == "paper" and shared_market_data_configured()

    # Gather token age metadata
    token_meta = dhan_token_age_metadata()

    # Hard-block on expired token (not for paper on the shared data feed).
    if not paper_uses_shared_data and token_meta.get("token_expired") is True:
        age_minutes = token_meta.get("token_age_minutes")
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Dhan token is expired (age: {age_minutes} min). Reconnect Dhan via Setup before starting the engine.",
                "token_age": token_meta,
            },
        )

    # Standard readiness check (includes Dhan ping, webhook secret, risk, base URL, etc.)
    readiness = setup_readiness(check_dhan_ping=True)

    # Fetch outgoing IP for readiness display (cached, non-blocking)
    outgoing_ip_result = get_outgoing_ip(timeout=3.0)
    outgoing_ip = outgoing_ip_result.get("outgoing_ip")

    dhan_ping = readiness.get("dhan_ping")
    checks = _build_engine_readiness_checks(readiness, token_meta, outgoing_ip, dhan_ping)

    if not readiness["ready"]:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Engine setup is incomplete.",
                "readiness": readiness,
                "checks": checks,
                "token_age": token_meta,
            },
        )

    if engine_mode == "live" and not body.confirm_live_orders:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "LIVE ORDERS ENABLED — real money orders can be placed. Set confirm_live_orders=true to proceed.",
                "requires_live_order_confirmation": True,
                "checks": checks,
            },
        )

    update_app_state(
        state="WAITING_ENTRY",
        engine_started=True,
        webhook_trading_enabled=True,
        last_message="Engine started. Waiting for TradingView entry alert.",
        last_signal_id=None,
        last_alert_at=None,
        engine_mode=engine_mode,
    )
    log_audit_event(
        "ENGINE_STARTED",
        "Webhook trading enabled.",
        severity="WARNING" if engine_mode == "live" else "INFO",
        metadata={
            "engine_mode": engine_mode,
            "dhan_mode": settings.DHAN_MODE.upper(),
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
            "outgoing_ip": outgoing_ip,
            "token_age_minutes": token_meta.get("token_age_minutes"),
        },
    )
    return {
        "success": True,
        "engine_started": True,
        "app_state": get_app_state(),
        "readiness": readiness,
        "checks": checks,
        "token_age": token_meta,
        "engine_mode": engine_mode,
        "paper_portfolio": get_paper_portfolio().__dict__ if engine_mode == "paper" else None,
    }


@router.post("/engine/stop")
def stop_engine() -> dict[str, Any]:
    update_app_state(
        state="ENGINE_STOPPED",
        engine_started=False,
        webhook_trading_enabled=False,
        last_message="Engine stopped. New entries are blocked.",
    )
    log_audit_event("ENGINE_STOPPED", "Webhook trading disabled. New entries are blocked.", severity="WARNING")
    return {"success": True, "engine_started": False, "app_state": get_app_state()}


@router.post("/engine/reconfigure")
def prepare_reconfigure() -> dict[str, Any]:
    if get_open_position().get("has_open_position"):
        raise HTTPException(status_code=409, detail="Cannot reconfigure with an open position. Stop and square off first.")
    stop_engine()
    log_audit_event("ENGINE_RECONFIGURE_READY", "Flat engine stopped and ready for mode selection.")
    set_engine_mode(None)
    return {"success": True, "engine_started": False, "engine_mode": None, "app_state": get_app_state()}


@router.get("/engine/status")
def engine_status() -> dict[str, Any]:
    app_state = get_app_state()
    token_meta = dhan_token_age_metadata()
    return {
        "engine_started": bool(app_state.get("webhook_trading_enabled")),
        "webhook_trading_enabled": bool(app_state.get("webhook_trading_enabled")),
        "app_state": app_state,
        "token_age": token_meta,
        "setup": setup_status_payload(include_outgoing_ip=False),
        "engine_mode": get_engine_mode(legacy_fallback=False),
    }
