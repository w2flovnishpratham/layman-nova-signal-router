from typing import Any

from pydantic import BaseModel


class TradeFlowStep(BaseModel):
    step: str
    status: str
    timestamp: str | None = None
    message: str | None = None
    order_id: str | None = None
    reason: str | None = None


class DashboardSummary(BaseModel):
    app_state: dict[str, Any]
    open_position: dict[str, Any]
    settings: dict[str, Any]
    mode: dict[str, Any]
    last_logs: dict[str, Any]
