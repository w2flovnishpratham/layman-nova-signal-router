from fastapi import APIRouter

from app.services.position_reconciler import get_reconciled_open_position
from app.services.state_store import get_external_positions


router = APIRouter()


@router.get("/positions")
def positions() -> dict:
    position = get_reconciled_open_position(reason="positions_endpoint")
    return {**position, "external_positions": get_external_positions()}
