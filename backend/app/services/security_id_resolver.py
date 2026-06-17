from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import BACKEND_DIR, DEFAULT_EXCHANGE_SEGMENT, RUNTIME_STATE_DIR, settings
from app.schemas.signal import NormalizedSignal


logger = logging.getLogger("security_id_resolver")

DEFAULT_SECURITY_ID_WARNING = "Using DEFAULT_SECURITY_ID override. Ensure it matches the intended option contract."

# NIFTY underlying security ID in Dhan scrip master.
# This is the index ID (used for quoting), NOT valid for option order placement.
# Any resolution that returns this ID for an option order should be blocked.
NIFTY_UNDERLYING_ID = "13"
NIFTY_UNDERLYING_ID_WARNING = (
    "Security ID '13' is the NIFTY underlying index ID, not a valid option contract ID. "
    "This ID must NOT be used for option order placement. "
    "Resolve the correct option security ID from the scrip master."
)


@dataclass(frozen=True)
class SecurityIdResolution:
    ok: bool
    security_id: str | None
    method: str
    reason: str
    trading_symbol: str | None = None
    lot_size: int | None = None
    source_path: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "security_id": self.security_id,
            "method": self.method,
            "reason": self.reason,
            "trading_symbol": self.trading_symbol,
            "lot_size": self.lot_size,
            "source_path": self.source_path,
        }


@dataclass(frozen=True)
class OptionContractSuggestion:
    symbol: str
    expiry: str
    strike: float
    option_side: str
    security_id: str
    trading_symbol: str | None = None
    lot_size: int | None = None
    reference_price: float | None = None
    method: str = "SCRIP_MASTER_AUTO"
    reason: str = "Auto-selected from local Dhan scrip master."
    source_path: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "expiry": self.expiry,
            "strike": self.strike,
            "optionSide": self.option_side,
            "securityId": self.security_id,
            "tradingSymbol": self.trading_symbol,
            "lotSize": self.lot_size,
            "referencePrice": self.reference_price,
            "method": self.method,
            "reason": self.reason,
            "sourcePath": self.source_path,
        }


def _contract_label(symbol: str | None, expiry: str | None, strike: float | str | None, option_side: str | None) -> str:
    strike_text = ""
    if strike not in (None, ""):
        try:
            strike_float = float(strike)
            strike_text = str(int(strike_float)) if strike_float.is_integer() else str(strike_float)
        except (TypeError, ValueError):
            strike_text = str(strike)
    return " ".join(part for part in [symbol, expiry, strike_text, option_side] if part)


def unresolved_reason(symbol: str | None, expiry: str | None, strike: float | str | None, option_side: str | None) -> str:
    return f"securityId not resolved for {_contract_label(symbol, expiry, strike, option_side)}"


def _value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _normalize_date(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text[:11], fmt).date().isoformat()
        except ValueError:
            continue
    return text[:10]


