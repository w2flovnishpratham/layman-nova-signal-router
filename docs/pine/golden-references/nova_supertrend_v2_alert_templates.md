# NOVA Supertrend v2 alert templates

## Actual private-webhook contract

Endpoint: `POST /api/webhooks/private` with `Content-Type: application/json`. Maximum body size is 4,096 bytes by default.

Required body fields:

| Field | Contract |
| --- | --- |
| `credential` | String, 1-128 characters; resolved to one active personal strategy instance and excluded from serialized payloads |
| `action` | String, 1-20 characters; normalized case-insensitively to exactly `BUY_CE`, `BUY_PE`, `EXIT`, or `HOLD` |
| `signal_id` | String, 1-128 characters; durable idempotency key within the strategy instance |
| `signal_time` | ISO-8601 datetime with timezone; default freshness window 300 seconds and future skew 30 seconds |

Optional audit-only fields are `strategy_version` (40 characters), `timeframe` (12), positive `reference_price`, and `comment` (200). Unknown fields are rejected. The body cannot name owner, broker, execution mode, lots, quantity, strike, expiry, security ID, order type, or product type.

Unknown and revoked credentials receive the same `401 INVALID_CREDENTIAL`. Stopped or non-private instances are rejected. Paused instances authenticate; entries are blocked later while protective exits remain possible. Identical retries return the existing durable status without a second job. Reusing a `signal_id` with different content returns `409 CONFLICTING_DUPLICATE`.

`HOLD` authenticates and creates a completed durable signal audit only. It creates no execution job or broker order and cannot complete paper qualification.

## TradingView message examples

Select **Any alert() function call** in TradingView. The golden Pine builds the message itself from the configured credential, confirmed candle `time_close`, and normalized action. Do not paste a second message template over it. These redacted examples show the exact JSON shape; the ISO timestamp and epoch-millisecond signal-ID component refer to the same confirmed candle close.

```json
{"credential":"<ONE_TIME_PRIVATE_CREDENTIAL>","action":"BUY_CE","signal_id":"NOVA-ST-V2:NIFTY:1784093700000:BUY_CE","signal_time":"2026-07-15T09:35:00Z"}
```

```json
{"credential":"<ONE_TIME_PRIVATE_CREDENTIAL>","action":"BUY_PE","signal_id":"NOVA-ST-V2:NIFTY:1784093700000:BUY_PE","signal_time":"2026-07-15T09:35:00Z"}
```

```json
{"credential":"<ONE_TIME_PRIVATE_CREDENTIAL>","action":"EXIT","signal_id":"NOVA-ST-V2:NIFTY:1784093700000:EXIT","signal_time":"2026-07-15T09:35:00Z"}
```

## HOLD connectivity test

Use HOLD only as a deliberate temporary test. The Pine input sends it once on a confirmed realtime bar; disable the input afterward.

```json
{"credential":"<ONE_TIME_PRIVATE_CREDENTIAL>","action":"HOLD","signal_id":"NOVA-ST-V2:NIFTY:1784093700000:HOLD","signal_time":"2026-07-15T09:35:00Z"}
```

Never place the credential in the webhook URL, signal ID, plot, label, workspace reference, log, or screenshot. Use the common endpoint only.
