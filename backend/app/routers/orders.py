from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.audit_logger import read_jsonl


router = APIRouter()


@router.get("/orders")
def orders(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
    rows = read_jsonl("order", limit=limit)
    parsed: list[dict] = []
    order_rows = [row for row in rows if row.get("phase") in {"before_request", "after_response", "blocked"}]
    for index, row in enumerate(reversed(order_rows), start=1):
        parsed.append(
            {
                "id": index,
                "created_at": row.get("timestamp"),
                "phase": row.get("phase"),
                "signal_id": row.get("signal_id"),
                "payload_format": row.get("payload_format"),
                "action": row.get("action"),
                "side": row.get("side"),
                "normalized_action": row.get("normalized_action"),
                "normalized_side": row.get("normalized_side"),
                "normalized_qty": row.get("normalized_qty"),
                "normalized_symbol": row.get("normalized_symbol"),
                "normalized_strike": row.get("normalized_strike"),
                "normalized_expiry": row.get("normalized_expiry"),
                "normalized_option_side": row.get("normalized_option_side"),
                "dhan_mode": row.get("dhan_mode"),
                "live_orders_enabled": row.get("live_orders_enabled"),
                "order_id": row.get("order_id"),
                "status": row.get("status"),
                "success": row.get("success"),
                "blocked": row.get("blocked", row.get("phase") == "blocked"),
                "reason": row.get("reason") or row.get("error"),
                "security_id": row.get("security_id"),
                "trading_symbol": row.get("trading_symbol"),
                "qty": row.get("qty"),
                "order_type": row.get("order_type"),
                "product_type": row.get("product_type"),
                "avg_price": row.get("avg_price"),
                "request": row.get("request"),
                "response": row.get("response"),
            }
        )
    return parsed
