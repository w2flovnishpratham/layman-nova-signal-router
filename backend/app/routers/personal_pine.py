from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user, require_admin
from app.schemas.personal_pine import (
    CreatePineStrategyPayload,
    CreatePineVersionPayload,
    LinkPineVersionPayload,
    ReviewNotePayload,
    UpdateStrategyMetadataPayload,
)
from app.schemas.tradingview_setup import UserAcceptancePayload
from app.services import personal_pine_service as service
from app.services.user_context import CurrentUser

router = APIRouter(prefix="/api/personal-pine-strategies", tags=["Personal Pine Strategies"])
admin_router = APIRouter(prefix="/api/admin/pine-reviews", tags=["Pine Admin Review"])
link_router = APIRouter(prefix="/api/personal-strategies", tags=["Personal Pine Strategies"])


def _error(exc: Exception):
    if isinstance(exc, service.PineWorkflowError):
        body = {"ok": False, "error": str(exc)}
        if exc.code:
            body["reason"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=body)
    from app.services.strategy_instance_service import InstanceError
    from app.services.tradingview_setup_service import SetupError
    if isinstance(exc, SetupError):
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc), "reason": exc.code})
    if isinstance(exc, InstanceError):
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc)})
    raise exc


@router.post("")
def create_strategy(payload: CreatePineStrategyPayload, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, **service.create_strategy(
            user.id,
            name=payload.name,
            source=payload.source,
            filename=payload.filename,
            description=payload.description,
        )}
    except Exception as exc:
        return _error(exc)


@router.get("")
def list_strategies(limit: int = 50, offset: int = 0, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, **service.list_strategies(user.id, limit=limit, offset=offset)}
    except Exception as exc:
        return _error(exc)


@router.get("/{strategy_id}")
def get_strategy(strategy_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, **service.get_strategy(user.id, strategy_id)}
    except Exception as exc:
        return _error(exc)


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, **service.delete_strategy(user.id, strategy_id)}
    except Exception as exc:
        return _error(exc)


@router.patch("/{strategy_id}")
def update_strategy_metadata(
    strategy_id: uuid.UUID,
    payload: UpdateStrategyMetadataPayload,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return {"ok": True, **service.update_strategy_metadata(
            user.id, strategy_id, is_admin=False,
            display_name=payload.display_name, description=payload.description,
        )}
    except Exception as exc:
        return _error(exc)


@router.post("/{strategy_id}/force-delete")
def force_delete_strategy(strategy_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    """Permanently delete an owned strategy regardless of approval or
    instance-linked state -- unlike delete_strategy above, which refuses
    once anything has ever been approved/linked/submitted. No undo.
    Blocked only while an instance is actively running.
    """
    try:
        return {"ok": True, **service.force_delete_strategy(user.id, strategy_id, is_admin=False)}
    except Exception as exc:
        return _error(exc)


@router.post("/{strategy_id}/versions")
def create_version(
    strategy_id: uuid.UUID,
    payload: CreatePineVersionPayload,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return {"ok": True, **service.create_version(
            user.id,
            strategy_id,
            source=payload.source,
            filename=payload.filename,
            changelog=payload.changelog,
        )}
    except Exception as exc:
        return _error(exc)


@router.get("/{strategy_id}/versions")
def list_versions(strategy_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        result = service.get_strategy(user.id, strategy_id)
        return {"ok": True, "versions": result["versions"]}
    except Exception as exc:
        return _error(exc)


@router.get("/{strategy_id}/versions/{version_id}")
def get_version(strategy_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, "version": service.get_version(user.id, strategy_id, version_id)}
    except Exception as exc:
        return _error(exc)


@router.post("/{strategy_id}/versions/{version_id}/validate")
def validate_version(strategy_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, **service.validate_version(user.id, strategy_id, version_id)}
    except Exception as exc:
        return _error(exc)


@router.post("/{strategy_id}/versions/{version_id}/submit")
def submit_version(strategy_id: uuid.UUID, version_id: uuid.UUID, payload: UserAcceptancePayload, user: CurrentUser = Depends(get_current_user)):
    try:
        from app.services import tradingview_setup_service
        return {"ok": True, **tradingview_setup_service.accept_and_submit(user.id, strategy_id, version_id, payload)}
    except Exception as exc:
        return _error(exc)


@router.get("/{strategy_id}/versions/{version_id}/source")
def get_source(strategy_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, **service.get_source(user.id, strategy_id, version_id)}
    except Exception as exc:
        return _error(exc)


@router.get("/{strategy_id}/versions/{version_id}/validation")
def get_validation(strategy_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try:
        return {"ok": True, **service.get_validation(user.id, strategy_id, version_id)}
    except Exception as exc:
        return _error(exc)


@link_router.post("/{instance_id}/link-version")
def link_version(
    instance_id: uuid.UUID,
    payload: LinkPineVersionPayload,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return {"ok": True, "link": service.link_version(
            user.id, instance_id, payload.strategy_id, payload.version_id
        )}
    except Exception as exc:
        return _error(exc)


@admin_router.get("")
def list_reviews(limit: int = 50, offset: int = 0, admin: CurrentUser = Depends(require_admin)):
    return {"ok": True, **service.list_reviews(limit=limit, offset=offset)}


@admin_router.get("/{review_id}")
def get_review(review_id: uuid.UUID, admin: CurrentUser = Depends(require_admin)):
    try:
        return {"ok": True, "review": service.get_review(admin.id, review_id)}
    except Exception as exc:
        return _error(exc)


@admin_router.post("/{review_id}/start")
def start_review(
    review_id: uuid.UUID,
    payload: ReviewNotePayload | None = None,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {"ok": True, **service.start_review(admin.id, review_id, note=payload.note if payload else None)}
    except Exception as exc:
        return _error(exc)


def _decision(review_id, decision, payload, admin):
    try:
        return {"ok": True, **service.decide_review(
            admin.id,
            review_id,
            decision,
            note=payload.note if payload else None,
            acknowledge_warnings=payload.acknowledge_warnings if payload else False,
        )}
    except Exception as exc:
        return _error(exc)


@admin_router.post("/{review_id}/approve")
def approve_review(review_id: uuid.UUID, payload: ReviewNotePayload | None = None, admin: CurrentUser = Depends(require_admin)):
    return _decision(review_id, "approved", payload, admin)


@admin_router.post("/{review_id}/request-changes")
def request_changes(review_id: uuid.UUID, payload: ReviewNotePayload | None = None, admin: CurrentUser = Depends(require_admin)):
    return _decision(review_id, "changes_requested", payload, admin)


@admin_router.post("/{review_id}/reject")
def reject_review(review_id: uuid.UUID, payload: ReviewNotePayload | None = None, admin: CurrentUser = Depends(require_admin)):
    return _decision(review_id, "rejected", payload, admin)
