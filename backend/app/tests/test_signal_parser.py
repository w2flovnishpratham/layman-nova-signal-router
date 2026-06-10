from app.services.signal_parser import parse_webhook_payload


TEST_WEBHOOK_SECRET = "nova-7Kx9mQ2pL8sZ-2026"


def nova_payload(action="ENTRY", side="BUY", signal_id="nova-001"):
    return {
        "secret": TEST_WEBHOOK_SECRET,
        "signal_id": signal_id,
        "strategy_code": "TRADINGVIEW_NIFTY_V1",
        "action": action,
        "side": side,
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "exchange_segment": "NSE_FNO",
        "security_id": "123456",
        "trading_symbol": "NIFTY 28 MAY 22500 CALL",
        "option_side": "CE",
        "strike": 22500,
        "expiry": "2026-05-28",
        "qty": 1,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
        "source": "tradingview",
    }


def pine_payload(transaction_type="B", quantity="1", signal_id=None):
    payload = {
        "secret": TEST_WEBHOOK_SECRET,
        "alertType": "multi_leg_order",
        "order_legs": [
            {
                "transactionType": transaction_type,
                "orderType": "MKT",
                "quantity": quantity,
                "exchange": "NSE",
                "symbol": "NIFTY",
                "instrument": "OPT",
                "productType": "I",
                "sort_order": "1",
                "price": "0",
                "option_type": "CE",
                "strike_price": "22500.0",
                "expiry_date": "2026-05-28",
            }
        ],
    }
    if signal_id:
        payload["signal_id"] = signal_id
    return payload


def test_nova_entry_payload_parses_to_entry_buy():
    signal = parse_webhook_payload(nova_payload("ENTRY", "BUY", "nova-entry-001"))

    assert signal.payload_format == "NOVA"
    assert signal.action == "ENTRY"
    assert signal.side == "BUY"


def test_nova_v3_sr_fields_are_preserved_for_exit_suggestion():
    payload = nova_payload("ENTRY", "BUY", "nova-v3-entry")
    payload.update(
        {
            "strategy_code": "TRADINGVIEW_NIFTY_V3",
            "sl_level": 23450,
            "tp_level": 23600,
            "nifty_price": 23500,
            "delta": 0.5,
        }
    )

    signal = parse_webhook_payload(payload)

    assert signal.strategy_code == "TRADINGVIEW_NIFTY_V3"
    assert signal.raw_payload["sl_level"] == 23450
    assert signal.raw_payload["tp_level"] == 23600
    assert signal.raw_payload["nifty_price"] == 23500
    assert signal.raw_payload["delta"] == 0.5


def test_nova_exit_payload_parses_to_exit_sell():
    signal = parse_webhook_payload(nova_payload("EXIT", "SELL", "nova-exit-001"))

    assert signal.payload_format == "NOVA"
    assert signal.action == "EXIT"
    assert signal.side == "SELL"


def test_pine_transaction_b_parses_to_entry_buy():
    signal = parse_webhook_payload(pine_payload("B"))

    assert signal.payload_format == "PINE_MULTI_LEG"
    assert signal.action == "ENTRY"
    assert signal.side == "BUY"


def test_pine_transaction_s_parses_to_exit_sell():
    signal = parse_webhook_payload(pine_payload("S"))

    assert signal.payload_format == "PINE_MULTI_LEG"
    assert signal.action == "EXIT"
    assert signal.side == "SELL"


def test_pine_mkt_maps_to_market_and_product_i_maps_to_intraday():
    signal = parse_webhook_payload(pine_payload("B"))

    assert signal.order_type == "MARKET"
    assert signal.product_type == "INTRADAY"


def test_pine_qty_string_and_strike_price_normalize():
    signal = parse_webhook_payload(pine_payload("B", quantity="1"))

    assert signal.qty == 1
    assert isinstance(signal.qty, int)
    assert signal.strike == 22500.0


def test_generated_pine_signal_id_is_stable_within_current_minute():
    first = parse_webhook_payload(pine_payload("B"))
    second = parse_webhook_payload(pine_payload("B"))

    assert first.signal_id == second.signal_id
