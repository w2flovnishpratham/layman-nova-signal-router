from fastapi import APIRouter

from app.services.position_reconciler import get_reconciled_open_position


router = APIRouter()


@router.get("/positions")
def positions() -> dict:
    return get_reconciled_open_position(reason="positions_endpoint")
