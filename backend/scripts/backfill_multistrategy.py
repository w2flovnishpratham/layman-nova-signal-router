#!/usr/bin/env python
"""Seed Phase 1 multi-strategy tables and backfill Supertrend instances.

This script is idempotent and additive. It does not delete or disable legacy
strategy_subscriptions, and the live webhook path does not read the new tables.

Usage from backend/ with DATABASE_URL set:

    python -m scripts.backfill_multistrategy
    python scripts/backfill_multistrategy.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DEFAULT_STRATEGY_CODE  # noqa: E402
from app.core.enums import (  # noqa: E402
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
)
from app.db import models  # noqa: E402
from app.db.engine import database_configured, session_scope  # noqa: E402


SUPERTREND_CODE = "SUPERTREND_V1"
SUPERTREND_VERSION = "v1"


CATALOG_SEED = [
    {
        "code": SUPERTREND_CODE,
        "name": "Supertrend",
        "status": StrategyCatalogStatus.ACTIVE.value,
        "source_type": StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        "description": "Current Nova Supertrend TradingView strategy.",
        "metadata": {
            "legacy_strategy_names": ["supertrend", DEFAULT_STRATEGY_CODE],
            "aliases": ["supertrend", DEFAULT_STRATEGY_CODE, "TRADINGVIEW_NIFTY_V1"],
        },
    },
    {
        "code": "ORB",
        "name": "ORB",
        "status": StrategyCatalogStatus.COMING_SOON.value,
        "source_type": StrategySourceType.BACKEND_HOSTED.value,
        "description": "Opening Range Breakout placeholder.",
        "metadata": {"aliases": ["orb"]},
    },
    {
        "code": "VWAP",
        "name": "VWAP",
        "status": StrategyCatalogStatus.COMING_SOON.value,
        "source_type": StrategySourceType.BACKEND_HOSTED.value,
        "description": "VWAP strategy placeholder.",
        "metadata": {"aliases": ["vwap"]},
    },
    {
        "code": "RSI",
        "name": "RSI",
        "status": StrategyCatalogStatus.COMING_SOON.value,
        "source_type": StrategySourceType.BACKEND_HOSTED.value,
        "description": "RSI strategy placeholder.",
        "metadata": {"aliases": ["rsi"]},
    },
    {
        "code": "SCALPER",
        "name": "Scalper",
        "status": StrategyCatalogStatus.COMING_SOON.value,
        "source_type": StrategySourceType.BACKEND_HOSTED.value,
        "description": "Scalper strategy placeholder.",
        "metadata": {"aliases": ["scalper"]},
    },
]


def _canonical_strategy_name(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("_", "-")
    aliases = {
        "supertrend": "supertrend",
        DEFAULT_STRATEGY_CODE.lower().replace("_", "-"): "supertrend",
        SUPERTREND_CODE.lower().replace("_", "-"): "supertrend",
    }
    return aliases.get(normalized, normalized)


def _risk_config_for_subscription(db, subscription: models.StrategySubscription) -> dict[str, Any]:
    rows = db.scalars(
        select(models.UserStrategyRiskControl).where(
            models.UserStrategyRiskControl.user_id == subscription.user_id
        )
    ).all()
    for row in rows:
        if _canonical_strategy_name(row.strategy_name) != "supertrend":
            continue
        return {
            "kill_switch": bool(row.kill_switch),
            "max_lots_per_order": int(row.max_lots_per_order or 0),
            "max_notional_per_trade_paise": int(row.max_notional_per_trade_paise or 0),
            "max_orders_per_day": int(row.max_orders_per_day or 0),
            "max_loss_per_day_paise": int(row.max_loss_per_day_paise or 0),
            "source": "user_strategy_risk_controls",
        }
    return {}


def seed_strategy_catalog(db) -> dict[str, int]:
    created_catalog = 0
    updated_catalog = 0
    created_versions = 0

    for entry in CATALOG_SEED:
        catalog = db.scalar(
            select(models.StrategyCatalog).where(models.StrategyCatalog.code == entry["code"])
        )
        if catalog is None:
            catalog = models.StrategyCatalog(
                code=entry["code"],
                name=entry["name"],
                status=entry["status"],
                source_type=entry["source_type"],
                description=entry["description"],
                metadata_json=entry["metadata"],
            )
            db.add(catalog)
            db.flush()
            created_catalog += 1
        else:
            catalog.name = entry["name"]
            catalog.status = entry["status"]
            catalog.source_type = entry["source_type"]
            catalog.description = entry["description"]
            catalog.metadata_json = entry["metadata"]
            updated_catalog += 1

        version = db.scalar(
            select(models.StrategyVersion).where(
                models.StrategyVersion.strategy_id == catalog.id,
                models.StrategyVersion.version == SUPERTREND_VERSION,
            )
        )
        if version is None:
            db.add(
                models.StrategyVersion(
                    strategy_id=catalog.id,
                    version=SUPERTREND_VERSION,
                    status=entry["status"],
                    payload_version="nova.v1" if entry["code"] == SUPERTREND_CODE else None,
                    notes="Seeded by Phase 1 multi-strategy backfill.",
                    metadata_json={
                        "seeded_by": "backfill_multistrategy",
                        "catalog_code": entry["code"],
                        "legacy_strategy_code": DEFAULT_STRATEGY_CODE if entry["code"] == SUPERTREND_CODE else None,
                    },
                )
            )
            created_versions += 1
        else:
            version.status = entry["status"]
            if entry["code"] == SUPERTREND_CODE:
                version.payload_version = version.payload_version or "nova.v1"

    return {
        "catalog_created": created_catalog,
        "catalog_updated": updated_catalog,
        "versions_created": created_versions,
    }


def _supertrend_catalog_and_version(db) -> tuple[models.StrategyCatalog, models.StrategyVersion]:
    catalog = db.scalar(select(models.StrategyCatalog).where(models.StrategyCatalog.code == SUPERTREND_CODE))
    if catalog is None:
        raise RuntimeError("Supertrend catalog was not seeded.")
    version = db.scalar(
        select(models.StrategyVersion).where(
            models.StrategyVersion.strategy_id == catalog.id,
            models.StrategyVersion.version == SUPERTREND_VERSION,
        )
    )
    if version is None:
        raise RuntimeError("Supertrend strategy version was not seeded.")
    return catalog, version


def _active_real_order_conflict(db, user_id, legacy_subscription_id) -> bool:
    row = db.scalar(
        select(models.UserStrategyInstance).where(
            models.UserStrategyInstance.user_id == user_id,
            models.UserStrategyInstance.execution_mode == StrategyExecutionMode.REAL_ORDERS.value,
            models.UserStrategyInstance.status == StrategyInstanceStatus.ACTIVE.value,
            models.UserStrategyInstance.legacy_subscription_id != legacy_subscription_id,
        )
    )
    return row is not None


def backfill_supertrend_instances(db) -> dict[str, int]:
    catalog, version = _supertrend_catalog_and_version(db)
    created = 0
    updated = 0
    skipped = 0

    subscriptions = db.scalars(
        select(models.StrategySubscription).where(models.StrategySubscription.active.is_(True))
    ).all()
    for subscription in subscriptions:
        if _canonical_strategy_name(subscription.strategy_name) != "supertrend":
            skipped += 1
            continue

        instance = db.scalar(
            select(models.UserStrategyInstance).where(
                models.UserStrategyInstance.legacy_subscription_id == subscription.id
            )
        )
        execution_mode = subscription.execution_mode or StrategyExecutionMode.SIGNAL_ONLY.value
        status = StrategyInstanceStatus.ACTIVE.value
        config: dict[str, Any] = {
            "legacy_strategy_name": subscription.strategy_name,
            "legacy_strategy_code": DEFAULT_STRATEGY_CODE,
            "backfilled_from": "strategy_subscriptions",
        }
        if execution_mode == StrategyExecutionMode.REAL_ORDERS.value and _active_real_order_conflict(
            db,
            subscription.user_id,
            subscription.id,
        ):
            status = StrategyInstanceStatus.PAUSED.value
            config["backfill_note"] = "Paused to preserve one active real_orders strategy per user."

        values = {
            "user_id": subscription.user_id,
            "strategy_id": catalog.id,
            "strategy_version_id": version.id,
            "legacy_subscription_id": subscription.id,
            "instance_label": "Supertrend",
            "source_type": StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
            "status": status,
            "execution_mode": execution_mode,
            "lots": max(int(subscription.lots or 1), 1),
            "side_preference": None,
            "config_json": config,
            "risk_config": _risk_config_for_subscription(db, subscription),
        }

        if instance is None:
            db.add(models.UserStrategyInstance(**values))
            created += 1
        else:
            for key, value in values.items():
                setattr(instance, key, value)
            updated += 1

    return {
        "instances_created": created,
        "instances_updated": updated,
        "subscriptions_skipped": skipped,
    }


def run_backfill() -> dict[str, int]:
    with session_scope() as db:
        summary = {}
        summary.update(seed_strategy_catalog(db))
        summary.update(backfill_supertrend_instances(db))
        return summary


def main() -> int:
    if not database_configured():
        print("ERROR: DATABASE_URL is not set.")
        return 1
    summary = run_backfill()
    print("Multi-strategy Phase 1 backfill complete:")
    for key in sorted(summary):
        print(f"  {key}: {summary[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
