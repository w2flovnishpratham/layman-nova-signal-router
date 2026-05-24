from fastapi import APIRouter

from app.config import settings
from app.services.credential_vault import dhan_metadata, get_dhan_credentials
from app.services.dhan_client import MockDhanClient, RealDhanClient
from app.services.wallet_service import refresh_wallet_snapshot


router = APIRouter()


@router.get("/dhan/status")
def dhan_status() -> dict:
    meta = dhan_metadata()
    return {
        "broker": "DHAN",
        "dhan_mode": settings.DHAN_MODE.upper(),
        "client_id_configured": bool(meta["client_id_masked"]),
        "client_id_masked": meta["client_id_masked"],
        "access_token_configured": meta["access_token_present"],
        "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
    }


@router.post("/dhan/test")
def test_dhan() -> dict:
    creds = get_dhan_credentials()
    if settings.DHAN_MODE.upper() == "REAL":
        if not creds:
            return {"success": False, "message": "Dhan Client ID or Access Token missing."}
        result = RealDhanClient().validate_token(
            client_id=creds.client_id,
            access_token=creds.access_token,
        )
        wallet = refresh_wallet_snapshot(force=True, log_event=True) if result.success else None
        message = result.message
        if wallet and wallet.get("success") and wallet.get("available_balance") is not None:
            message = f"{message} Available balance: Rs.{wallet['available_balance']:.2f}."
        elif wallet and not wallet.get("success"):
            message = f"{message} Wallet fetch failed: {wallet.get('message')}"
        return {"success": result.success, "message": message, "wallet": wallet}

    result = MockDhanClient().validate_token(client_id=creds.client_id if creds else "MOCK_CLIENT", access_token="")
    wallet = refresh_wallet_snapshot(force=True, log_event=False)
    return {"success": result.success, "message": result.message, "wallet": wallet}


@router.get("/dhan/funds")
def dhan_funds() -> dict:
    return refresh_wallet_snapshot(force=False, log_event=False)
