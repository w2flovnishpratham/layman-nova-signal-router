"""C2 admin and owner control-plane APIs."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response

from app.auth.dependencies import get_current_user, require_admin
from app.schemas.c2_tradingview import (
    CompileFailurePayload,
    CompileSuccessPayload,
    CreateC2InstallationPayload,
    SuspendC2InstallationPayload,
)
from app.services import c2_tradingview_service as service
from app.services.user_context import CurrentUser


router = APIRouter(tags=["C2 TradingView Installation"])
admin_router = APIRouter(prefix="/api/admin", tags=["C2 TradingView Installation Admin"])


def _error(exc: Exception):
    if isinstance(exc, service.C2Error):
        body = {"ok": False, "error": str(exc)}
        if exc.code:
            body["reason"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=body)
    raise exc


@router.get("/api/strategy-installations/config")
def config(user: CurrentUser = Depends(get_current_user)):
    return {"ok": True, **service.public_config()}


@router.get("/api/strategies/my-installations")
def my_installations(user: CurrentUser = Depends(get_current_user)):
    return {
        "ok": True,
        "installations": service.list_installations(user.id, admin=False),
    }


@router.get("/api/strategies/my-installations/{installation_id}")
def my_installation(
    installation_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return {
            "ok": True,
            "installation": service.get_installation(
                user.id, installation_id, admin=False
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/api/strategies/my-installations/{installation_id}/self-credential")
def self_credential(
    installation_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return {
            "ok": True,
            "credential": service.generate_credential(
                user.id, installation_id, admin=False
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/api/strategies/my-installations/{installation_id}/credential/rotate")
def rotate_self_credential(
    installation_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return {
            "ok": True,
            "credential": service.rotate_credential(
                user.id, installation_id, admin=False
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/api/strategies/my-installations/{installation_id}/credential/revoke")
def revoke_self_credential(
    installation_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return {
            "ok": True,
            "installation": service.revoke_credential(
                user.id, installation_id, admin=False
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.get("/pine-conversions/{conversion_id}/c2")
def conversion_c2(
    conversion_id: uuid.UUID,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {
            "ok": True,
            **service.admin_conversion_status(admin.id, conversion_id),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.get("/pine-conversions/{conversion_id}/approved-pine")
def download_approved_pine(
    conversion_id: uuid.UUID,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        source, filename = service.approved_pine(admin.id, conversion_id)
        return Response(
            content=source.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.post("/pine-conversions/{conversion_id}/compile-success")
def compile_success(
    conversion_id: uuid.UUID,
    payload: CompileSuccessPayload,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {
            "ok": True,
            **service.record_compile_success_for_origin(
                admin.id,
                conversion_id,
                setup_notes=payload.setup_notes,
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.post("/pine-conversions/{conversion_id}/compile-failure")
def compile_failure(
    conversion_id: uuid.UUID,
    payload: CompileFailurePayload,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {
            "ok": True,
            "compile": service.record_compile(
                admin.id,
                conversion_id,
                succeeded=False,
                compiler_error_summary=payload.compiler_error_summary,
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.get("/strategy-installations")
def admin_installations(
    owner_user_id: uuid.UUID | None = None,
    admin: CurrentUser = Depends(require_admin),
):
    return {
        "ok": True,
        "installations": service.list_installations(
            admin.id, admin=True, owner_user_id=owner_user_id
        ),
    }


@admin_router.post("/strategy-installations")
def create_installation(
    payload: CreateC2InstallationPayload,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {
            "ok": True,
            "installation": service.create_installation(
                admin.id,
                payload.conversion_id,
                payload.owner_user_id,
                mode=payload.mode,
                instance_label=payload.instance_label,
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.get("/strategy-installations/{installation_id}")
def admin_installation(
    installation_id: uuid.UUID,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {
            "ok": True,
            "installation": service.get_installation(
                admin.id, installation_id, admin=True
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.post("/strategy-installations/{installation_id}/credential")
def admin_credential(
    installation_id: uuid.UUID,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {
            "ok": True,
            "credential": service.generate_credential(
                admin.id, installation_id, admin=True
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.post("/strategy-installations/{installation_id}/credential/rotate")
def admin_rotate_credential(
    installation_id: uuid.UUID,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {
            "ok": True,
            "credential": service.rotate_credential(
                admin.id, installation_id, admin=True
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.post("/strategy-installations/{installation_id}/credential/revoke")
def admin_revoke_credential(
    installation_id: uuid.UUID,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {
            "ok": True,
            "installation": service.revoke_credential(
                admin.id, installation_id, admin=True
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.post("/strategy-installations/{installation_id}/suspend")
def admin_suspend_installation(
    installation_id: uuid.UUID,
    payload: SuspendC2InstallationPayload,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        return {
            "ok": True,
            "installation": service.suspend_installation(
                admin.id, installation_id, payload.reason
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.post("/strategy-installations/{installation_id}/promote-paper-verification")
def admin_promote_paper_verification(
    installation_id: uuid.UUID,
    admin: CurrentUser = Depends(require_admin),
):
    """Admin-only graduation of a HOLD-verified C2 installation into executable
    Paper Verification. The only transition that lifts the HOLD-only wall."""
    try:
        return {
            "ok": True,
            "installation": service.admin_promote_c2_to_paper_verification(
                admin.id, installation_id
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@admin_router.post("/strategy-installations/{installation_id}/mark-ready")
def admin_mark_ready(
    installation_id: uuid.UUID,
    admin: CurrentUser = Depends(require_admin),
):
    """Admin-only finalization: a verified C2 installation (HOLD + paper entry +
    paper exit observed) becomes READY for normal automated Paper execution."""
    try:
        return {
            "ok": True,
            "installation": service.admin_mark_c2_ready(admin.id, installation_id),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
