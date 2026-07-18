from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user, require_admin
from app.schemas.pine_conversion import (
    AdminManualResponsePayload,
    AdminPineDecisionPayload,
    AdminPineSubmission,
    CreateConversionPayload,
    RejectConversionPayload,
    RetryConversionPayload,
)
from app.services import admin_pine_conversion_service as admin_service
from app.services import pine_conversion_service as service
from app.services.user_context import CurrentUser
from app.workers.pine_conversion_worker import wake_pine_conversion_worker

router = APIRouter(tags=["Pine Conversion"])
admin_router = APIRouter(prefix="/api/admin/pine-conversions", tags=["Pine Conversion Admin"])


def _error(exc):
    if isinstance(exc, admin_service.AdminConversionError):
        body = {"ok": False, "error": str(exc)}
        if exc.code:
            body["reason"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=body)
    if isinstance(exc, service.ConversionError):
        body = {"ok": False, "error": str(exc)}
        if exc.code: body["reason"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=body)
    from app.services.personal_pine_service import PineWorkflowError
    if isinstance(exc, PineWorkflowError):
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc), "reason": exc.code})
    raise exc


@router.get("/api/pine-conversions/config")
def config(user: CurrentUser = Depends(get_current_user)):
    return {"ok": True, **service.public_config()}


@router.post("/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/conversion-package")
def conversion_package(strategy_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, **service.manual_package(user.id, strategy_id, version_id)}
    except Exception as exc: return _error(exc)


@router.post("/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/convert", status_code=202)
def convert(strategy_id: uuid.UUID, version_id: uuid.UUID, payload: CreateConversionPayload, user: CurrentUser = Depends(get_current_user)):
    try:
        result = service.create_request(user.id, strategy_id, version_id, payload.options)
        wake_pine_conversion_worker()
        return {"ok": True, **result}
    except Exception as exc: return _error(exc)


@router.get("/api/pine-conversions")
def list_conversions(limit: int = 50, offset: int = 0, user: CurrentUser = Depends(get_current_user)):
    return {"ok": True, **service.list_requests(user.id, limit=limit, offset=offset)}


@router.get("/api/pine-conversions/{conversion_id}")
def get_conversion(conversion_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, **service.get_request(user.id, conversion_id)}
    except Exception as exc: return _error(exc)


@router.post("/api/pine-conversions/{conversion_id}/cancel")
def cancel(conversion_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, **service.cancel(user.id, conversion_id)}
    except Exception as exc: return _error(exc)


@router.post("/api/pine-conversions/{conversion_id}/retry", status_code=202)
def retry(conversion_id: uuid.UUID, payload: RetryConversionPayload, user: CurrentUser = Depends(get_current_user)):
    try:
        result = service.retry(user.id, conversion_id); wake_pine_conversion_worker(); return {"ok": True, **result}
    except Exception as exc: return _error(exc)


@router.post("/api/pine-conversions/{conversion_id}/accept")
def accept(conversion_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, **service.accept(user.id, conversion_id)}
    except Exception as exc: return _error(exc)


@router.post("/api/pine-conversions/{conversion_id}/reject")
def reject(conversion_id: uuid.UUID, payload: RejectConversionPayload | None = None, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, **service.reject(user.id, conversion_id, payload.reason if payload else None)}
    except Exception as exc: return _error(exc)


@admin_router.get("/usage")
def usage(admin: CurrentUser = Depends(require_admin)):
    from sqlalchemy import func, select
    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db:
        rows = db.execute(select(models.PineConversionRequest.provider, models.PineConversionRequest.model, models.PineConversionRequest.status, func.count()).group_by(models.PineConversionRequest.provider, models.PineConversionRequest.model, models.PineConversionRequest.status)).all()
        return {"ok": True, "usage": [{"provider": p, "model": m, "status": s, "requests": count} for p, m, s, count in rows]}


@admin_router.post("")
def admin_submit(payload: AdminPineSubmission, admin: CurrentUser = Depends(require_admin)):
    try:
        return {"ok": True, **admin_service.submit(admin.id, payload)}
    except Exception as exc:
        return _error(exc)


@admin_router.get("")
def admin_list(limit: int = 50, offset: int = 0, admin: CurrentUser = Depends(require_admin)):
    try:
        return {"ok": True, **admin_service.list_conversions(admin.id, limit=limit, offset=offset)}
    except Exception as exc:
        return _error(exc)


@admin_router.get("/{conversion_id}")
def admin_detail(conversion_id: uuid.UUID, admin: CurrentUser = Depends(require_admin)):
    try:
        return {"ok": True, **admin_service.get_conversion(admin.id, conversion_id)}
    except Exception as exc:
        return _error(exc)


@admin_router.post("/{conversion_id}/convert")
def admin_convert(conversion_id: uuid.UUID, admin: CurrentUser = Depends(require_admin)):
    try:
        return {"ok": True, **admin_service.convert(admin.id, conversion_id)}
    except Exception as exc:
        return _error(exc)


@admin_router.post("/{conversion_id}/manual-package")
def admin_manual_package(conversion_id: uuid.UUID, admin: CurrentUser = Depends(require_admin)):
    try:
        return {"ok": True, **admin_service.manual_package(admin.id, conversion_id)}
    except Exception as exc:
        return _error(exc)


@admin_router.post("/{conversion_id}/manual-response")
def admin_manual_response(
    conversion_id: uuid.UUID,
    payload: AdminManualResponsePayload,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {"ok": True, **admin_service.submit_manual_response(admin.id, conversion_id, payload.response_json)}
    except Exception as exc:
        return _error(exc)


@admin_router.post("/{conversion_id}/approve")
def admin_approve(
    conversion_id: uuid.UUID,
    payload: AdminPineDecisionPayload | None = None,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {"ok": True, **admin_service.approve(admin.id, conversion_id, payload.reason if payload else None)}
    except Exception as exc:
        return _error(exc)


@admin_router.post("/{conversion_id}/reject")
def admin_reject(
    conversion_id: uuid.UUID,
    payload: AdminPineDecisionPayload,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {"ok": True, **admin_service.reject(admin.id, conversion_id, payload.reason)}
    except Exception as exc:
        return _error(exc)
