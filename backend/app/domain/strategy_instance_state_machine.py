"""Central state machine for StrategyInstance.status.

Every status change must go through assert_transition()/transition() — no code
may assign StrategyInstance.status directly. The full platform state set is
declared now; states not reachable in the current phase simply have no inbound
transition yet.
"""
from __future__ import annotations

from enum import StrEnum

from app.domain.state_machine import StateTransitionError

__all__ = ["InstanceState", "StateTransitionError", "assert_transition", "ALLOWED_TRANSITIONS"]


class InstanceState(StrEnum):
    DRAFT = "draft"
    AWAITING_VALIDATION = "awaiting_validation"
    AWAITING_ADMIN_REVIEW = "awaiting_admin_review"
    APPROVED = "approved"
    READY = "ready"
    ACTIVE = "active"
    PAUSED = "paused"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    ARCHIVED = "archived"


# Phase 1 implements the READY/ACTIVE/PAUSED/STOPPED/ARCHIVED lifecycle.
# Validation/review states (DRAFT..APPROVED) gain inbound transitions in the
# personal-strategy phases; DEGRADED/STOPPING/ERROR when execution is
# instance-aware. ARCHIVED is terminal.
ALLOWED_TRANSITIONS: dict[InstanceState, frozenset[InstanceState]] = {
    InstanceState.DRAFT: frozenset({InstanceState.AWAITING_VALIDATION, InstanceState.ARCHIVED}),
    InstanceState.AWAITING_VALIDATION: frozenset(
        {InstanceState.AWAITING_ADMIN_REVIEW, InstanceState.DRAFT, InstanceState.ERROR}
    ),
    InstanceState.AWAITING_ADMIN_REVIEW: frozenset({InstanceState.APPROVED, InstanceState.DRAFT}),
    InstanceState.APPROVED: frozenset({InstanceState.READY, InstanceState.ARCHIVED}),
    InstanceState.READY: frozenset({InstanceState.ACTIVE, InstanceState.ARCHIVED}),
    InstanceState.ACTIVE: frozenset(
        {InstanceState.PAUSED, InstanceState.DEGRADED, InstanceState.STOPPING, InstanceState.STOPPED, InstanceState.ERROR}
    ),
    InstanceState.PAUSED: frozenset({InstanceState.ACTIVE, InstanceState.STOPPED, InstanceState.ARCHIVED}),
    InstanceState.DEGRADED: frozenset({InstanceState.ACTIVE, InstanceState.PAUSED, InstanceState.STOPPED, InstanceState.ERROR}),
    InstanceState.STOPPING: frozenset({InstanceState.STOPPED, InstanceState.ERROR}),
    InstanceState.STOPPED: frozenset({InstanceState.READY, InstanceState.ARCHIVED}),
    InstanceState.ERROR: frozenset({InstanceState.STOPPED, InstanceState.ARCHIVED}),
    InstanceState.ARCHIVED: frozenset(),
}


def assert_transition(current: InstanceState | str, target: InstanceState | str) -> None:
    """Raise StateTransitionError unless current -> target is an allowed move."""
    current_state = InstanceState(current)
    target_state = InstanceState(target)
    if target_state not in ALLOWED_TRANSITIONS[current_state]:
        raise StateTransitionError(
            f"Strategy instance cannot move from '{current_state}' to '{target_state}'."
        )
