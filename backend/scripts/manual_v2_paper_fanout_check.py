#!/usr/bin/env python
"""Manual staging-style check for controlled v2 paper fanout.

Usage from backend/:

    python -m scripts.manual_v2_paper_fanout_check --dry-run
    python -m scripts.manual_v2_paper_fanout_check --confirm-paper-only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core.enums import (  # noqa: E402
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
)
from app.core.feature_flags import (  # noqa: E402
    MULTI_STRATEGY_FANOUT,
    V2_PAPER_RUNNER_DEBUG,
    feature_flag_states,
    is_feature_enabled,
)
from app.db import crud, models  # noqa: E402
from app.db.engine import database_configured, session_scope  # noqa: E402
from app.schemas.nova_signal_v1 import NovaSignalV1  # noqa: E402
from app.services import portfolio_analytics, state_store  # noqa: E402
from app.services.strategy_paper_fanout_runner_v2 import run_v2_paper_fanout_once  # noqa: E402


SUPERTREND_CODE = "SUPERTREND_V1"
SUPERTREND_VERSION = "v1"
TEST_USER_EMAIL = "manual-v2-paper-fanout-check@example.invalid"
TEST_GOOGLE_SUB = "manual-v2-paper-fanout-check"
TEST_INSTANCE_LABEL = "manual-v2-paper-fanout-check"
PAPER_CONFIRM_ENV = "V2_PAPER_FANOUT_CONFIRM"
PAPER_CONFIRM_VALUE = "paper_only"


class ManualCheckError(RuntimeError):
    """Raised when the script refuses to run or verification fails."""


@dataclass(frozen=True)
class ManualCheckConfig:
    dry_run: bool = False
    confirm_paper_only: bool = False
    simulate_paper_ltp: bool = True
    strategy_code: str = SUPERTREND_CODE
    user_email: str = TEST_USER_EMAIL
    strike: float = 25000.0
    expiry: str = "2026-07-09"
    lots: int = 1
    max_jobs: int = 1


def build_entry_payload(*, signal_id: str, strike: float, expiry: str, lots: int) -> dict[str, Any]:
    return _base_payload(
        signal_id=signal_id,
        action="ENTRY",
        intent="BULLISH",
        option_side="AUTO",
        strike=strike,
        expiry=expiry,
        lots=lots,
    )


def build_exit_payload(*, signal_id: str, strike: float, expiry: str, lots: int) -> dict[str, Any]:
    return _base_payload(
        signal_id=signal_id,
        action="EXIT",
        intent="FLAT",
        option_side="NONE",
        strike=strike,
        expiry=expiry,
        lots=lots,
    )


def cleanup_instance_position(instance_id: str) -> None:
    """Clear only the v2 instance-scoped state slot; legacy global state is untouched."""
    state_store.clear_open_position(instance_id=instance_id)


def run_manual_check(
    config: ManualCheckConfig,
    *,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "dry_run": config.dry_run,
        "strategy_code": config.strategy_code,
        "flags": feature_flag_states(),
        "settings": _settings_summary(),
    }
    _emit(out, "feature_flags", summary["flags"])
    _emit(out, "settings", summary["settings"])
    _require_safe_environment(config)

    family = f"manual-v2-paper-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    entry_payload = build_entry_payload(
        signal_id=f"{family}-entry",
        strike=config.strike,
        expiry=config.expiry,
        lots=config.lots,
    )
    exit_payload = build_exit_payload(
        signal_id=f"{family}-exit",
        strike=config.strike,
        expiry=config.expiry,
        lots=config.lots,
    )
    NovaSignalV1.model_validate(entry_payload)
    NovaSignalV1.model_validate(exit_payload)

    summary["payloads"] = {
        "entry": _payload_preview(entry_payload),
        "exit": _payload_preview(exit_payload),
    }

    if config.dry_run:
        summary["would_run"] = [
            "ensure SUPERTREND_V1 catalog/version",
            "ensure one active paper_live_data test instance",
            "run ENTRY through run_v2_paper_fanout_once",
            "verify instance position exists",
            "run EXIT through run_v2_paper_fanout_once",
            "verify instance position is cleared and legacy global position is unchanged",
        ]
        _emit(out, "dry_run_summary", summary)
        return _sanitize(summary)

    state_store.init_runtime_files()
    runtime_snapshot = _prepare_runtime_for_manual_check()
    paper_market_shim = _install_simulated_paper_market_data() if config.simulate_paper_ltp else None
    legacy_before = state_store.get_paper_position()
    instance_id: str | None = None
    try:
        with session_scope() as db:
            user = _ensure_test_user(db, config.user_email)
            catalog, version = _ensure_supertrend_catalog_version(db)
            instance = _ensure_single_test_paper_instance(db, user=user, catalog=catalog, version=version)
            instance_id = str(instance.id)
            summary["user_id"] = str(user.id)
            summary["instance_id"] = instance_id
            summary["instance_created_or_reused"] = True

            entry_result = run_v2_paper_fanout_once(
                db,
                entry_payload,
                config.strategy_code,
                max_jobs=config.max_jobs,
            )
            entry_position = state_store.get_open_position(instance_id)
            if not _result_ok(entry_result) or not (entry_position or {}).get("has_open_position"):
                summary["entry_result"] = _result_dict(entry_result)
                summary["entry_position"] = _sanitize(entry_position)
                raise ManualCheckError("ENTRY verification failed; instance position was not opened.")

            exit_result = run_v2_paper_fanout_once(
                db,
                exit_payload,
                config.strategy_code,
                max_jobs=config.max_jobs,
            )
            exit_position = state_store.get_open_position(instance_id)
            legacy_after = state_store.get_paper_position()
            analytics_pairing = build_analytics_pairing_preview(instance_id=instance_id)

            summary.update(
                {
                    "entry_result": _result_dict(entry_result),
                    "exit_result": _result_dict(exit_result),
                    "entry_position_opened": True,
                    "exit_position_cleared": exit_position is None,
                    "legacy_position_untouched": legacy_after == legacy_before,
                    "analytics_pairing": analytics_pairing,
                }
            )

            if not _result_ok(exit_result):
                raise ManualCheckError("EXIT verification failed; runner result was not ok.")
            if exit_position is not None:
                raise ManualCheckError("EXIT verification failed; instance position is still present.")
            if legacy_after != legacy_before:
                raise ManualCheckError("Legacy global paper position changed during v2 paper run.")
            if analytics_pairing.get("paired_count") != 1:
                raise ManualCheckError("Analytics instance pairing check failed.")
    finally:
        if instance_id:
            cleanup_instance_position(instance_id)
        if paper_market_shim is not None:
            _restore_simulated_paper_market_data(paper_market_shim)
        _restore_runtime_after_manual_check(runtime_snapshot)

    sanitized = _sanitize(summary)
    _emit(out, "success_summary", sanitized)
    return sanitized


def build_analytics_pairing_preview(*, instance_id: str) -> dict[str, Any]:
    """Exercise portfolio pairing by instance using sanitized synthetic events.

    The production dashboard intentionally excludes PAPER-* fills from live-money
    analytics, so this script checks the pairing helper without changing that
    dashboard behavior.
    """
    events = [
        {
            "phase": "after_response",
            "success": True,
            "status": "TRADED",
            "mode": "live",
            "order_id": "CHECK-ENTRY",
            "action": "ENTRY",
            "trading_symbol": "NIFTY CHECK CE",
            "option_side": "CE",
            "qty": 75,
            "avg_price": 100.0,
            "timestamp": "2026-07-08T09:20:00+05:30",
            "instance_id": instance_id,
            "strategy_code": SUPERTREND_CODE,
            "execution_mode": StrategyExecutionMode.PAPER_LIVE_DATA.value,
        },
        {
            "phase": "after_response",
            "success": True,
            "status": "TRADED",
            "mode": "live",
            "order_id": "CHECK-EXIT",
            "action": "EXIT",
            "trading_symbol": "NIFTY CHECK CE",
            "option_side": "CE",
            "qty": 75,
            "avg_price": 105.0,
            "timestamp": "2026-07-08T09:35:00+05:30",
            "instance_id": instance_id,
            "strategy_code": SUPERTREND_CODE,
            "execution_mode": StrategyExecutionMode.PAPER_LIVE_DATA.value,
        },
        {
            "phase": "after_response",
            "success": True,
            "status": "TRADED",
            "mode": "live",
            "order_id": "CHECK-OTHER-EXIT",
            "action": "EXIT",
            "qty": 75,
            "avg_price": 106.0,
            "timestamp": "2026-07-08T09:36:00+05:30",
            "instance_id": "other-instance",
        },
    ]
    trades, open_entry = portfolio_analytics._pair_round_trips(events)
    return {
        "paired_count": len(trades),
        "open_entry_present": open_entry is not None,
        "instance_id": trades[0].get("instance_id") if trades else None,
        "entry_order_id": trades[0].get("entry_order_id") if trades else None,
        "exit_order_id": trades[0].get("exit_order_id") if trades else None,
    }


def parse_args(argv: list[str] | None = None) -> ManualCheckConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate setup and print planned actions only.")
    parser.add_argument(
        "--confirm-paper-only",
        action="store_true",
        help="Required for mutation. Equivalent env: V2_PAPER_FANOUT_CONFIRM=paper_only.",
    )
    parser.add_argument("--user-email", default=TEST_USER_EMAIL)
    parser.add_argument("--strike", type=float, default=25000.0)
    parser.add_argument("--expiry", default="2026-07-09")
    parser.add_argument("--lots", type=int, default=1)
    parser.add_argument("--max-jobs", type=int, default=1)
    parser.add_argument(
        "--use-real-paper-market-data",
        action="store_true",
        help="Do not install the local simulated LTP shim. Requires shared paper market-data credentials and market hours.",
    )
    args = parser.parse_args(argv)
    return ManualCheckConfig(
        dry_run=bool(args.dry_run),
        confirm_paper_only=bool(args.confirm_paper_only),
        simulate_paper_ltp=not bool(args.use_real_paper_market_data),
        user_email=args.user_email,
        strike=args.strike,
        expiry=args.expiry,
        lots=args.lots,
        max_jobs=args.max_jobs,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    try:
        run_manual_check(config)
    except ManualCheckError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": _safe_exception_message(exc),
                },
                sort_keys=True,
            )
        )
        return 1
    return 0


def _base_payload(
    *,
    signal_id: str,
    action: str,
    intent: str,
    option_side: str,
    strike: float,
    expiry: str,
    lots: int,
) -> dict[str, Any]:
    return {
        "version": "nova.v1",
        "secret": "manual-v2-paper-check-secret",
        "signal_id": signal_id,
        "strategy_code": SUPERTREND_CODE,
        "action": action,
        "intent": intent,
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "option_side": option_side,
        "strike_mode": "MANUAL",
        "strike": strike,
        "expiry_mode": "MANUAL",
        "expiry": expiry,
        "qty_mode": "LOTS",
        "lots": lots,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
        "source": "backend",
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {"source": "manual_v2_paper_fanout_check"},
    }


def _require_safe_environment(config: ManualCheckConfig) -> None:
    errors = []
    if not settings.DEBUG_ENABLED:
        errors.append("DEBUG_ENABLED must be true.")
    if not is_feature_enabled(MULTI_STRATEGY_FANOUT):
        errors.append("MULTI_STRATEGY_FANOUT must be true.")
    if not is_feature_enabled(V2_PAPER_RUNNER_DEBUG):
        errors.append("V2_PAPER_RUNNER_DEBUG must be true.")
    if config.max_jobs < 1 or config.max_jobs > 5:
        errors.append("max_jobs must be between 1 and 5.")
    if config.lots < 1:
        errors.append("lots must be >= 1.")

    explicit_mode = _read_explicit_engine_mode_without_init()
    fallback_mode = _fallback_engine_mode()
    current_mode = explicit_mode or fallback_mode
    live_orders_enabled = bool(settings.ENABLE_LIVE_ORDERS)
    if live_orders_enabled and current_mode != "paper":
        errors.append("ENABLE_LIVE_ORDERS=true is allowed only when explicit/current engine mode is paper.")

    if not config.dry_run:
        confirmed = config.confirm_paper_only or os.environ.get(PAPER_CONFIRM_ENV) == PAPER_CONFIRM_VALUE
        if not confirmed:
            errors.append("Pass --confirm-paper-only or set V2_PAPER_FANOUT_CONFIRM=paper_only.")
        if not database_configured():
            errors.append("DATABASE_URL must be configured for mutation.")

        if explicit_mode not in (None, "paper"):
            errors.append("Explicit engine mode must be paper or unset.")
        if not errors and explicit_mode is None and state_store.get_paper_position().get("has_open_position"):
            errors.append("A legacy paper position exists; set explicit engine_mode=paper before running.")

    if errors:
        raise ManualCheckError(" ".join(errors))


def _settings_summary() -> dict[str, Any]:
    return {
        "debug_enabled": bool(settings.DEBUG_ENABLED),
        "enable_live_orders": bool(settings.ENABLE_LIVE_ORDERS),
        "dhan_mode": str(settings.DHAN_MODE).upper(),
        "database_configured": database_configured(),
        "explicit_engine_mode": _read_explicit_engine_mode_without_init(),
        "fallback_engine_mode": _fallback_engine_mode(),
    }


def _prepare_runtime_for_manual_check() -> dict[str, Any]:
    snapshot = {
        "app_state": state_store.get_app_state(),
        "runtime_settings": state_store.get_runtime_settings(),
    }
    if state_store.get_engine_mode(legacy_fallback=False) != "paper":
        state_store.set_engine_mode("paper")
    state_store.update_app_state(webhook_trading_enabled=True, engine_started=True)
    state_store.update_runtime_settings(
        allow_entry=True,
        allow_exit=True,
        emergency_stop=False,
        global_kill_switch=False,
        max_trades_per_day=0,
        max_daily_loss=0,
        allowed_option_side="BOTH",
        option_exit_mode="SERVER",
    )
    return snapshot


def _restore_runtime_after_manual_check(snapshot: dict[str, Any]) -> None:
    try:
        runtime_settings = snapshot.get("runtime_settings")
        app_state = snapshot.get("app_state")
        if isinstance(runtime_settings, dict):
            state_store.set_runtime_settings(runtime_settings)
        if isinstance(app_state, dict):
            state_store.set_app_state(app_state)
    except Exception:
        # Verification outcome should not be hidden by best-effort restoration.
        pass


def _install_simulated_paper_market_data() -> dict[str, Any]:
    from app.services import execution_router, paper_broker, risk_manager, shared_market_data
    from app.services.credential_vault import DhanCredentials
    from app.services.dhan_client import DhanLtpResult
    from app.services.security_id_resolver import SecurityIdResolution

    originals = {
        "risk_market_is_open": risk_manager._market_is_open,
        "execution_market_is_open": execution_router._market_is_open,
        "execution_resolve_security_id": execution_router.resolve_security_id,
        "paper_market_is_open": paper_broker._market_is_open,
        "paper_ltp": paper_broker.PaperBroker._ltp,
        "shared_configured": shared_market_data.shared_market_data_configured,
        "shared_credentials": shared_market_data.get_shared_market_credentials,
        "shared_status": shared_market_data.shared_market_data_status,
    }

    def always_open() -> bool:
        return True

    def simulated_ltp(self, *, client_id: str, access_token: str, payload: dict[str, Any]) -> DhanLtpResult:
        return DhanLtpResult(
            success=True,
            message="Manual v2 paper check simulated LTP.",
            ltp=100.0,
            exchange_segment=str(payload.get("exchangeSegment") or "NSE_FNO"),
            security_id=str(payload.get("securityId") or ""),
            raw_response={"mode": "manual_v2_paper_check_simulated_ltp"},
        )

    def simulated_resolution(signal: Any) -> SecurityIdResolution:
        return SecurityIdResolution(
            ok=True,
            security_id="57046",
            method="MANUAL_V2_PAPER_CHECK_SIMULATED",
            reason="Manual v2 paper check simulated contract resolution.",
            trading_symbol=f"{signal.symbol} CHECK {int(float(signal.strike or 0))} {signal.option_side or 'CE'}",
            lot_size=75,
        )

    def shared_configured() -> bool:
        return True

    def shared_credentials() -> DhanCredentials:
        return DhanCredentials(
            client_id="manual-paper-data-client",
            access_token="manual-paper-data-token",
            source="manual_v2_paper_check",
        )

    def shared_status() -> dict[str, Any]:
        return {
            "configured": True,
            "enabled": True,
            "has_token": True,
            "client_id_masked": "****************lient",
            "token_valid": True,
            "last_error": None,
            "source": "manual_v2_paper_check",
        }

    risk_manager._market_is_open = always_open
    execution_router._market_is_open = always_open
    execution_router.resolve_security_id = simulated_resolution
    paper_broker._market_is_open = always_open
    paper_broker.PaperBroker._ltp = simulated_ltp
    shared_market_data.shared_market_data_configured = shared_configured
    shared_market_data.get_shared_market_credentials = shared_credentials
    shared_market_data.shared_market_data_status = shared_status
    return originals


def _restore_simulated_paper_market_data(originals: dict[str, Any]) -> None:
    from app.services import execution_router, paper_broker, risk_manager, shared_market_data

    risk_manager._market_is_open = originals["risk_market_is_open"]
    execution_router._market_is_open = originals["execution_market_is_open"]
    execution_router.resolve_security_id = originals["execution_resolve_security_id"]
    paper_broker._market_is_open = originals["paper_market_is_open"]
    paper_broker.PaperBroker._ltp = originals["paper_ltp"]
    shared_market_data.shared_market_data_configured = originals["shared_configured"]
    shared_market_data.get_shared_market_credentials = originals["shared_credentials"]
    shared_market_data.shared_market_data_status = originals["shared_status"]


def _read_explicit_engine_mode_without_init() -> str | None:
    path = state_store.scoped_runtime_path(state_store.APP_STATE_FILE)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    mode = str(data.get("engine_mode") or "").strip().lower()
    return mode if mode in {"paper", "live"} else None


def _fallback_engine_mode() -> str:
    return "live" if str(settings.DHAN_MODE).upper() == "REAL" else "paper"


def _ensure_test_user(db: Any, email: str) -> models.User:
    user = crud.upsert_google_user(
        db,
        google_sub=TEST_GOOGLE_SUB,
        email=email,
        name="Manual V2 Paper Check",
        picture_url=None,
        is_admin=False,
    )
    return user


def _ensure_supertrend_catalog_version(db: Any) -> tuple[models.StrategyCatalog, models.StrategyVersion]:
    catalog = db.scalar(select(models.StrategyCatalog).where(models.StrategyCatalog.code == SUPERTREND_CODE))
    if catalog is None:
        catalog = models.StrategyCatalog(
            code=SUPERTREND_CODE,
            name="Supertrend",
            status=StrategyCatalogStatus.ACTIVE.value,
            source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
            metadata_json={
                "aliases": ["supertrend", "TRADINGVIEW_NIFTY_V1"],
                "legacy_strategy_names": ["supertrend"],
            },
        )
        db.add(catalog)
        db.flush()
    else:
        catalog.status = StrategyCatalogStatus.ACTIVE.value
        catalog.source_type = StrategySourceType.NOVA_OWNED_TRADINGVIEW.value
        metadata = catalog.metadata_json if isinstance(catalog.metadata_json, dict) else {}
        aliases = set(metadata.get("aliases") or [])
        aliases.update({"supertrend", "TRADINGVIEW_NIFTY_V1"})
        metadata["aliases"] = sorted(aliases)
        metadata.setdefault("legacy_strategy_names", ["supertrend"])
        catalog.metadata_json = metadata

    version = db.scalar(
        select(models.StrategyVersion).where(
            models.StrategyVersion.strategy_id == catalog.id,
            models.StrategyVersion.version == SUPERTREND_VERSION,
        )
    )
    if version is None:
        version = models.StrategyVersion(
            strategy_id=catalog.id,
            version=SUPERTREND_VERSION,
            status=StrategyCatalogStatus.ACTIVE.value,
            payload_version="nova.v1",
        )
        db.add(version)
        db.flush()
    else:
        version.status = StrategyCatalogStatus.ACTIVE.value
        version.payload_version = "nova.v1"
    db.flush()
    return catalog, version


def _ensure_single_test_paper_instance(
    db: Any,
    *,
    user: models.User,
    catalog: models.StrategyCatalog,
    version: models.StrategyVersion,
) -> models.UserStrategyInstance:
    active_real = db.scalars(
        select(models.UserStrategyInstance).where(
            models.UserStrategyInstance.strategy_id == catalog.id,
            models.UserStrategyInstance.status == StrategyInstanceStatus.ACTIVE.value,
            models.UserStrategyInstance.execution_mode == StrategyExecutionMode.REAL_ORDERS.value,
        )
    ).all()
    if active_real:
        raise ManualCheckError("Active real_orders strategy instances exist; refusing manual paper run.")

    active_paper = db.scalars(
        select(models.UserStrategyInstance).where(
            models.UserStrategyInstance.strategy_id == catalog.id,
            models.UserStrategyInstance.status == StrategyInstanceStatus.ACTIVE.value,
            models.UserStrategyInstance.execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )
    ).all()
    foreign_paper = [instance for instance in active_paper if instance.user_id != user.id]
    if foreign_paper:
        raise ManualCheckError("Another active paper_live_data instance exists; refusing broad fanout.")
    if len(active_paper) > 1:
        raise ManualCheckError("Multiple active paper_live_data instances exist; refusing broad fanout.")

    test_instance = next((instance for instance in active_paper if instance.user_id == user.id), None)
    if test_instance is not None:
        test_instance.strategy_version_id = version.id
        test_instance.instance_label = test_instance.instance_label or TEST_INSTANCE_LABEL
        test_instance.lots = max(int(test_instance.lots or 1), 1)
        db.flush()
        return test_instance

    instance = models.UserStrategyInstance(
        user_id=user.id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        instance_label=TEST_INSTANCE_LABEL,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=StrategyInstanceStatus.ACTIVE.value,
        execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
        lots=1,
        config_json={"created_by": "manual_v2_paper_fanout_check"},
    )
    db.add(instance)
    db.flush()
    return instance


def _result_ok(result: Any) -> bool:
    data = _result_dict(result)
    return bool(data.get("ok"))


def _result_dict(result: Any) -> dict[str, Any]:
    data = result.as_dict() if hasattr(result, "as_dict") else result
    return _sanitize(data if isinstance(data, dict) else {"value": data})


def _payload_preview(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal_id": payload.get("signal_id"),
        "strategy_code": payload.get("strategy_code"),
        "action": payload.get("action"),
        "intent": payload.get("intent"),
        "symbol": payload.get("symbol"),
        "option_side": payload.get("option_side"),
        "strike": payload.get("strike"),
        "expiry": payload.get("expiry"),
        "lots": payload.get("lots"),
    }


def _emit(out: Callable[[str], None], label: str, payload: Any) -> None:
    out(json.dumps({"event": label, "data": _sanitize(payload)}, sort_keys=True))


def _safe_exception_message(exc: Exception) -> str:
    module_name = type(exc).__module__.split(".", 1)[0]
    if module_name in {"sqlalchemy", "psycopg", "psycopg2"}:
        return "Database operation failed; verify DATABASE_URL credentials and connectivity."
    return str(exc)


def _sanitize(value: Any) -> Any:
    redacted_keys = {
        "access_token",
        "client_id",
        "headers",
        "payload",
        "proxy_url",
        "raw_payload",
        "raw_response",
        "request",
        "response_json",
        "response_text",
        "secret",
    }
    if hasattr(value, "__dataclass_fields__"):
        return _sanitize(asdict(value))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in redacted_keys:
                continue
            sanitized[str(key)] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
