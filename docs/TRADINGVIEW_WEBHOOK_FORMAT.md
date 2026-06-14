# TradingView Webhook Format

## Endpoint

```text
POST https://your-domain/webhook/tradingview
Content-Type: application/json
X-Nova-Timestamp: <unix epoch seconds>
X-Nova-Signature: sha256=<hex hmac>
X-Nova-Nonce: <optional random value>
```

Production and live-mode requests require timestamped HMAC authentication.
The HMAC-SHA256 input is:

```text
<timestamp>.<exact raw request body>
```

The timestamp in the signing input must exactly match `X-Nova-Timestamp`.
The default accepted clock window is 60 seconds. Modified bodies, stale or
future timestamps, reused signal IDs, and reused nonces are rejected.

`X-Nova-Nonce` is optional because TradingView alerts cannot reliably generate
a fresh random header. When supplied, it must be 8-128 characters and can be
used only once per user.

TradingView's native webhook sender cannot calculate an HMAC or attach these
custom headers. Production alerts therefore require a trusted signing relay
that receives the TradingView alert, adds the timestamp/signature headers, and
forwards the unchanged body to NOVA. Direct unsigned TradingView delivery is
not accepted in production.

## Signing Example

```python
import hashlib
import hmac
import time

timestamp = str(int(time.time()))
signing_input = f"{timestamp}.{raw_body}".encode("utf-8")
signature = hmac.new(
    webhook_secret.encode("utf-8"),
    signing_input,
    hashlib.sha256,
).hexdigest()

headers = {
    "X-Nova-Timestamp": timestamp,
    "X-Nova-Signature": f"sha256={signature}",
}
```

The relay must sign the exact bytes it forwards. Reformatting JSON after
calculating the signature invalidates the request.

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
  "trading_symbol": "NIFTY 31 DEC 23000 CE",
  "option_side": "CE",
  "strike": 23000,
  "expiry": "2026-12-31",
  "qty": 65,
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
  "trading_symbol": "NIFTY 31 DEC 23000 CE",
  "option_side": "CE",
  "strike": 23000,
  "expiry": "2026-12-31",
  "qty": 65,
  "order_type": "MARKET",
  "product_type": "INTRADAY",
  "source": "tradingview"
}
```

## Validation Rules

1. `secret` must match the secret stored for the user.
2. `signal_id` must be unique per user.
3. Live mode requires an explicitly supplied `signal_id`.
4. Reusing a signal ID with different order fields is treated as suspicious.
5. `strategy_code` must be recognized.
6. Only supported NIFTY option contracts may enter the live route.
7. `security_id` must match symbol, expiry, strike, and CE/PE when the Dhan
   scrip master is available.
8. Quantity must be positive and a multiple of the Dhan lot size.
9. Live requests must pass timestamped HMAC verification.

## TradingView Setup

1. Configure the TradingView alert message with the JSON template.
2. Send the TradingView webhook to a trusted signing relay.
3. Configure the relay to forward to NOVA's `/webhook/tradingview` endpoint.
4. Keep the forwarded body byte-for-byte unchanged after signing.
5. Store the webhook secret only in NOVA and the signing relay.

## Nonce Decision

Nonce is optional. Timestamped HMAC plus atomic per-user `signal_id`
deduplication is mandatory for live mode. A nonce adds another replay barrier
for clients that can generate a unique value, but it is not required for
TradingView-compatible signing relays.

## Server-Side Exit Workers

Authenticated multi-user trading workers remain disabled. The web role never
starts EOD square-off, ghost-position, or option-monitor threads. A future
worker release must use DB-backed trading state, explicit per-user iteration,
and a verified distributed leader lock.
