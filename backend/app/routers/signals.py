# ruff: noqa: B008
"""Signals read endpoint - owner-scoped list of inbound webhook signal events."""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, get_execution_scoped_user
from app.db import crud
from app.db.engine import database_configured, session_scope
from app.services import (
    automations_overview,
    credential_vault,
    credentials_overview,
    reports_service,
    risk_configuration,
    risk_overview,
    runtime_reliability,
    signals_feed,
    terminal_feeds,
    user_credential_vault,
    user_preferences,
    webhooks_overview,
)
from app.services.user_context import CurrentUser

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/signals")
def list_signals(
    limit: int = Query(signals_feed.DEFAULT_LIMIT, ge=1, le=signals_feed.MAX_LIMIT),
    cursor: str | None = Query(None),
    status: str = Query("all"),
    since: datetime | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """Page the signed-in owner's signal events, newest first.

    Owner scoping comes from the session only - there is deliberately no
    ``user_id`` parameter, so one owner can never request another's rows.
    """
    result = signals_feed.list_signals(
        user.id, limit=limit, cursor=cursor, status=status, since=since
    )
    if not result.get("ok", True):
        return JSONResponse(status_code=400, content=result)
    return result


@router.get("/webhooks/overview")
def webhooks_overview_endpoint(user: CurrentUser = Depends(get_current_user)):
    """Owner-scoped webhook endpoints, masked secret metadata and delivery stats.

    The raw webhook secret is never returned - only {set, masked, source}.
    """
    return webhooks_overview.build_webhooks_overview(user.id)


@router.post("/webhooks/secret/rotate")
def rotate_webhook_secret(user: CurrentUser = Depends(get_current_user)):
    """Replace the owner's encrypted secret and reveal the new value once."""
    secret = user_credential_vault.generate_webhook_secret()
    if database_configured():
        if not user_credential_vault.vault_ready():
            return JSONResponse(
                status_code=503,
                content={"ok": False, "error": "Credential storage is not configured."},
            )
        try:
            user_credential_vault.save_user_credentials(user.id, webhook_secret=secret)
        except user_credential_vault.UserVaultError:
            return JSONResponse(
                status_code=503,
                content={"ok": False, "error": "Could not rotate the webhook secret."},
            )
        if not user.is_dev:
            try:
                with session_scope() as db:
                    crud.add_audit_log(db, user_id=user.id, action="WEBHOOK_SECRET_ROTATED", metadata={})
            except Exception:
                logger.exception("Could not audit webhook secret rotation.")
    elif user.is_dev:
        if credential_vault.webhook_secret_metadata().get("source") == "environment":
            return JSONResponse(
                status_code=409,
                content={"ok": False, "error": "This secret is managed by the server environment."},
            )
        try:
            credential_vault.save_webhook_secret(secret)
        except credential_vault.VaultError:
            return JSONResponse(
                status_code=503,
                content={"ok": False, "error": "Could not rotate the webhook secret."},
            )
    else:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "Credential storage is not configured."},
        )
    return JSONResponse(
        content={"ok": True, "secret": secret},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/risk/overview")
def risk_overview_endpoint(
    mode: str = Query("paper", pattern="^(paper|live)$"),
    user: CurrentUser = Depends(get_current_user),
):
    """Owner-scoped strategy fan-out risk limits and today's IST usage.

    Limits of 0 mean "no limit"; utilisation is then undefined rather than 0%.
    """
    return risk_overview.build_risk_overview(user.id, mode)


class RiskConfigurationPayload(BaseModel):
    mode: str = Field(pattern="^(?i:paper|live)$")
    basedOnPreset: str = Field(pattern="^(?i:conservative|balanced|aggressive)$")
    values: dict[str, Any]
    changeSource: str = Field(pattern="^(SETUP|RISK_PAGE|TRADING_TERMINAL|ADMIN|SYSTEM_MIGRATION)$")
    expectedVersion: int = Field(ge=0)


class RiskPresetPayload(BaseModel):
    mode: str = Field(pattern="^(?i:paper|live)$")
    preset: str = Field(pattern="^(?i:conservative|balanced|aggressive)$")
    changeSource: str = Field(pattern="^(SETUP|RISK_PAGE|TRADING_TERMINAL|ADMIN|SYSTEM_MIGRATION)$")
    expectedVersion: int = Field(ge=0)


class RiskKillPayload(BaseModel):
    mode: str = Field(pattern="^(?i:paper|live)$")


@router.get("/risk/presets")
def risk_presets_endpoint(
    mode: str = Query("paper", pattern="^(paper|live)$"),
    user: CurrentUser = Depends(get_current_user),
):
    del user
    return {"ok": True, "mode": mode.upper(), "presets": risk_configuration.presets(mode)}


@router.get("/risk/configuration")
def risk_configuration_endpoint(
    mode: str = Query("paper", pattern="^(paper|live)$"),
    user: CurrentUser = Depends(get_current_user),
):
    return {"ok": True, **risk_configuration.configuration(user.id, mode)}


def _risk_configuration_error(exc: risk_configuration.RiskConfigurationError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": exc.message, "code": exc.code},
    )


@router.put("/risk/configuration")
def save_risk_configuration(
    body: RiskConfigurationPayload,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return {
            "ok": True,
            **risk_configuration.save_configuration(
                user.id,
                mode=body.mode,
                based_on_preset=body.basedOnPreset,
                values=body.values,
                change_source=body.changeSource,
                expected_version=body.expectedVersion,
            ),
        }
    except risk_configuration.RiskConfigurationError as exc:
        return _risk_configuration_error(exc)


@router.post("/risk/configuration/apply-preset")
def apply_risk_preset(
    body: RiskPresetPayload,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return {
            "ok": True,
            **risk_configuration.save_configuration(
                user.id,
                mode=body.mode,
                based_on_preset=body.preset,
                values=risk_configuration.preset_values(body.preset),
                change_source=body.changeSource,
                expected_version=body.expectedVersion,
            ),
        }
    except risk_configuration.RiskConfigurationError as exc:
        return _risk_configuration_error(exc)


@router.post("/risk/kill-switch")
def risk_kill_switch(
    body: RiskKillPayload,
    user: CurrentUser = Depends(get_current_user),
):
    """Immediate owner-scoped stop; only the selected running mode is squared off."""
    mode = body.mode.lower()
    from app.services.state_store import get_engine_mode

    if get_engine_mode(legacy_fallback=False) != mode:
        return {"ok": True, "mode": mode.upper(), "outcome": "No running engine in this mode."}
    result = runtime_reliability.square_off(user)
    if database_configured():
        from app.db import models
        from app.db.engine import session_scope

        with session_scope() as db:
            db.add(
                models.AuditLog(
                    user_id=user.id,
                    action="RISK_KILL_SWITCH",
                    audit_metadata={
                        "mode": mode,
                        "message": "Manual kill switch stopped entries and squared off tracked positions.",
                        "trigger_type": "MANUAL",
                        "outcome": "Squared off",
                    },
                )
            )
    return {"ok": True, "mode": mode.upper(), "outcome": "Squared off", "runtime": result}


@router.get("/credentials/overview")
def credentials_overview_endpoint(user: CurrentUser = Depends(get_current_user)):
    """Owner-scoped broker credential status. Never returns a stored secret."""
    return credentials_overview.build_credentials_overview(user.id)


def _period(start: date | None, end: date | None) -> tuple[date, date]:
    """Default to the last 30 IST days when no range is given."""
    today = datetime.now(reports_service.IST).date()
    resolved_end = end or today
    resolved_start = start or (resolved_end - timedelta(days=29))
    return resolved_start, resolved_end


@router.get("/reports")
def reports_endpoint(
    start: date | None = Query(None),
    end: date | None = Query(None),
    mode: str = Query("paper", pattern="^(paper|live)$"),
    trade_origin: str | None = Query(None, pattern="^(all|automated|manual)$"),
    user: CurrentUser = Depends(get_current_user),
):
    """Performance over a date range, computed from the owner's closed trades."""
    begin, finish = _period(start, end)
    if begin > finish:
        return JSONResponse(status_code=400, content={"ok": False, "error": "start must not be after end."})
    return reports_service.build_report(user.id, start=begin, end=finish, mode=mode, trade_origin=trade_origin)


@router.get("/reports/export.csv")
def reports_csv_endpoint(
    start: date | None = Query(None),
    end: date | None = Query(None),
    mode: str = Query("paper", pattern="^(paper|live)$"),
    trade_origin: str | None = Query(None, pattern="^(all|automated|manual)$"),
    user: CurrentUser = Depends(get_current_user),
):
    begin, finish = _period(start, end)
    if begin > finish:
        return JSONResponse(status_code=400, content={"ok": False, "error": "start must not be after end."})
    report = reports_service.build_report(user.id, start=begin, end=finish, mode=mode, trade_origin=trade_origin)
    filename = f"nova-report-{mode}-{begin.strftime('%Y-%m')}.csv"
    return Response(
        content=reports_service.report_csv(report),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/export.pdf")
def reports_pdf_endpoint(
    start: date | None = Query(None),
    end: date | None = Query(None),
    mode: str = Query("paper", pattern="^(paper|live)$"),
    trade_origin: str | None = Query(None, pattern="^(all|automated|manual)$"),
    user: CurrentUser = Depends(get_current_user),
):
    begin, finish = _period(start, end)
    if begin > finish:
        return JSONResponse(status_code=400, content={"ok": False, "error": "start must not be after end."})
    report = reports_service.build_report(user.id, start=begin, end=finish, mode=mode, trade_origin=trade_origin)
    filename = f"nova-report-{mode}-{begin.strftime('%Y-%m')}.pdf"
    return Response(
        content=reports_service.report_pdf(report),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class PreferencesPayload(BaseModel):
    timezone: str | None = None
    reduced_motion: bool | None = None
    table_density: str | None = None
    default_chart_timeframe: str | None = None
    notification_preferences: dict[str, bool] | None = None


@router.get("/preferences")
def read_preferences(user: CurrentUser = Depends(get_current_user)):
    return {"ok": True, **user_preferences.get_preferences(user.id)}


@router.put("/preferences")
def write_preferences(payload: PreferencesPayload, user: CurrentUser = Depends(get_current_user)):
    values = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    try:
        return {"ok": True, **user_preferences.save_preferences(user.id, values)}
    except user_preferences.PreferenceError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})


class AutomationsPayload(BaseModel):
    """One reviewed batch against one exact selected configuration revision."""

    configuration_id: uuid.UUID
    expected_revision: int
    changes: dict[str, Any]


class AlertAcknowledgementPayload(BaseModel):
    event_ids: list[str]


@router.get("/trading/activity")
def trading_activity(
    mode: str | None = Query(None, pattern="^(paper|live)$"),
    run_id: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200),
    user: CurrentUser = Depends(get_execution_scoped_user),
):
    return terminal_feeds.activity(
        user.id,
        mode=mode,
        run_id=run_id,
        cursor=cursor,
        limit=limit,
    )


@router.get("/trading/engine-log")
def trading_engine_log(
    mode: str | None = Query(None, pattern="^(paper|live)$"),
    run_id: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200),
    user: CurrentUser = Depends(get_execution_scoped_user),
):
    return terminal_feeds.engine_log(
        user.id,
        mode=mode,
        run_id=run_id,
        cursor=cursor,
        limit=limit,
    )


@router.get("/trading/executions")
def trading_executions(
    mode: str | None = Query(None, pattern="^(paper|live)$"),
    run_id: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200),
    user: CurrentUser = Depends(get_execution_scoped_user),
):
    return terminal_feeds.executions(
        user.id,
        mode=mode,
        run_id=run_id,
        cursor=cursor,
        limit=limit,
    )


