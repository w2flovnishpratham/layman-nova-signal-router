from __future__ import annotations

import asyncio
import json
import logging
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import websockets

from app.services.shared_market_data import (
    market_data_credentials,
    refresh_shared_token_after_auth_failure,
)
from app.services.state_store import utc_now


logger = logging.getLogger("dhan_marketfeed_ws")

DHAN_MARKETFEED_WS_HOST = "api-feed.dhan.co"
DHAN_MARKETFEED_WS_URL = f"wss://{DHAN_MARKETFEED_WS_HOST}"

SUBSCRIBE_TICKER_REQUEST_CODE = 15
UNSUBSCRIBE_TICKER_REQUEST_CODE = 16
DISCONNECT_REQUEST_CODE = 12

TICKER_RESPONSE_CODE = 2
QUOTE_RESPONSE_CODE = 4
FULL_RESPONSE_CODE = 8
FEED_DISCONNECT_RESPONSE_CODE = 50

EXCHANGE_SEGMENT_CODES = {
    "IDX_I": 0,
    "NSE_EQ": 1,
    "NSE_FNO": 2,
    "NSE_CURRENCY": 3,
    "BSE_EQ": 4,
    "MCX_COMM": 5,
    "BSE_CURRENCY": 7,
    "BSE_FNO": 8,
}
EXCHANGE_SEGMENT_BY_CODE = {value: key for key, value in EXCHANGE_SEGMENT_CODES.items()}


@dataclass(frozen=True)
class MarketFeedPacket:
    response_code: int
    message_length: int
    exchange_segment_code: int
    exchange_segment: str | None
    security_id: str
    ltp: float | None = None
    last_trade_time: int | None = None
    disconnect_code: int | None = None


@dataclass(frozen=True)
class MarketFeedLtpResult:
    success: bool
    message: str
    ltp: float | None
    exchange_segment: str | None
    security_id: str | None
    received_at: str | None = None
    age_seconds: float | None = None
    source: str = "dhan_marketfeed_ws"
    error: str | None = None
    packet: dict[str, Any] | None = None


def _now_monotonic() -> float:
    return time.monotonic()


def _target_key(exchange_segment: str, security_id: str) -> tuple[str, str]:
    return (str(exchange_segment or "").upper(), str(security_id or "").strip())


def _targets_for_status(targets: set[tuple[str, str]] | None) -> list[dict[str, str]]:
    return [
        {"exchange_segment": exchange_segment, "security_id": security_id}
        for exchange_segment, security_id in sorted(targets or set())
    ]


def _subscription_message_many(request_code: int, targets: set[tuple[str, str]]) -> str:
    ordered = sorted(target for target in targets if target[0] and target[1])
    payload = {
        "RequestCode": request_code,
        "InstrumentCount": len(ordered),
        "InstrumentList": [
            {
                "ExchangeSegment": exchange_segment,
                "SecurityId": str(security_id),
            }
            for exchange_segment, security_id in ordered
        ],
    }
    return json.dumps(payload, separators=(",", ":"))


def _subscription_message(request_code: int, exchange_segment: str, security_id: str) -> str:
    target = _target_key(exchange_segment, security_id)
    payload = {
        "RequestCode": request_code,
        "InstrumentCount": 1,
        "InstrumentList": [
            {
                "ExchangeSegment": target[0],
                "SecurityId": target[1],
            }
        ],
    }
    return json.dumps(payload, separators=(",", ":"))


def parse_marketfeed_packet(message: bytes) -> MarketFeedPacket | None:
    """
    Parse Dhan v2 market-feed binary packets.

    Official docs define an 8-byte little-endian header:
      byte 0: response code
      bytes 1-2: int16 message length
      byte 3: exchange segment enum
      bytes 4-7: int32 security id

    Ticker/Quote/Full packets expose LTP as float32 at offset 8.
    """
    if len(message) < 8:
        return None

    response_code = message[0]
    message_length = struct.unpack_from("<H", message, 1)[0]
    exchange_segment_code = message[3]
    security_id = str(struct.unpack_from("<i", message, 4)[0])
    exchange_segment = EXCHANGE_SEGMENT_BY_CODE.get(exchange_segment_code)

    if response_code in {TICKER_RESPONSE_CODE, QUOTE_RESPONSE_CODE, FULL_RESPONSE_CODE} and len(message) >= 12:
        ltp = round(float(struct.unpack_from("<f", message, 8)[0]), 4)
        last_trade_time = struct.unpack_from("<i", message, 12)[0] if len(message) >= 16 else None
        return MarketFeedPacket(
            response_code=response_code,
            message_length=message_length,
            exchange_segment_code=exchange_segment_code,
            exchange_segment=exchange_segment,
            security_id=security_id,
            ltp=ltp,
            last_trade_time=last_trade_time,
        )

    if response_code == FEED_DISCONNECT_RESPONSE_CODE:
        disconnect_code = struct.unpack_from("<H", message, 8)[0] if len(message) >= 10 else None
        return MarketFeedPacket(
            response_code=response_code,
            message_length=message_length,
            exchange_segment_code=exchange_segment_code,
            exchange_segment=exchange_segment,
            security_id=security_id,
            disconnect_code=disconnect_code,
        )

    return MarketFeedPacket(
        response_code=response_code,
        message_length=message_length,
        exchange_segment_code=exchange_segment_code,
        exchange_segment=exchange_segment,
        security_id=security_id,
    )


class DhanMarketFeedWsManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._desired_targets: set[tuple[str, str]] = set()
        self._active_targets: set[tuple[str, str]] = set()
        self._connected = False
        self._last_error: str | None = None
        self._last_connected_at: str | None = None
        self._last_message_at: str | None = None
        self._last_subscribed_at: str | None = None
        self._reconnect_count = 0
        self._ticks: dict[tuple[str, str], dict[str, Any]] = {}

    def ensure_subscription(self, *, exchange_segment: str, security_id: str) -> None:
        target = _target_key(exchange_segment, security_id)
        if not target[0] or not target[1]:
            return
        with self._lock:
            self._desired_targets.add(target)
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_thread, name="dhan-marketfeed-ws", daemon=True)
            self._thread.start()

    def ensure_subscriptions(self, targets: list[dict[str, str]] | set[tuple[str, str]]) -> None:
        normalized: set[tuple[str, str]] = set()
        for item in targets:
            if isinstance(item, tuple):
                target = _target_key(item[0], item[1])
            else:
                target = _target_key(item.get("exchange_segment", ""), item.get("security_id", ""))
            if target[0] and target[1]:
                normalized.add(target)
        if not normalized:
            return
        with self._lock:
            self._desired_targets.update(normalized)
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_thread, name="dhan-marketfeed-ws", daemon=True)
            self._thread.start()

    def clear_subscription(self) -> None:
        with self._lock:
            self._desired_targets.clear()
            self._active_targets.clear()

    def stop(self, timeout: float = 2.0) -> None:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
            self._desired_targets.clear()
        if thread:
            thread.join(timeout=timeout)

    def latest_ltp(self, *, exchange_segment: str, security_id: str, max_age_seconds: float) -> MarketFeedLtpResult:
        key = _target_key(exchange_segment, security_id)
        with self._lock:
            item = dict(self._ticks.get(key) or {})
            status = self.status()

        if not item:
            return MarketFeedLtpResult(
                success=False,
                message="Waiting for first Dhan market-feed WebSocket tick.",
                ltp=None,
                exchange_segment=key[0],
                security_id=key[1],
                error="ws_tick_missing",
                packet={"ws_status": status},
            )

        age_seconds = round(_now_monotonic() - float(item["received_monotonic"]), 3)
        if age_seconds > max_age_seconds:
            return MarketFeedLtpResult(
                success=False,
                message=f"Dhan market-feed WebSocket tick is stale ({age_seconds}s old).",
                ltp=item.get("ltp"),
                exchange_segment=key[0],
                security_id=key[1],
                received_at=item.get("received_at"),
                age_seconds=age_seconds,
                error="ws_tick_stale",
                packet={"ws_status": status, "last_tick": item},
            )

        return MarketFeedLtpResult(
            success=True,
            message="Dhan market-feed WebSocket LTP received.",
            ltp=float(item["ltp"]),
            exchange_segment=key[0],
            security_id=key[1],
            received_at=item.get("received_at"),
            age_seconds=age_seconds,
            packet={k: v for k, v in item.items() if k != "received_monotonic"},
        )

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "source": "dhan_marketfeed_ws",
                "thread_alive": bool(self._thread and self._thread.is_alive()),
                "connected": self._connected,
                "desired_targets": _targets_for_status(self._desired_targets),
                "active_targets": _targets_for_status(self._active_targets),
                "desired_target": next(iter(sorted(self._desired_targets)), None),
                "active_target": next(iter(sorted(self._active_targets)), None),
                "last_error": self._last_error,
                "last_connected_at": self._last_connected_at,
                "last_message_at": self._last_message_at,
                "last_subscribed_at": self._last_subscribed_at,
                "reconnect_count": self._reconnect_count,
            }

    def _run_thread(self) -> None:
        asyncio.run(self._run_loop())

    async def _run_loop(self) -> None:
        backoff_seconds = 1.0
        while not self._stop_event.is_set():
            targets = self._get_desired_targets()
            if not targets:
                await asyncio.sleep(0.5)
                continue
            try:
                await self._connect_and_stream(targets)
                backoff_seconds = 1.0
            except Exception as exc:
                message = str(exc)
                refresh_shared_token_after_auth_failure(message=message)
                logger.warning("Dhan market-feed WebSocket disconnected: %s", message)
                with self._lock:
                    self._connected = False
                    self._active_targets.clear()
                    self._last_error = message
                    self._reconnect_count += 1
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 15.0)

    async def _connect_and_stream(self, initial_targets: set[tuple[str, str]]) -> None:
        creds = market_data_credentials()
        if not creds:
            with self._lock:
                self._last_error = "missing_dhan_credentials"
            await asyncio.sleep(2.0)
            return

        encoded_token = quote(creds.access_token, safe="")
        url = f"{DHAN_MARKETFEED_WS_URL}?version=2&token={encoded_token}&clientId={creds.client_id}&authType=2"

        async with websockets.connect(url, ping_interval=None, close_timeout=5) as ws:
            with self._lock:
                self._connected = True
                self._active_targets.clear()
                self._last_connected_at = utc_now()
                self._last_error = None

            active_targets = set(initial_targets)
            await ws.send(_subscription_message_many(SUBSCRIBE_TICKER_REQUEST_CODE, active_targets))
            with self._lock:
                self._active_targets = set(active_targets)
                self._last_subscribed_at = utc_now()

            while not self._stop_event.is_set():
                desired = self._get_desired_targets()
                if not desired:
                    try:
                        await ws.send(_subscription_message_many(UNSUBSCRIBE_TICKER_REQUEST_CODE, active_targets))
                    except Exception:
                        pass
                    return

                to_unsubscribe = active_targets - desired
                if to_unsubscribe:
                    try:
                        await ws.send(_subscription_message_many(UNSUBSCRIBE_TICKER_REQUEST_CODE, to_unsubscribe))
                    except Exception:
                        pass
                    active_targets -= to_unsubscribe
                    with self._lock:
                        self._active_targets = set(active_targets)

                to_subscribe = desired - active_targets
                if to_subscribe:
                    await ws.send(_subscription_message_many(SUBSCRIBE_TICKER_REQUEST_CODE, to_subscribe))
                    active_targets |= to_subscribe
                    with self._lock:
                        self._active_targets = set(active_targets)
                        self._last_subscribed_at = utc_now()

                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                if isinstance(message, bytes):
                    self._handle_binary(message)
                else:
                    if self._handle_text(str(message)):
                        return

    def _get_desired_targets(self) -> set[tuple[str, str]]:
        with self._lock:
            return set(self._desired_targets)

    def _handle_binary(self, message: bytes) -> None:
        packet = parse_marketfeed_packet(message)
        if not packet:
            return
        with self._lock:
            self._last_message_at = utc_now()
            if packet.ltp is not None and packet.exchange_segment:
                key = _target_key(packet.exchange_segment, packet.security_id)
                self._ticks[key] = {
                    "ltp": packet.ltp,
                    "exchange_segment": packet.exchange_segment,
                    "security_id": packet.security_id,
                    "response_code": packet.response_code,
                    "message_length": packet.message_length,
                    "last_trade_time": packet.last_trade_time,
                    "received_at": utc_now(),
                    "received_monotonic": _now_monotonic(),
                }
            elif packet.response_code == FEED_DISCONNECT_RESPONSE_CODE:
                self._last_error = f"feed_disconnect_{packet.disconnect_code}"

    def _handle_text(self, message: str) -> bool:
        safe_message = message[:500]
        with self._lock:
            self._last_message_at = utc_now()
            self._last_error = safe_message
        return refresh_shared_token_after_auth_failure(message=safe_message)


_MANAGER = DhanMarketFeedWsManager()


def ensure_marketfeed_subscription(*, exchange_segment: str, security_id: str) -> None:
    _MANAGER.ensure_subscription(exchange_segment=exchange_segment, security_id=security_id)


def ensure_marketfeed_subscriptions(targets: list[dict[str, str]] | set[tuple[str, str]]) -> None:
    _MANAGER.ensure_subscriptions(targets)


def clear_marketfeed_subscription() -> None:
    _MANAGER.clear_subscription()


def stop_marketfeed_ws(timeout: float = 2.0) -> None:
    _MANAGER.stop(timeout=timeout)


def get_marketfeed_ltp(*, exchange_segment: str, security_id: str, max_age_seconds: float) -> MarketFeedLtpResult:
    return _MANAGER.latest_ltp(
        exchange_segment=exchange_segment,
        security_id=security_id,
        max_age_seconds=max_age_seconds,
    )


def marketfeed_ws_status() -> dict[str, Any]:
    return _MANAGER.status()
