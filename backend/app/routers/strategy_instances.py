"""Owner-scoped strategy-instance control API (Phase 1, non-executing).

All identity comes from the authenticated session; instance ids in the path
are validated against ownership inside the service. Admin listing is a
separate, explicitly admin-gated endpoint.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user, require_admin
from app.schemas.strategy import (
    CloneInstancePayload,
    CreateInstancePayload,
    SelectStrategyPayload,
    StatusReasonPayload,
    UpdateLotsPayload,
)
from app.services import strategy_instance_service as instances
from app.services.entitlements import EntitlementError
from app.services.user_context import CurrentUser

router = APIRouter(prefix="/api/strategy-instances", tags=["Strategy Instances"])


def _error(exc: Exception) -> JSONResponse:
    from app.services.c2_tradingview_service import C2Error

    if isinstance(exc, C2Error):
        content = {"ok": False, "error": str(exc)}
        if exc.code:
            content["reason"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content)
    if isinstance(exc, instances.InstanceError):
        content = {"ok": False, "error": str(exc)}
        if exc.code:
            content["reason"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content)
    if isinstance(exc, EntitlementError):
        return JSONResponse(status_code=403, content={"ok": False, "error": str(exc) or "Entitlement required."})
    if isinstance(exc, ValueError):
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    raise exc


@router.get("")
def list_instances(
    include_archived: bool = False,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    return {"ok": True, "instances": instances.list_instances(user.id, include_archived=include_archived)}


@router.post("")
def create_instance(payload: CreateInstancePayload, user: CurrentUser = Depends(get_current_user)):
    try:
        instance = instances.create_instance(
            user.id,
            strategy_code=payload.strategy_code,
            source_journey=payload.source_journey,
            label=payload.label,
            lots=payload.lots,
            execution_mode=payload.execution_mode,
        )
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "instance": instance}


@router.get("/{instance_id}")
def get_instance(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, "instance": instances.get_instance(user.id, instance_id)}
    except Exception as exc:
        return _error(exc)


@router.post("/{instance_id}/clone")
def clone_instance(
    instance_id: uuid.UUID,
    payload: CloneInstancePayload | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        instance = instances.clone_instance(user.id, instance_id, label=payload.label if payload else None)
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "instance": instance}


@router.post("/{instance_id}/lots")
def update_lots(instance_id: uuid.UUID, payload: UpdateLotsPayload, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, "instance": instances.update_lots(user.id, instance_id, payload.lots)}
    except Exception as exc:
        return _error(exc)


def _lifecycle(action, instance_id, user, **kwargs):
    try:
        return {"ok": True, "instance": action(user.id, instance_id, **kwargs)}
    except Exception as exc:
        return _error(exc)


@router.post("/{instance_id}/activate")
def activate_instance(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    return _lifecycle(instances.activate_instance, instance_id, user)


@router.post("/{instance_id}/pause")
def pause_instance(
    instance_id: uuid.UUID,
    payload: StatusReasonPayload | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    return _lifecycle(instances.pause_instance, instance_id, user, reason=payload.reason if payload else None)


@router.post("/{instance_id}/resume")
def resume_instance(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    return _lifecycle(instances.resume_instance, instance_id, user)


@router.post("/{instance_id}/stop")
def stop_instance(
    instance_id: uuid.UUID,
    payload: StatusReasonPayload | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    return _lifecycle(instances.stop_instance, instance_id, user, reason=payload.reason if payload else None)


@router.post("/{instance_id}/verification-mode")
def start_verification(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    """Premium owner starts controlled paper-only verification for their strategy."""
    try:
        return {"ok": True, "instance": instances.start_verification(user.id, instance_id)}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/{instance_id}/archive")
def archive_instance(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    return _lifecycle(instances.archive_instance, instance_id, user)


@router.get("/{instance_id}/webhook-executions")
def list_webhook_executions(
    instance_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    user: CurrentUser = Depends(get_current_user),
):
    """Owner-scoped, paginated private-webhook execution history (Phase 3A)."""
    from app.services import private_webhook_service

    try:
        return {
            "ok": True,
            **private_webhook_service.list_webhook_executions(
                user.id, instance_id, limit=limit, offset=offset
            ),
        }
    except Exception as exc:
        return _error(exc)


@router.post("/{instance_id}/webhook-test")
def test_webhook_connection(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    """Authenticated, owner-scoped, paper-only HOLD connectivity test."""
    from app.services import private_webhook_service

    try:
        return {"ok": True, **private_webhook_service.test_connection(user.id, instance_id)}
    except Exception as exc:
        if isinstance(exc, private_webhook_service.PrivateWebhookError):
            return JSONResponse(
                status_code=exc.status_code,
                content={"ok": False, "error": str(exc), "reason": exc.reason},
            )
        return _error(exc)


@router.post("/{instance_id}/webhook-credential")
def generate_webhook_credential(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, "credential": instances.generate_webhook_credential(user.id, instance_id)}
    except Exception as exc:
        return _error(exc)


@router.post("/{instance_id}/webhook-credential/rotate")
def rotate_webhook_credential(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, "credential": instances.rotate_webhook_credential(user.id, instance_id)}
    except Exception as exc:
        return _error(exc)


@router.delete("/{instance_id}/webhook-credential")
def revoke_webhook_credential(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, "credential": instances.revoke_webhook_credential(user.id, instance_id)}
    except Exception as exc:
        return _error(exc)


admin_router = APIRouter(prefix="/api/admin/strategy-instances", tags=["Strategy Instances (admin)"])


@admin_router.get("")
def admin_list_instances(
    user_id: uuid.UUID | None = None,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    return {"ok": True, "instances": instances.admin_list_instances(user_id=user_id)}


engine_router = APIRouter(prefix="/api/engine", tags=["Engine Strategy Picker"])


@engine_router.get("/strategies")
def engine_strategies(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Owner-scoped engine picker: eligible strategies for this user only."""
    return {"ok": True, **instances.list_engine_strategies(user.id)}


@engine_router.get("/selection")
def get_engine_selection(user: CurrentUser = Depends(get_current_user)) -> dict:
    """The user's persisted engine strategy selection."""
    return {"ok": True, **instances.get_engine_selection(user.id)}


@engine_router.put("/selection")
def set_engine_selection(payload: SelectStrategyPayload, user: CurrentUser = Depends(get_current_user)):
    """Persist the selected strategy instance (owner-scoped, eligible only)."""
    try:
        return {"ok": True, **instances.set_engine_selection(user.id, payload.strategy_instance_id)}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