@router.get("/trading/alerts")
def trading_alerts(
    mode: str | None = Query(None, pattern="^(paper|live)$"),
    run_id: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200),
    user: CurrentUser = Depends(get_execution_scoped_user),
):
    return terminal_feeds.alerts(
        user.id,
        runtime=runtime_reliability.runtime_status(user),
        mode=mode,
        run_id=run_id,
        cursor=cursor,
        limit=limit,
    )


@router.post("/trading/alerts/acknowledge-visible")
def acknowledge_visible_alerts(
    payload: AlertAcknowledgementPayload,
    user: CurrentUser = Depends(get_execution_scoped_user),
):
    runtime = runtime_reliability.runtime_status(user)
    try:
        acknowledged = terminal_feeds.acknowledge(
            user.id,
            payload.event_ids,
            runtime=runtime,
        )
    except terminal_feeds.ActiveAlertAcknowledgementError as exc:
        return JSONResponse(
            status_code=409,
            content={"ok": False, "error": str(exc), "acknowledged": 0},
        )
    return {"ok": True, "acknowledged": acknowledged}


@router.get("/automations")
def read_automations(user: CurrentUser = Depends(get_current_user)):
    """Editable risk controls plus the protected policies, which have no toggle."""
    return automations_overview.build_automations_overview(user.id)


@router.put("/automations")
def write_automations(payload: AutomationsPayload, user: CurrentUser = Depends(get_current_user)):
    try:
        clean = automations_overview.validate_automation_changes(payload.changes)
    except automations_overview.AutomationError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    if not clean:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": "No automation changes were supplied."},
        )
    from app.services import setup_configuration

    try:
        revision = setup_configuration.revise_automation_configuration(
            user.id,
            configuration_id=payload.configuration_id,
            expected_revision=payload.expected_revision,
            changes=clean,
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
    return {
        **automations_overview.build_automations_overview(user.id),
        "confirmed_revision": revision,
    }
