"""Request-local execution context for multi-user routing."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from app.services.user_context import CurrentUser


@dataclass(frozen=True)
class ExecutionContext:
    user: CurrentUser
    proxy_url: str | None = None
    egress_ip: str | None = None


_CURRENT_EXECUTION: ContextVar[ExecutionContext | None] = ContextVar(
    "current_execution",
    default=None,
)


@contextmanager
def bind_execution_context(
    user: CurrentUser,
    *,
    proxy_url: str | None = None,
    egress_ip: str | None = None,
) -> Iterator[ExecutionContext]:
    context = ExecutionContext(user=user, proxy_url=proxy_url, egress_ip=egress_ip)
    token = _CURRENT_EXECUTION.set(context)
    try:
        yield context
    finally:
        _CURRENT_EXECUTION.reset(token)


@contextmanager
def bind_user_execution_context(user: CurrentUser) -> Iterator[ExecutionContext]:
    """Bind a user and their currently assigned egress node for request work."""
    proxy_url = None
    egress_ip = None
    if not user.is_dev:
        try:
            # Local import avoids a module cycle during application startup.
            from app.services.strategy_fanout import get_user_egress

            egress = get_user_egress(user.id)
            if egress:
                proxy_url = egress.get("proxy_url")
                egress_ip = egress.get("public_ip")
        except Exception:
            # Live Dhan calls fail closed when routing is enabled and no proxy is
            # present. Non-Dhan account screens should still remain available.
            pass
    with bind_execution_context(
        user,
        proxy_url=proxy_url,
        egress_ip=egress_ip,
    ) as context:
        yield context


def current_execution_context() -> ExecutionContext | None:
    return _CURRENT_EXECUTION.get()


def current_execution_user() -> CurrentUser | None:
    context = current_execution_context()
    return context.user if context else None


def current_proxy_url() -> str | None:
    context = current_execution_context()
    return context.proxy_url if context else None


def current_egress_ip() -> str | None:
    context = current_execution_context()
    return context.egress_ip if context else None
