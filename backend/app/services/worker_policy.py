from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from sqlmodel import select

from app.auth.db import session_scope
from app.auth.models import User
from app.config import settings
from app.services.user_context import reset_current_user_id, set_current_user_id


VALID_WORKER_ROLES = {"web", "paper-worker", "live-pilot-worker", "trading-worker"}
T = TypeVar("T")


def configured_worker_role() -> str:
    role = settings.WORKER_ROLE.strip().lower()
    if role not in VALID_WORKER_ROLES:
        raise RuntimeError("WORKER_ROLE must be web, paper-worker, live-pilot-worker, or trading-worker.")
    return role


def validate_worker_configuration() -> bool:
    role = configured_worker_role()
    if role != "trading-worker" or not settings.ENABLE_TRADING_WORKERS:
        return False
    if settings.APP_ENV.lower() == "production" and not settings.TRADING_WORKER_DISTRIBUTED_LOCK_ENABLED:
        raise RuntimeError("Production trading workers require a verified DB/distributed leader lock.")
    if settings.AUTH_REQUIRED:
        raise RuntimeError(
            "Authenticated multi-user trading workers remain disabled until trading state is DB-backed "
            "and every worker loop uses explicit per-user iteration."
        )
    return True


def validate_paper_worker_configuration() -> bool:
    role = configured_worker_role()
    if role != "paper-worker":
        return False
    if not settings.ENABLE_PAPER_WORKERS:
        raise RuntimeError("WORKER_ROLE=paper-worker requires ENABLE_PAPER_WORKERS=true.")
    if settings.PAPER_WORKER_CONCURRENCY < 1 or settings.PAPER_WORKER_CONCURRENCY > 32:
        raise RuntimeError("PAPER_WORKER_CONCURRENCY must be between 1 and 32.")
    if settings.ENABLE_LIVE_ORDERS:
        raise RuntimeError("The Stage 5B paper worker cannot run while live orders are enabled.")
    return True


def validate_live_pilot_worker_configuration() -> bool:
    role = configured_worker_role()
    if role != "live-pilot-worker":
        return False
    if not settings.ENABLE_LIVE_PILOT_WORKERS:
        raise RuntimeError("WORKER_ROLE=live-pilot-worker requires ENABLE_LIVE_PILOT_WORKERS=true.")
    if not settings.LIVE_PILOT_ENABLED:
        raise RuntimeError("Live pilot workers require LIVE_PILOT_ENABLED=true.")
    if not settings.EXECUTION_NODE_ROUTING_ENABLED:
        raise RuntimeError("Live pilot workers require EXECUTION_NODE_ROUTING_ENABLED=true.")
    return True


def active_worker_user_ids() -> list[str]:
    with session_scope() as session:
        return list(session.exec(select(User.id).where(User.status == "active").order_by(User.id)).all())


def run_for_active_users(callback: Callable[[str], T]) -> list[T]:
    results: list[T] = []
    for user_id in active_worker_user_ids():
        token = set_current_user_id(user_id)
        try:
            results.append(callback(user_id))
        finally:
            reset_current_user_id(token)
    return results
