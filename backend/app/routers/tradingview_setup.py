from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user, require_admin
from app.schemas.tradingview_setup import (
    CompilationPayload, CreateSetupPayload, InstallationPayload, PromptDecisionPayload,
    QualificationTrialPayload, ResetSetupPayload, SetupStatePayload,
    VerificationPayload,
)
from app.services import tradingview_setup_service as service
from app.services.user_context import CurrentUser

router = APIRouter(tags=["TradingView Setup"])
admin_router = APIRouter(prefix="/api/admin", tags=["TradingView Setup Admin"])


def _error(exc: Exception):
    if isinstance(exc, service.SetupError):
        body = {"ok": False, "error": str(exc)}
        if exc.code: body["reason"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=body)
    from app.services.strategy_instance_service import InstanceError
    if isinstance(exc, InstanceError):
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc), "reason": exc.code})
    raise exc


@router.get("/api/pine-prompts/current")
def current_prompt(user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, "prompt": service.prompt_status()}
    except Exception as exc: return _error(exc)


@router.post("/api/strategy-instances/{instance_id}/tradingview-setup")
def create_setup(instance_id: uuid.UUID, payload: CreateSetupPayload, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, "setup": service.create_setup(user.id, instance_id, payload.setup_type, payload.requested_timeframe)}
    except Exception as exc: return _error(exc)


@router.get("/api/strategy-instances/{instance_id}/tradingview-setup")
def get_setup(instance_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, "setup": service.get_setup(user.id, instance_id)}
    except Exception as exc: return _error(exc)


@router.post("/api/strategy-instances/{instance_id}/tradingview-setup/reset")
def reset_setup(instance_id: uuid.UUID, payload: ResetSetupPayload, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, "setup": service.reset_setup(user.id, instance_id, payload.setup_type, payload.requested_timeframe, payload.reason)}
    except Exception as exc: return _error(exc)


@router.post("/api/strategy-instances/{instance_id}/tradingview-setup/compiled")
def report_compiled(instance_id: uuid.UUID, payload: CompilationPayload, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, "setup": service.report_compiled(user.id, instance_id)}
    except Exception as exc: return _error(exc)


@router.post("/api/strategy-instances/{instance_id}/tradingview-setup/verification")
def record_verification(instance_id: uuid.UUID, payload: VerificationPayload, user: CurrentUser = Depends(get_current_user)):
    try: return {"ok": True, "setup": service.record_verification(user.id, instance_id, payload.kind, payload.signal_id)}
    except Exception as exc: return _error(exc)


@admin_router.get("/managed-tradingview-setups")
def managed_queue(limit: int = 50, offset: int = 0, admin: CurrentUser = Depends(require_admin)):
    return {"ok": True, **service.list_managed(limit=limit, offset=offset)}


@admin_router.post("/managed-tradingview-setups/{setup_id}/installation")
def record_installation(setup_id: uuid.UUID, payload: InstallationPayload, admin: CurrentUser = Depends(require_admin)):
    try: return {"ok": True, "setup": service.record_installation(admin.id, setup_id, payload.model_dump())}
    except Exception as exc: return _error(exc)


@admin_router.post("/managed-tradingview-setups/{setup_id}/state")
def set_state(setup_id: uuid.UUID, payload: SetupStatePayload, admin: CurrentUser = Depends(require_admin)):
    try: return {"ok": True, "setup": service.admin_set_state(admin.id, setup_id, payload.status, payload.reason, payload.admin_notes)}
    except Exception as exc: return _error(exc)


@admin_router.post("/managed-tradingview-setups/{setup_id}/credential")
def issue_managed_credential(setup_id: uuid.UUID, rotate: bool = False, admin: CurrentUser = Depends(require_admin)):
    """Admin-only: provision the private credential for a NOVA-managed setup.
    Plaintext is returned exactly once here and never in GET/list responses."""
    from app.services import strategy_instance_service as instances
    try:
        return {"ok": True, "credential": instances.admin_generate_managed_credential(admin.id, setup_id, rotate=rotate)}
    except Exception as exc:
        return _error(exc)


@admin_router.post("/managed-tradingview-setups/{setup_id}/verification-mode")
def start_managed_verification(setup_id: uuid.UUID, admin: CurrentUser = Depends(require_admin)):
    """Admin-only: start controlled paper verification for a NOVA-managed setup
    on the owner's behalf. The non-Premium user never handles this step."""
    from app.services import strategy_instance_service as instances
    try:
        return {"ok": True, "instance": instances.admin_start_managed_verification(admin.id, setup_id)}
    except Exception as exc:
        return _error(exc)


@admin_router.post("/strategy-instances/{instance_id}/tradingview-verification")
def admin_verification(instance_id: uuid.UUID, payload: VerificationPayload, admin: CurrentUser = Depends(require_admin)):
    try: return {"ok": True, "setup": service.record_verification(admin.id, instance_id, payload.kind, payload.signal_id, admin=True)}
    except Exception as exc: return _error(exc)


@admin_router.get("/pine-prompts/{prompt_id}/qualification-trials")
def list_trials(prompt_id: str, limit: int = 100, offset: int = 0, admin: CurrentUser = Depends(require_admin)):
    return {"ok": True, **service.list_trials(prompt_id, limit=limit, offset=offset)}


@admin_router.post("/pine-prompts/qualification-trials")
def create_trial(payload: QualificationTrialPayload, admin: CurrentUser = Depends(require_admin)):
    try: return {"ok": True, "trial": service.create_trial(admin.id, payload)}
    except Exception as exc: return _error(exc)


@admin_router.post("/pine-prompts/{prompt_id}/approve")
def approve_prompt(prompt_id: str, payload: PromptDecisionPayload, admin: CurrentUser = Depends(require_admin)):
    try: return {"ok": True, "prompt": service.approve_prompt(admin.id, prompt_id, payload.change_notes)}
    except Exception as exc: return _error(exc)
