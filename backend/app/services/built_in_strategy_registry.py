"""Server-owned definitions for NOVA's product strategy catalog.

This registry is presentation and setup authority only.  A strategy is marked
READY only when a real execution adapter exists in this repository.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _standard_setup_schema() -> dict[str, Any]:
    return {
        "fields": [
            {
                "key": "direction",
                "type": "choice",
                "label": "Which signals should NOVA trade?",
                "options": ["CE", "PE", "BOTH"],
                "required": True,
                "default": "BOTH",
            },
            {
                "key": "lots",
                "type": "integer",
                "label": "How many lots should be used?",
                "minimum": 1,
                "maximum": 20,
                "required": True,
                "default": 1,
            },
            {
                "key": "stop_loss_percent",
                "type": "decimal",
                "label": "What stop loss percentage should be applied?",
                "minimum": 0,
                "maximum": 100,
                "required": True,
                "default": 10,
            },
            {
                "key": "take_profit_percent",
                "type": "decimal",
                "label": "What take profit percentage should be applied?",
                "minimum": 0,
                "maximum": 1000,
                "required": True,
                "default": 20,
            },
        ]
    }


_BUILT_INS: tuple[dict[str, Any], ...] = (
    {
        "strategy_key": "nova-supertrend",
        "catalog_code": "supertrend",
        "name": "Supertrend",
        "version": "1.0.0",
        "description": "NOVA built-in NIFTY Supertrend strategy.",
        "availability": "READY",
        "disabled_reason": None,
        "paper_eligible": True,
        "live_eligible": True,
        "execution_adapter": "strategy_webhook:supertrend",
        "setup_schema": _standard_setup_schema(),
    },
)


def _live_nova_shared_entries() -> list[dict[str, Any]]:
    """Strategies published via admin approval (see
    admin_pine_conversion_service.publish_as_shared). Read live from the
    database, not the static tuple above, so a new publish is selectable
    immediately -- no code change or deploy required. Each entry needs a
    StrategyCatalog row (owner_user_id IS NULL, visibility=nova_shared,
    status=active) and its latest approved StrategyVersion.

    list_built_ins() is called from validate_signal() on every webhook,
    including the legacy private-credential path that predates NOVA_SHARED
    and has nothing to do with it -- a DB outage or an environment with no
    DATABASE_URL (most unit tests) must not turn into every signal 500ing.
    Degrade to the static registry only (no newly-published strategies
    visible) rather than raise.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from app.db import models
    from app.db.engine import database_configured, session_scope

    if not database_configured():
        return []
    try:
        with session_scope() as db:
            rows = db.execute(
                select(models.StrategyCatalog, models.StrategyVersion)
                .join(
                    models.StrategyVersion,
                    models.StrategyVersion.strategy_id == models.StrategyCatalog.id,
                )
                .where(
                    models.StrategyCatalog.owner_user_id.is_(None),
                    models.StrategyCatalog.visibility == "nova_shared",
                    models.StrategyCatalog.status == "active",
                    models.StrategyVersion.status == "approved",
                )
                .order_by(models.StrategyVersion.approved_at.asc())
            ).all()
    except SQLAlchemyError:
        return []
    # Later (more recently approved) rows overwrite earlier ones for the
    # same code, so each catalog code resolves to its latest approval.
    entries: dict[str, dict[str, Any]] = {}
    for catalog, version in rows:
        entries[catalog.code] = {
            "strategy_key": f"nova-{catalog.code}",
            "catalog_code": catalog.code,
            "name": catalog.display_name,
            "version": version.version,
            "description": catalog.description or catalog.display_name,
            "availability": "READY",
            "disabled_reason": None,
            "paper_eligible": True,
            "live_eligible": True,
            "execution_adapter": f"strategy_webhook:{catalog.code}",
            "setup_schema": _standard_setup_schema(),
        }
    return list(entries.values())


def list_built_ins() -> list[dict[str, Any]]:
    # Static entries are the default/placeholder menu (COMING_SOON stubs) and
    # stay authoritative for their own product flags (live_eligible,
    # paper_eligible, description, ...) -- tests and future config rely on
    # patching _BUILT_INS directly. A live published row only flips
    # availability/adapter/version for a code that already has a stub; a
    # code with no static stub at all (a brand-new published strategy) is
    # added wholesale with the DB-derived defaults.
    merged: dict[str, dict[str, Any]] = {
        item["catalog_code"]: deepcopy(item) for item in _BUILT_INS
    }
    for live in _live_nova_shared_entries():
        code = live["catalog_code"]
        if code in merged:
            merged[code].update(
                availability="READY",
                disabled_reason=None,
                execution_adapter=live["execution_adapter"],
                version=live["version"],
            )
        else:
            merged[code] = live
    return list(merged.values())


def get_built_in(strategy_key: str) -> dict[str, Any] | None:
    normalized = str(strategy_key or "").strip().lower()
    return next(
        (item for item in list_built_ins() if item["strategy_key"] == normalized),
        None,
    )


def key_for_catalog_code(code: str | None) -> str | None:
    normalized = str(code or "").strip().lower()
    item = next(
        (item for item in list_built_ins() if item["catalog_code"] == normalized),
        None,
    )
    return str(item["strategy_key"]) if item else None


def standard_setup_schema() -> dict[str, Any]:
    return _standard_setup_schema()
