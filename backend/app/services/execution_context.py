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
    egress_verified: bool = False
    expected_egress_ip: str | None = None
    observed_egress_ip: str | None = None
    egress_last_verified_at: str | None = None
    egress_provider: str | None = None
    egress_slot_number: int | None = None


class LiveEgressGuardError(RuntimeError):
    """Safe fail-closed error for live Dhan order egress validation."""


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
    egress_verified: bool = False,
    expected_egress_ip: str | None = None,
    observed_egress_ip: str | None = None,
    egress_last_verified_at: str | None = None,
    egress_provider: str | None = None,
    egress_slot_number: int | None = None,
) -> Iterator[ExecutionContext]:
    context = ExecutionContext(
        user=user,
        proxy_url=proxy_url,
        egress_ip=egress_ip,
        egress_verified=egress_verified,
        expected_egress_ip=expected_egress_ip,
        observed_egress_ip=observed_egress_ip,
        egress_last_verified_at=egress_last_verified_at,
        egress_provider=egress_provider,
        egress_slot_number=egress_slot_number,
    )
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
    egress_verified = False
    expected_egress_ip = None
    observed_egress_ip = None
    egress_last_verified_at = None
    egress_provider = None
    egress_slot_number = None
    if not user.is_dev:
        try:
            # Local import avoids a module cycle during application startup.
            from app.services.strategy_fanout import get_user_egress

            egress = get_user_egress(user.id)
            if egress:
                proxy_url = egress.get("proxy_url")
                egress_ip = egress.get("public_ip")
                expected_egress_ip = egress.get("expected_egress_ip") or egress_ip
                observed_egress_ip = egress.get("last_observed_ip")
                egress_verified = bool(egress.get("verified"))
                egress_last_verified_at = egress.get("last_verified_at")
                egress_provider = egress.get("provider")
                egress_slot_number = egress.get("slot_number")
        except Exception:
            # Live Dhan calls fail closed when routing is enabled and no proxy is
            # present. Non-Dhan account screens should still remain available.
            pass
    with bind_execution_context(
        user,
        proxy_url=proxy_url,
        egress_ip=egress_ip,
        egress_verified=egress_verified,
        expected_egress_ip=expected_egress_ip,
        observed_egress_ip=observed_egress_ip,
        egress_last_verified_at=egress_last_verified_at,
        egress_provider=egress_provider,
        egress_slot_number=egress_slot_number,
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


def current_expected_egress_ip() -> str | None:
    context = current_execution_context()
    if context is None:
        return None
    return context.expected_egress_ip or context.egress_ip


def current_egress_verified() -> bool:
    context = current_execution_context()
    return bool(context and context.egress_verified)


def require_verified_live_egress() -> ExecutionContext:
    """Require a server-derived, verified egress assignment for live Dhan writes."""
    context = current_execution_context()
    if context is None:
        raise LiveEgressGuardError("Live Dhan orders require authenticated execution context.")
    if not context.proxy_url:
        raise LiveEgressGuardError("Live Dhan orders require an assigned Nova Static IP.")
    expected_ip = context.expected_egress_ip or context.egress_ip
    if not expected_ip:
        raise LiveEgressGuardError("Live Dhan orders require an expected Nova Static IP.")
    if not context.egress_verified:
        raise LiveEgressGuardError("Live Dhan orders require a verified Nova Static IP.")
    if context.observed_egress_ip and context.observed_egress_ip != expected_ip:
        raise LiveEgressGuardError("Live Dhan orders require observed egress IP to match the assigned Nova Static IP.")
    return context
