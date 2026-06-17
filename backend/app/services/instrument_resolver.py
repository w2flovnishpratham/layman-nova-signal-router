from __future__ import annotations

import csv
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import RUNTIME_STATE_DIR
from app.schemas.signal import NormalizedSignal


logger = logging.getLogger("instrument_resolver")

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
CACHE_FILE = RUNTIME_STATE_DIR / "api-scrip-master-detailed.csv"
CACHE_TTL = timedelta(hours=12)
_INDEX_LOCK = threading.RLock()
_INDEX: dict[tuple[str, str, str, str], "InstrumentMatch"] | None = None
_INDEX_MTIME: float | None = None
_WARMUP_STARTED = False


@dataclass(frozen=True)
class InstrumentMatch:
    security_id: str
    trading_symbol: str | None = None
    lot_size: int | None = None


@dataclass(frozen=True)
class InstrumentContract:
    symbol: str
    expiry: str
    strike: float
    option_side: str
    security_id: str
    trading_symbol: str | None = None
    lot_size: int | None = None


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return datetime.now(timezone.utc) - modified < CACHE_TTL


def _ensure_scrip_master(*, allow_download: bool = True) -> Path | None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CACHE_FILE.exists() and CACHE_FILE.stat().st_size > 0:
        if _is_cache_fresh(CACHE_FILE) or not allow_download:
            return CACHE_FILE

    if not allow_download:
        return None

    if _is_cache_fresh(CACHE_FILE):
        return CACHE_FILE

    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        response = client.get(SCRIP_MASTER_URL)
    response.raise_for_status()
    CACHE_FILE.write_text(response.text, encoding="utf-8")
    return CACHE_FILE


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


def _normalize_strike(value: str | float | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _strike_key(value: str | float | None) -> str | None:
    strike = _normalize_strike(value)
    if strike is None:
        return None
    return f"{strike:.3f}"


def _row_key(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    symbol = _value(row, "UNDERLYING_SYMBOL", "SM_SYMBOL_NAME", "SYMBOL_NAME").upper()
    if not symbol:
        return None
    instrument = _value(row, "SEM_INSTRUMENT_NAME", "INSTRUMENT").upper()
    if instrument not in {"OPTIDX", "OPTSTK", "OPT"}:
        return None
    segment = _value(row, "SEM_SEGMENT", "SEGMENT").upper()
    if segment and segment != "D":
        return None
    expiry = _normalize_date(_value(row, "SEM_EXPIRY_DATE", "SM_EXPIRY_DATE"))
    strike = _strike_key(_value(row, "SEM_STRIKE_PRICE", "STRIKE_PRICE"))
    option_type = _value(row, "SEM_OPTION_TYPE", "OPTION_TYPE").upper()
    if not expiry or not strike or option_type not in {"CE", "PE"}:
        return None
    return (symbol, expiry, strike, option_type)


def _row_match(row: dict[str, Any]) -> InstrumentMatch | None:
    security_id = _value(row, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
    if not security_id:
        return None
    trading_symbol = _value(row, "SEM_TRADING_SYMBOL", "DISPLAY_NAME", "SEM_CUSTOM_SYMBOL")
    lot_size = _optional_int(_value(row, "SEM_LOT_UNITS", "LOT_SIZE"))
    return InstrumentMatch(security_id=security_id, trading_symbol=trading_symbol or None, lot_size=lot_size)


def _build_index(path: Path) -> dict[tuple[str, str, str, str], InstrumentMatch]:
    index: dict[tuple[str, str, str, str], InstrumentMatch] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = _row_key(row)
            if not key:
                continue
            match = _row_match(row)
            if match:
                index[key] = match
    return index


def load_instrument_index(*, allow_download: bool = False, force_download: bool = False) -> bool:
    path = _ensure_scrip_master(allow_download=allow_download)
    if path is None:
        return False
    if force_download:
        path = _ensure_scrip_master(allow_download=True)
        if path is None:
            return False

    mtime = path.stat().st_mtime
    with _INDEX_LOCK:
        global _INDEX, _INDEX_MTIME
        if _INDEX is not None and _INDEX_MTIME == mtime:
            return True
        _INDEX = _build_index(path)
        _INDEX_MTIME = mtime
    logger.info("Loaded %s Dhan option instruments from %s", len(_INDEX or {}), path)
    return True


def warm_instrument_cache() -> None:
    try:
        load_instrument_index(allow_download=True, force_download=False)
    except Exception as exc:
        logger.warning("Unable to warm Dhan instrument cache: %s", exc)


def start_instrument_cache_warmup() -> None:
    global _WARMUP_STARTED
    with _INDEX_LOCK:
        if _WARMUP_STARTED:
            return
        _WARMUP_STARTED = True
    thread = threading.Thread(target=warm_instrument_cache, name="dhan-instrument-cache", daemon=True)
    thread.start()


def resolve_option_security_id(signal: NormalizedSignal) -> InstrumentMatch | None:
    if signal.security_id:
        return InstrumentMatch(security_id=str(signal.security_id), trading_symbol=signal.trading_symbol)
    if not signal.symbol or not signal.expiry or signal.strike is None or not signal.option_side:
        return None

    try:
        if not load_instrument_index(allow_download=False):
            start_instrument_cache_warmup()
            logger.warning("Dhan instrument master is not ready yet.")
            return None
    except Exception as exc:
        logger.warning("Unable to read Dhan instrument master: %s", exc)
        return None

    key = (signal.symbol.upper(), _normalize_date(signal.expiry), _strike_key(signal.strike) or "", signal.option_side.upper())
    with _INDEX_LOCK:
        match = (_INDEX or {}).get(key)
    if not match:
        return None
    return InstrumentMatch(
        security_id=match.security_id,
        trading_symbol=match.trading_symbol or signal.trading_symbol,
        lot_size=match.lot_size,
    )


def list_option_contracts(*, symbol: str = "NIFTY", option_side: str | None = None) -> list[InstrumentContract]:
    try:
        if not load_instrument_index(allow_download=False):
            start_instrument_cache_warmup()
            return []
    except Exception as exc:
        logger.warning("Unable to read Dhan instrument master: %s", exc)
        return []

    target_symbol = str(symbol or "").upper()
    target_side = str(option_side or "").upper()
    contracts: list[InstrumentContract] = []
    with _INDEX_LOCK:
        items = list((_INDEX or {}).items())
    for (row_symbol, expiry, strike_key, row_side), match in items:
        if target_symbol and row_symbol != target_symbol:
            continue
        if target_side and row_side != target_side:
            continue
        try:
            strike = float(strike_key)
        except (TypeError, ValueError):
            continue
        contracts.append(
            InstrumentContract(
                symbol=row_symbol,
                expiry=expiry,
                strike=strike,
                option_side=row_side,
                security_id=match.security_id,
                trading_symbol=match.trading_symbol,
                lot_size=match.lot_size,
            )
        )
    return contracts
