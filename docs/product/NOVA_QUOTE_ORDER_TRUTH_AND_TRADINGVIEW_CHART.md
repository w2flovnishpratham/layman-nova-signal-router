# Quote, manual-order truth, and chart call map

## Current LTP authority

`backend/app/services/quote_service.py` is the only application service allowed
to reach Dhan's Market Quote REST endpoint. It prefers a fresh market-feed
WebSocket tick, then the timestamped shared cache, then a bounded, rate-limited
REST batch.

| Caller | Instrument | Consumer cadence | Cache/source | Duplicate REST risk |
| --- | --- | --- | --- | --- |
| ATM snapshot / option cards | NIFTY spot plus resolved CE/PE IDs | On backend snapshot request | WebSocket → shared cache → one CE/PE REST batch | Coalesced by instrument |
| Manual-order panel | Both ATM option IDs | One browser request every 8 seconds | Same ATM snapshot; no provider call from browser | Removed two parallel 4-second loops |
| Explicit-contract quote | Resolved option security ID | User/API request | Quote authority | Cannot bypass limiter |
| PaperBroker fill | Exact submitted option security ID | Once per logical Paper operation | Quote authority, freshness required | Idempotent operation plus single-flight |
| Option position/P&L/SL-TP monitor | Open-position security ID | Runtime setting, minimum 1 second | Quote authority | Shared WebSocket/cache; REST single-flight |
| Dhan Super Order level calculation | Entry option security ID | Entry only, and one bounded level retry | Quote authority | No direct REST call |
| Runtime/market cards | NIFTY/ATM snapshot | App refresh every 15 seconds plus session events | Backend snapshot | No independent REST loop |
| TradingView Lightweight Chart | NIFTY 5-minute candles/current spot | Candle Data API every 20 seconds; live spot from session WebSocket | Candle cache/stream, not Market Quote REST | Zero LTP REST calls |

## Manual Paper terminal truth

`POSITION_OPEN` is returned only when the tracked position has all of:

- `has_open_position=true`
- entry order/position ID
- positive entry price
- positive quantity
- resolved security ID or trading symbol

The response hydrates the position and Paper portfolio immediately. Request
acceptance without this proof is non-terminal; contradictory accepted/fill
evidence returns `RECONCILIATION_REQUIRED`.

## TradingView reference

- Initial approved Lightweight Charts port:
  `db374bbc69baa7614d2c42efbbf5e9028d5b6cd4`
- Responsive/live hardening ported:
  `4811382bc03280fcd4d3a1da65e52a468c629f82`
- Library: `lightweight-charts` 5.2

The custom NIFTY SVG chart was removed. Backend-confirmed signal timestamps are
bucketed onto exact visible five-minute candles; no visual marker is guessed.
