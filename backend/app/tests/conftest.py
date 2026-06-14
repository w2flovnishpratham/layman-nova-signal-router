from __future__ import annotations

import pytest
from sqlmodel import delete

from app.auth.db import init_auth_db, session_scope
from app.auth.models import (
    ExecutorNonceReceipt,
    ExecutorNode,
    LiveOrderJob,
    OrderRouteAttempt,
    PaperLedgerEntry,
    PaperPosition,
    PaperStrategyOrder,
    StrategySignal,
    UserSignalJob,
    UserStrategySubscription,
    UserNotification,
    UserExecutorAssignment,
    UserLivePilotApproval,
    WebhookNonceReceipt,
    WebhookSignalReceipt,
    WorkerHeartbeat,
    WorkerJob,
)
from app.config import settings
from app.services.strategy_catalog_service import seed_strategy_catalog


@pytest.fixture(autouse=True)
def safe_test_defaults(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", False)
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL", True)
    monkeypatch.setattr(settings, "WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", 60)
    monkeypatch.setattr(settings, "REQUIRE_SIGNAL_ID_LIVE", False)
    monkeypatch.setattr(settings, "REQUIRE_INSTRUMENT_MASTER_VALIDATION_LIVE", False)
    monkeypatch.setattr(settings, "REQUIRE_FRESH_EXPIRY_LIVE", False)
    monkeypatch.setattr(settings, "WORKER_ROLE", "web")
    monkeypatch.setattr(settings, "ENABLE_TRADING_WORKERS", False)
    monkeypatch.setattr(settings, "TRADING_WORKER_DISTRIBUTED_LOCK_ENABLED", False)
    monkeypatch.setattr(settings, "ENABLE_PAPER_WORKERS", False)
    monkeypatch.setattr(settings, "PAPER_WORKER_CONCURRENCY", 1)
    monkeypatch.setattr(settings, "WORKER_JOB_LOCK_SECONDS", 60)
    monkeypatch.setattr(settings, "WORKER_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(settings, "WORKER_HEARTBEAT_SECONDS", 10.0)
    monkeypatch.setattr(settings, "WORKER_RETRY_BASE_SECONDS", 1)
    monkeypatch.setattr(settings, "PAPER_QUEUE_INLINE_LOCAL", True)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings, "FREE_MAX_ACTIVE_PAPER_STRATEGIES", 1)
    monkeypatch.setattr(settings, "RELAY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "RELAY_ALLOWED_STRATEGIES", "SUPERTREND_FLIP")
    monkeypatch.setattr(settings, "RELAY_ENABLED", False)
    monkeypatch.setattr(settings, "LIVE_PILOT_ENABLED", False)
    monkeypatch.setattr(settings, "LIVE_PILOT_ALLOWED_STRATEGIES", "SUPERTREND_FLIP")
    monkeypatch.setattr(settings, "LIVE_ORDER_DRY_RUN_ONLY", True)
    monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", False)
    monkeypatch.setattr(settings, "EXECUTOR_SHARED_SECRETS_JSON", "{}")
    monkeypatch.setattr(settings, "EXECUTOR_TIMESTAMP_TOLERANCE_SECONDS", 60)
    monkeypatch.setattr(settings, "EXECUTOR_CODE", "")
    monkeypatch.setattr(settings, "EXECUTOR_RESERVED_IP", "")
    monkeypatch.setattr(settings, "EXECUTOR_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "EXECUTOR_REAL_ORDERS_ENABLED", False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_PILOT_WORKERS", False)
    init_auth_db()
    seed_strategy_catalog()
    with session_scope() as session:
        session.exec(delete(ExecutorNonceReceipt))
        session.exec(delete(UserNotification))
        session.exec(delete(PaperLedgerEntry))
        session.exec(delete(PaperPosition))
        session.exec(delete(WorkerHeartbeat))
        session.exec(delete(WorkerJob))
        session.exec(delete(LiveOrderJob))
        session.exec(delete(UserSignalJob))
        session.exec(delete(PaperStrategyOrder))
        session.exec(delete(StrategySignal))
        session.exec(delete(UserStrategySubscription))
        session.exec(delete(UserLivePilotApproval))
        session.exec(delete(UserExecutorAssignment))
        session.exec(delete(ExecutorNode))
        session.exec(delete(OrderRouteAttempt))
        session.exec(delete(WebhookNonceReceipt))
        session.exec(delete(WebhookSignalReceipt))
        session.commit()