def _date_from_normalized(value: str | None) -> date | None:
    normalized = _normalize_date(value)
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _parse_float(value: float | str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _strike_key(value: float | str | None) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return ""


def _configured_scrip_master_path() -> Path:
    configured = Path(settings.DHAN_SCRIP_MASTER_PATH)
    if configured.is_absolute():
        return configured
    return BACKEND_DIR / configured


def _candidate_scrip_master_paths() -> list[Path]:
    paths = [
        _configured_scrip_master_path(),
        RUNTIME_STATE_DIR / "api-scrip-master-detailed.csv",
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _row_is_index_option(row: dict[str, Any], signal_exchange_segment: str | None) -> bool:
    instrument = _value(row, "SEM_INSTRUMENT_NAME", "INSTRUMENT", "INSTRUMENT_TYPE").upper()
    if instrument not in {"OPTIDX", "OPT", "OPTSTK"}:
        return False

    segment = _value(row, "SEM_SEGMENT", "SEGMENT").upper()
    exchange = _value(row, "EXCH_ID", "EXCHANGE").upper()
    exchange_segment = (signal_exchange_segment or DEFAULT_EXCHANGE_SEGMENT).upper()

    if exchange_segment in {"NSE_FNO", "NSE_F&O", "NFO"}:
        if exchange and exchange != "NSE":
            return False
        if segment and segment not in {"D", "NSE_FNO", "FNO", "NFO"}:
            return False
    return True


def _row_matches_contract(
    row: dict[str, Any],
    *,
    symbol: str,
    expiry: str,
    strike: float | str,
    option_side: str,
    exchange_segment: str | None,
) -> bool:
    row_symbol = _value(row, "UNDERLYING_SYMBOL", "SM_SYMBOL_NAME", "SYMBOL_NAME").upper()
    if row_symbol != symbol.upper():
        return False
    if not _row_is_index_option(row, exchange_segment):
        return False
    row_expiry = _normalize_date(_value(row, "SEM_EXPIRY_DATE", "SM_EXPIRY_DATE"))
    row_strike = _strike_key(_value(row, "SEM_STRIKE_PRICE", "STRIKE_PRICE"))
    row_option_side = _value(row, "SEM_OPTION_TYPE", "OPTION_TYPE").upper()
    return (
        row_expiry == _normalize_date(expiry)
        and row_strike == _strike_key(strike)
        and row_option_side == option_side.upper()
    )


def _parse_lot_size(row: dict[str, Any]) -> int | None:
    raw = _value(row, "SEM_LOT_UNITS", "LOT_SIZE", "LOT_UNITS")
    if not raw:
        return None
    try:
        lot = int(float(raw))
        return lot if lot > 0 else None
    except (TypeError, ValueError):
        return None


def _guard_nifty_underlying_id(security_id: str | None, *, is_option_order: bool = True) -> tuple[bool, str]:
    """
    Returns (is_blocked, warning_message).
    Blocks if security_id == NIFTY_UNDERLYING_ID and this is an option order.
    """
    if is_option_order and str(security_id) == NIFTY_UNDERLYING_ID:
        return True, NIFTY_UNDERLYING_ID_WARNING
    return False, ""


def _lookup_scrip_master(
    *,
    symbol: str,
    expiry: str,
    strike: float | str,
    option_side: str,
    exchange_segment: str | None,
) -> SecurityIdResolution | None:
    for path in _candidate_scrip_master_paths():
        if not path.exists() or path.stat().st_size == 0:
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if not _row_matches_contract(
                    row,
                    symbol=symbol,
                    expiry=expiry,
                    strike=strike,
                    option_side=option_side,
                    exchange_segment=exchange_segment,
                ):
                    continue
                security_id = _value(row, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
                if not security_id:
                    continue

                # Guard: never return the NIFTY underlying index ID for option orders
                blocked, block_reason = _guard_nifty_underlying_id(security_id, is_option_order=True)
                if blocked:
                    logger.warning("SCRIP_MASTER match returned NIFTY underlying ID %s — blocked. %s", security_id, block_reason)
                    return SecurityIdResolution(
                        ok=False,
                        security_id=None,
                        method="NOT_FOUND",
                        reason=block_reason,
                        source_path=str(path),
                    )

                trading_symbol = _value(row, "SEM_TRADING_SYMBOL", "DISPLAY_NAME", "SEM_CUSTOM_SYMBOL")
                lot_size = _parse_lot_size(row)
                return SecurityIdResolution(
                    ok=True,
                    security_id=security_id,
                    method="SCRIP_MASTER",
                    reason=f"Resolved from local Dhan scrip master: {path}",
                    trading_symbol=trading_symbol or None,
                    lot_size=lot_size,
                    source_path=str(path),
                )
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_security_id_for_contract(
    *,
    symbol: str,
    expiry: str,
    strike: float | str,
    option_side: str,
    exchange_segment: str | None = None,
) -> SecurityIdResolution:
    """
    Resolution order (production-safe):
      1. SCRIP_MASTER  — prefer actual instrument data
      2. DEFAULT_ENV   — allowed only when ALLOW_DEFAULT_SECURITY_ID=true (manual testing)
      3. NOT_FOUND
    """
    if settings.AUTO_RESOLVE_SECURITY_ID:
        match = _lookup_scrip_master(
            symbol=symbol,
            expiry=expiry,
            strike=strike,
            option_side=option_side,
            exchange_segment=exchange_segment,
        )
        if match:
            return match

    if settings.ALLOW_DEFAULT_SECURITY_ID and settings.DEFAULT_SECURITY_ID:
        default_id = str(settings.DEFAULT_SECURITY_ID)
        blocked, block_reason = _guard_nifty_underlying_id(default_id, is_option_order=True)
        if blocked:
            logger.warning("DEFAULT_SECURITY_ID=%s is the NIFTY underlying ID — blocked.", default_id)
            return SecurityIdResolution(
                ok=False,
                security_id=None,
                method="NOT_FOUND",
                reason=block_reason,
            )
        logger.warning("Using DEFAULT_SECURITY_ID override: %s", default_id)
        return SecurityIdResolution(
            ok=True,
            security_id=default_id,
            method="DEFAULT_ENV",
            reason=DEFAULT_SECURITY_ID_WARNING,
        )

    return SecurityIdResolution(
        ok=False,
        security_id=None,
        method="NOT_FOUND",
        reason=unresolved_reason(symbol, expiry, strike, option_side),
    )


def suggest_option_contract(
    *,
    symbol: str = "NIFTY",
    option_side: str,
    exchange_segment: str | None = None,
    reference_price: float | None = None,
    expiry: str | None = None,
    strike: float | str | None = None,
) -> OptionContractSuggestion | None:
    """Suggest a concrete option contract from the local Dhan scrip master.

    This is intentionally data-driven and only suggests contracts already
    present in the scrip master. Callers decide whether auto-selection is safe
    for their mode; live order paths should require explicit user intent.
    """
    target_symbol = str(symbol or "NIFTY").upper()
    target_side = str(option_side or "").upper()
    if target_side not in {"CE", "PE"}:
        return None

    target_expiry = _normalize_date(expiry)
    target_strike = _strike_key(strike)
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    parsed_reference = _parse_float(reference_price)

    indexed = _suggest_option_contract_from_index(
        symbol=target_symbol,
        option_side=target_side,
        target_expiry=target_expiry,
        target_strike=target_strike,
        reference_price=parsed_reference,
        today=today,
    )
    if indexed is not None:
        return indexed

    candidates: list[tuple[date, float, dict[str, Any], Path]] = []

    for path in _candidate_scrip_master_paths():
        if not path.exists() or path.stat().st_size == 0:
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                row_symbol = _value(row, "UNDERLYING_SYMBOL", "SM_SYMBOL_NAME", "SYMBOL_NAME").upper()
                if row_symbol != target_symbol:
                    continue
                if not _row_is_index_option(row, exchange_segment):
                    continue
                if _value(row, "SEM_OPTION_TYPE", "OPTION_TYPE").upper() != target_side:
                    continue

                row_expiry_text = _normalize_date(_value(row, "SEM_EXPIRY_DATE", "SM_EXPIRY_DATE"))
                row_expiry_date = _date_from_normalized(row_expiry_text)
                if row_expiry_date is None or row_expiry_date < today:
                    continue
                if target_expiry and row_expiry_text != target_expiry:
                    continue

                row_strike = _parse_float(_value(row, "SEM_STRIKE_PRICE", "STRIKE_PRICE"))
                if row_strike is None:
                    continue
                if target_strike and _strike_key(row_strike) != target_strike:
                    continue

                security_id = _value(row, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
                if not security_id:
                    continue
                blocked, _ = _guard_nifty_underlying_id(security_id, is_option_order=True)
                if blocked:
                    continue

                candidates.append((row_expiry_date, row_strike, row, path))

    if not candidates:
        return None

    nearest_expiry = min(item[0] for item in candidates)
    same_expiry = [item for item in candidates if item[0] == nearest_expiry]
    if parsed_reference is None:
        strikes = sorted({item[1] for item in same_expiry})
        parsed_reference = strikes[len(strikes) // 2] if strikes else None

    chosen_expiry, chosen_strike, chosen_row, chosen_path = min(
        same_expiry,
        key=lambda item: (abs(item[1] - (parsed_reference if parsed_reference is not None else item[1])), item[1]),
    )
    trading_symbol = _value(chosen_row, "SEM_TRADING_SYMBOL", "SYMBOL_NAME", "DISPLAY_NAME", "SEM_CUSTOM_SYMBOL")
    lot_size = _parse_lot_size(chosen_row)
    security_id = _value(chosen_row, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
    return OptionContractSuggestion(
        symbol=target_symbol,
        expiry=chosen_expiry.isoformat(),
        strike=chosen_strike,
        option_side=target_side,
        security_id=security_id,
        trading_symbol=trading_symbol or None,
        lot_size=lot_size,
        reference_price=parsed_reference,
        reason=(
            "Auto-selected nearest-expiry option from local Dhan scrip master"
            + (" using latest NIFTY reference price." if reference_price is not None else " for local paper testing.")
        ),
        source_path=str(chosen_path),
    )


def _suggest_option_contract_from_index(
    *,
    symbol: str,
    option_side: str,
    target_expiry: str,
    target_strike: str,
    reference_price: float | None,
    today: date,
) -> OptionContractSuggestion | None:
    try:
        from app.services.instrument_resolver import list_option_contracts

        contracts = list_option_contracts(symbol=symbol, option_side=option_side)
    except Exception:
        return None
    candidates: list[Any] = []
    for contract in contracts:
        expiry_date = _date_from_normalized(contract.expiry)
        if expiry_date is None or expiry_date < today:
            continue
        if target_expiry and contract.expiry != target_expiry:
            continue
        if target_strike and _strike_key(contract.strike) != target_strike:
            continue
        candidates.append(contract)

    if not candidates:
        return None

    nearest_expiry = min(_date_from_normalized(item.expiry) for item in candidates)
    same_expiry = [item for item in candidates if _date_from_normalized(item.expiry) == nearest_expiry]
    if reference_price is None:
        strikes = sorted({float(item.strike) for item in same_expiry})
        reference_price = strikes[len(strikes) // 2] if strikes else None
    chosen = min(
        same_expiry,
        key=lambda item: (abs(float(item.strike) - (reference_price if reference_price is not None else float(item.strike))), float(item.strike)),
    )
    return OptionContractSuggestion(
        symbol=symbol,
        expiry=chosen.expiry,
        strike=float(chosen.strike),
        option_side=option_side,
        security_id=chosen.security_id,
        trading_symbol=chosen.trading_symbol,
        lot_size=chosen.lot_size,
        reference_price=reference_price,
        reason=(
            "Auto-selected nearest-expiry option from warmed Dhan instrument index"
            + (" using latest NIFTY reference price." if reference_price is not None else " for local paper testing.")
        ),
        source_path="instrument_index",
    )


def resolve_security_id(signal: NormalizedSignal) -> SecurityIdResolution:
    """
    Resolution order (production-safe):
      1. PROVIDED_IN_SIGNAL — if signal carries securityId
      2. SCRIP_MASTER       — scrip master CSV lookup
      3. DEFAULT_ENV        — only if ALLOW_DEFAULT_SECURITY_ID=true (controlled manual testing)
      4. NOT_FOUND
    """
    # Step 1: signal carries security_id
    if signal.security_id:
        provided_id = str(signal.security_id)
        # Guard: never accept the NIFTY underlying ID for an option order
        blocked, block_reason = _guard_nifty_underlying_id(provided_id, is_option_order=True)
        if blocked:
            logger.warning(
                "Signal provided security_id=%s which is the NIFTY underlying ID — blocked for option order.",
                provided_id,
            )
            return SecurityIdResolution(
                ok=False,
                security_id=None,
                method="NOT_FOUND",
                reason=block_reason,
                trading_symbol=signal.trading_symbol,
            )
        if (
            signal.symbol
            and signal.expiry
            and signal.strike is not None
            and signal.option_side
            and settings.AUTO_RESOLVE_SECURITY_ID
        ):
            match = _lookup_scrip_master(
                symbol=signal.symbol,
                expiry=signal.expiry,
                strike=signal.strike,
                option_side=signal.option_side,
                exchange_segment=signal.exchange_segment,
            )
            if match and str(match.security_id) == provided_id:
                return SecurityIdResolution(
                    ok=True,
                    security_id=provided_id,
                    method="PROVIDED_IN_SIGNAL",
                    reason="securityId provided by normalized signal and verified against local Dhan scrip master.",
                    trading_symbol=match.trading_symbol or signal.trading_symbol,
                    lot_size=match.lot_size,
                    source_path=match.source_path,
                )
        return SecurityIdResolution(
            ok=True,
            security_id=provided_id,
            method="PROVIDED_IN_SIGNAL",
            reason="securityId provided by normalized signal.",
            trading_symbol=signal.trading_symbol,
        )

    # Step 2: scrip master lookup (BEFORE DEFAULT_ENV — production safety)
    if (
        signal.symbol
        and signal.expiry
        and signal.strike is not None
        and signal.option_side
        and settings.AUTO_RESOLVE_SECURITY_ID
    ):
        match = _lookup_scrip_master(
            symbol=signal.symbol,
            expiry=signal.expiry,
            strike=signal.strike,
            option_side=signal.option_side,
            exchange_segment=signal.exchange_segment,
        )
        if match:
            return match

    # Step 3: DEFAULT_ENV override (controlled manual testing only)
    if settings.ALLOW_DEFAULT_SECURITY_ID and settings.DEFAULT_SECURITY_ID:
        default_id = str(settings.DEFAULT_SECURITY_ID)
        blocked, block_reason = _guard_nifty_underlying_id(default_id, is_option_order=True)
        if blocked:
            logger.warning("DEFAULT_SECURITY_ID=%s is the NIFTY underlying ID — blocked.", default_id)
            return SecurityIdResolution(
                ok=False,
                security_id=None,
                method="NOT_FOUND",
                reason=block_reason,
                trading_symbol=signal.trading_symbol,
            )
        logger.warning("Using DEFAULT_SECURITY_ID override: %s", default_id)
        return SecurityIdResolution(
            ok=True,
            security_id=default_id,
            method="DEFAULT_ENV",
            reason=DEFAULT_SECURITY_ID_WARNING,
            trading_symbol=signal.trading_symbol,
        )

    # Step 4: not found
    return SecurityIdResolution(
        ok=False,
        security_id=None,
        method="NOT_FOUND",
        reason=unresolved_reason(signal.symbol, signal.expiry, signal.strike, signal.option_side),
        trading_symbol=signal.trading_symbol,
    )
