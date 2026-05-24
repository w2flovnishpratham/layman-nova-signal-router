from fastapi import APIRouter

from app.services.state_store import get_open_position


router = APIRouter()


@router.get("/positions")
def positions() -> dict:
    return get_open_position()
