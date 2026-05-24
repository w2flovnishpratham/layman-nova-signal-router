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
