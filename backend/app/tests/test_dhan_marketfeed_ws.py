import struct

from app.services.dhan_marketfeed_ws import (
    SUBSCRIBE_TICKER_REQUEST_CODE,
    _subscription_message,
    parse_marketfeed_packet,
)


def test_parse_dhan_ticker_packet_little_endian():
    packet = struct.pack("<BHBifi", 2, 16, 2, 49081, 121.5, 1716880000)

    parsed = parse_marketfeed_packet(packet)

    assert parsed is not None
    assert parsed.response_code == 2
    assert parsed.message_length == 16
    assert parsed.exchange_segment_code == 2
    assert parsed.exchange_segment == "NSE_FNO"
    assert parsed.security_id == "49081"
    assert parsed.ltp == 121.5
    assert parsed.last_trade_time == 1716880000


def test_parse_dhan_feed_disconnect_packet():
    packet = struct.pack("<BHBih", 50, 10, 2, 49081, 805)

    parsed = parse_marketfeed_packet(packet)

    assert parsed is not None
    assert parsed.response_code == 50
    assert parsed.disconnect_code == 805


def test_subscription_message_is_ticker_json_without_credentials():
    message = _subscription_message(SUBSCRIBE_TICKER_REQUEST_CODE, "NSE_FNO", "49081")

    assert '"RequestCode":15' in message
    assert '"InstrumentCount":1' in message
    assert '"ExchangeSegment":"NSE_FNO"' in message
    assert '"SecurityId":"49081"' in message
    assert "token" not in message.lower()
