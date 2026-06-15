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
