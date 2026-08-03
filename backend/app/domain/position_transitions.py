"""Central position-state transition matrix (Phase 2B1).

Every typed position operation validates its transition here BEFORE mutating
PostgreSQL. States mirror models.ACTIVE_POSITION_STATES plus 'closed';
'scaling_in' is schema-supported but has no inbound transition until manual
scale-in is explicitly authorized (Phase 4).
"""
from __future__ import annotations

from app.domain.state_machine import StateTransitionError

__all__ = ["PositionTransitionError", "assert_position_transition", "ALLOWED_POSITION_TRANSITIONS"]


class PositionTransitionError(StateTransitionError):
    pass


# None = no active row (flat slot). 'open' -> 'open' is permitted for
# non-financial refreshes (entry-fill metadata sync); monitor P&L ticks never
# reach typed operations at all.
ALLOWED_POSITION_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"entering", "open"}),
    "entering": frozenset({"open", "exiting", "reversing", "closed", "error_reconciling"}),
    "open": frozenset({"open", "exiting", "reversing", "closed", "error_reconciling"}),
    "exiting": frozenset({"exiting", "closed", "open", "error_reconciling"}),
    "reversing": frozenset({"reversing", "exiting", "closed", "open", "error_reconciling"}),
    "error_reconciling": frozenset({"open", "exiting", "closed"}),
    "closed": frozenset(),  # terminal: the next lifetime is a NEW row
}


def assert_position_transition(current: str | None, target: str) -> None:
    allowed = ALLOWED_POSITION_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise PositionTransitionError(
            f"Position cannot move from {current!r} to {target!r}."
        )
