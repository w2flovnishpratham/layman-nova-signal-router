from fastapi import APIRouter, Depends

from app.auth.security import require_user_if_auth_enabled
from app.services.position_reconciler import get_reconciled_open_position
from app.services.state_store import get_external_positions


router = APIRouter(dependencies=[Depends(require_user_if_auth_enabled)])


@router.get("/positions")
def positions() -> dict:
    position = get_reconciled_open_position(reason="positions_endpoint")
    return {**position, "external_positions": get_external_positions()}
