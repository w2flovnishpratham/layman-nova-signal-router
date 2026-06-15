# TradingView Webhook Format

## Endpoint

```
POST http://your-domain/webhook/tradingview
Content-Type: application/json
```

## Entry Alert JSON

```json
{
  "secret": "change_me_webhook_secret",
  "signal_id": "{{timenow}}-{{ticker}}-ENTRY",
  "strategy_code": "TRADINGVIEW_NIFTY_V1",
  "action": "ENTRY",
  "side": "BUY",
  "symbol": "NIFTY",
  "instrument_type": "OPTIDX",
  "exchange_segment": "NSE_FNO",
  "security_id": "123456",
  "trading_symbol": "NIFTY 28 MAY 22500 CALL",
  "option_side": "CE",
  "strike": 22500,
  "expiry": "2026-05-28",
  "qty": 75,
  "order_type": "MARKET",
  "product_type": "INTRADAY",
  "source": "tradingview"
}
```

## Exit Alert JSON

```json
{
  "secret": "change_me_webhook_secret",
  "signal_id": "{{timenow}}-{{ticker}}-EXIT",
  "strategy_code": "TRADINGVIEW_NIFTY_V1",
  "action": "EXIT",
  "side": "SELL",
  "symbol": "NIFTY",
  "instrument_type": "OPTIDX",
  "exchange_segment": "NSE_FNO",
  "security_id": "123456",
  "trading_symbol": "NIFTY 28 MAY 22500 CALL",
  "qty": 75,
  "order_type": "MARKET",
  "product_type": "INTRADAY",
  "source": "tradingview"
}
```

## Required Fields

| Field           | Required | Description                          |
|-----------------|----------|--------------------------------------|
| secret          | YES      | Must match the webhook secret saved in setup UI |
| signal_id       | YES      | Unique ID per alert (use {{timenow}})|
| strategy_code   | YES      | Must match a known strategy          |
| action          | YES      | ENTRY or EXIT                        |
| side            | YES      | BUY or SELL                          |
| security_id     | YES      | Dhan instrument security ID          |
| trading_symbol  | YES      | Human-readable symbol                |
| qty             | YES      | Number of lots/units                 |

## Validation Rules

1. `secret` must match the server-side webhook secret saved during setup
2. `action` must be `ENTRY` or `EXIT`
3. `side` must be `BUY` or `SELL`
4. `signal_id` must be unique — duplicate IDs are silently ignored
5. `strategy_code` must exist in the database
6. `qty` must be > 0

## TradingView Pine Script Setup

In TradingView → Alert → Webhook:

1. Set URL to `https://your-domain/webhook/tradingview`
2. Set Message to the JSON template above
3. Use `{{timenow}}-{{ticker}}-ENTRY` as `signal_id` for uniqueness
4. Use the same `secret` saved from the frontend setup screen

## Entry-Only Pine With Server-Side Option SL/TP

For option premium SL/TP, use `backend/tradingview_entry_only_server_exit.pine`.

The flow is:

1. Pine watches the NIFTY chart and sends only an `ENTRY` alert.
2. Backend places the Dhan CE/PE market buy order.
3. Backend polls Dhan order status and stores the actual option fill price.
4. Backend subscribes to Dhan Market Feed WebSocket ticker data for the option `securityId`.
5. Backend sends a Dhan market sell when the real option premium reaches SL or TP.

Runtime defaults:

| Setting | Default |
|---------|---------|
| `server_side_exit_enabled` | `true` |
| `marketfeed_ws_enabled` | `true` |
| `option_ltp_source` | `WEBSOCKET` |
| `option_ws_stale_seconds` | `5.0` |
| `option_rest_fallback_enabled` | `false` |
| `option_sl_percent` | `10.0` |
| `option_tp_percent` | `20.0` |
| `option_ltp_poll_seconds` | `1.0` |

The monitor only sends automatic exits when `DHAN_MODE=REAL` and `ENABLE_LIVE_ORDERS=true`.

The WebSocket connection uses Dhan's documented endpoint:

```text
wss://api-feed.dhan.co?version=2&token=<token>&clientId=<clientId>&authType=2
```

The backend subscribes with request code `15` for ticker packets. Dhan sends binary little-endian packets; the backend parses the ticker packet LTP from the documented float32 LTP field. REST `/marketfeed/ltp` is available only if explicitly enabled as a fallback.
