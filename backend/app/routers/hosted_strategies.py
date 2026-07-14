from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user, require_admin
from app.config import settings
from app.schemas.hosted_strategy import CreateIRRequest, HostedLinkRequest, ReplayRequest
from app.services import hosted_strategy_runtime as hosted
from app.services.hosted_strategy_engine import validate_ir_document
from app.services.user_context import CurrentUser

router = APIRouter(tags=["Hosted Strategies"])
admin_router = APIRouter(prefix="/api/admin/hosted-strategies", tags=["Hosted Strategies (admin)"])


def _error(exc: Exception):
    if isinstance(exc, hosted.HostedStrategyError):
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc), "reason": exc.code})
    if isinstance(exc, ValueError): return JSONResponse(status_code=422, content={"ok": False, "error": str(exc), "reason": "INVALID_REQUEST"})
    raise exc


@router.get("/api/hosted-strategies/config")
def config(user: CurrentUser = Depends(get_current_user)):
    return {"ok": True, "runtime_enabled": settings.HOSTED_STRATEGY_RUNTIME_ENABLED, "paper_execution_enabled": settings.HOSTED_STRATEGY_PAPER_EXECUTION_ENABLED, "paper_only": True}


@router.get("/api/hosted-strategies/ir")
def list_ir(limit: int = 50, offset: int = 0, user: CurrentUser = Depends(get_current_user)):
    return {"ok": True, **hosted.list_ir(user.id, limit=limit, offset=offset)}


@router.get("/api/hosted-strategies/ir/{ir_version_id}")
def get_ir(ir_version_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, "ir_version": hosted.get_ir(user.id, ir_version_id)}
    except Exception as exc: return _error(exc)


@router.post("/api/hosted-strategies/ir/{ir_version_id}/validate")
def validate_existing(ir_version_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        item = hosted.get_ir(user.id, ir_version_id); _, report = validate_ir_document(item["ir"])
        return {"ok": True, "report": report, "mutated": False}
    except Exception as exc: return _error(exc)


@router.post("/api/hosted-strategies/ir/{ir_version_id}/replay")
def replay(ir_version_id: uuid.UUID, payload: ReplayRequest, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, **hosted.replay_ir(user.id, ir_version_id, payload.candles, payload.starting_position)}
    except Exception as exc: return _error(exc)


@router.post("/api/strategy-instances/{instance_id}/hosted-link")
def link(instance_id: uuid.UUID, payload: HostedLinkRequest, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, "runtime": hosted.link_runtime(user.id, instance_id, uuid.UUID(payload.ir_version_id))}
    except Exception as exc: return _error(exc)


@router.get("/api/strategy-instances/{instance_id}/hosted-runtime")
def runtime(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, "runtime": hosted.get_runtime(user.id, instance_id)}
    except Exception as exc: return _error(exc)


def _lifecycle(action, instance_id, user):
    try: return {"ok": True, "runtime": action(user.id, instance_id)}
    except Exception as exc: return _error(exc)


@router.post("/api/strategy-instances/{instance_id}/hosted-activate")
def activate(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)): return _lifecycle(hosted.activate, instance_id, user)


@router.post("/api/strategy-instances/{instance_id}/hosted-pause")
def pause(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)): return _lifecycle(hosted.pause, instance_id, user)


@router.post("/api/strategy-instances/{instance_id}/hosted-resume")
def resume(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)): return _lifecycle(hosted.resume, instance_id, user)


@router.post("/api/strategy-instances/{instance_id}/hosted-stop")
def stop(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)): return _lifecycle(hosted.stop, instance_id, user)


@router.get("/api/strategy-instances/{instance_id}/hosted-evaluations")
def evaluations(instance_id: uuid.UUID, limit: int = 50, offset: int = 0, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, **hosted.list_evaluations(user.id, instance_id, limit=limit, offset=offset)}
    except Exception as exc: return _error(exc)


@router.post("/api/strategy-instances/{instance_id}/hosted-replay")
def replay_latest(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, **hosted.replay_latest(user.id, instance_id)}
    except Exception as exc: return _error(exc)


@admin_router.post("/ir")
def admin_create_ir(payload: CreateIRRequest, admin: CurrentUser = Depends(require_admin)):
    try: return {"ok": True, "ir_version": hosted.create_ir(strategy_id=uuid.UUID(payload.strategy_id), document=payload.ir.model_dump(mode="json"), creation_source=payload.creation_source, admin_user_id=admin.id)}
    except Exception as exc: return _error(exc)


@admin_router.post("/ir/{ir_version_id}/approve")
def admin_approve_ir(ir_version_id: uuid.UUID, admin: CurrentUser = Depends(require_admin)):
    try: return {"ok": True, "ir_version": hosted.approve_ir(ir_version_id, admin.id)}
    except Exception as exc: return _error(exc)


@admin_router.get("/ir")
def admin_list_ir(limit: int = 50, offset: int = 0, admin: CurrentUser = Depends(require_admin)):
    return {"ok": True, **hosted.admin_list_ir(limit=limit, offset=offset)}


@admin_router.get("/health")
def admin_health(admin: CurrentUser = Depends(require_admin)):
    return {"ok": True, **hosted.admin_health()}


@admin_router.post("/runtimes/{runtime_id}/pause")
def admin_pause(runtime_id: uuid.UUID, admin: CurrentUser = Depends(require_admin)):
    try: return {"ok": True, "runtime": hosted.admin_pause(runtime_id)}
    except Exception as exc: return _error(exc)
