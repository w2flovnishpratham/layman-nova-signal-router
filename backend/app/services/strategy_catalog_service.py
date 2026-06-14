from __future__ import annotations

from sqlmodel import Session, select

from app.auth.db import session_scope
from app.auth.models import Strategy, StrategyVersion, utc_now_dt


INITIAL_STRATEGIES = (
    {
        "strategy_code": "ORB_PORTAL",
        "name": "ORB Portal",
        "description": "Opening-range breakout signals for NIFTY options.",
        "risk_level": "medium",
        "timeframe": "5m",
    },
    {
        "strategy_code": "SUPERTREND_FLIP",
        "name": "Supertrend Flip",
        "description": "Trend reversal signals from confirmed Supertrend flips.",
        "risk_level": "medium",
        "timeframe": "5m",
    },
    {
        "strategy_code": "SUPPORT_RESISTANCE_REVERSAL",
        "name": "Support/Resistance Reversal",
        "description": "Reversal signals near validated intraday support and resistance.",
        "risk_level": "medium",
        "timeframe": "5m",
    },
    {
        "strategy_code": "OOPS_GAP_REVERSAL",
        "name": "OOPS Gap Reversal",
        "description": "Gap-reversal signals after confirmation.",
        "risk_level": "high",
        "timeframe": "5m",
    },
    {
        "strategy_code": "NIFTY_MOMENTUM_SCALPER",
        "name": "Nifty Momentum Scalper",
        "description": "Short-timeframe NIFTY option momentum signals.",
        "risk_level": "high",
        "timeframe": "1m",
    },
)

SIGNAL_SCHEMA = {
    "required": ["strategy_code", "signal_id", "symbol", "action"],
    "paper_fill_requires": ["price"],
    "option_types": ["CE", "PE"],
}


def seed_strategy_catalog() -> None:
    with session_scope() as session:
        for seed in INITIAL_STRATEGIES:
            strategy = session.exec(
                select(Strategy).where(Strategy.strategy_code == seed["strategy_code"])
            ).first()
            if strategy is None:
                strategy = Strategy(
                    **seed,
                    market="NSE",
                    instrument_type="OPTIDX",
                    is_active=True,
                    is_paper_allowed=True,
                    is_live_allowed=seed["strategy_code"] == "SUPERTREND_FLIP",
                )
                session.add(strategy)
                session.flush()
            else:
                strategy.is_live_allowed = seed["strategy_code"] == "SUPERTREND_FLIP"
                strategy.is_paper_allowed = True
                session.add(strategy)

            version = session.exec(
                select(StrategyVersion).where(
                    StrategyVersion.strategy_id == strategy.id,
                    StrategyVersion.version == 1,
                )
            ).first()
            if version is None:
                version = StrategyVersion(
                    strategy_id=int(strategy.id),
                    version=1,
                    status="active",
                    changelog="Initial paper-safe catalog version.",
                    signal_schema_json=SIGNAL_SCHEMA,
                    activated_at=utc_now_dt(),
                )
                session.add(version)
            elif not session.exec(
                select(StrategyVersion).where(
                    StrategyVersion.strategy_id == strategy.id,
                    StrategyVersion.status == "active",
                )
            ).first():
                version.status = "active"
                version.activated_at = utc_now_dt()
                session.add(version)
        session.commit()


def list_active_strategies(session: Session) -> list[Strategy]:
    return list(
        session.exec(
            select(Strategy)
            .where(Strategy.is_active.is_(True))
            .order_by(Strategy.name)
        ).all()
    )


def get_strategy_by_code(session: Session, strategy_code: str) -> Strategy | None:
    return session.exec(
        select(Strategy).where(Strategy.strategy_code == strategy_code.strip().upper())
    ).first()


def get_active_version(session: Session, strategy_id: int) -> StrategyVersion | None:
    return session.exec(
        select(StrategyVersion)
        .where(
            StrategyVersion.strategy_id == strategy_id,
            StrategyVersion.status == "active",
        )
        .order_by(StrategyVersion.version.desc())
    ).first()
