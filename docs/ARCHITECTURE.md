# Architecture

## Overview

```text
User browser on Vercel
  -> Backend setup APIs on VPS
  -> Encrypted file vault for Dhan credentials and webhook secret

TradingView
  -> POST /webhook/tradingview on VPS backend
  -> Secret validation
  -> Pine/NOVA payload parser
  -> Duplicate signal protection
  -> securityId resolver
  -> Risk manager
  -> Dhan client
  -> Runtime state and sanitized logs
```

The frontend never calls Dhan. Only the VPS backend sends read-only Dhan validation calls and order placement requests.

## Runtime Storage

MVP storage is file-backed:

- `runtime_state/credentials.enc.json`: encrypted Dhan credentials and webhook secret
- `runtime_state/settings.json`: runtime engine and risk settings
- `runtime_state/app_state.json`: dashboard lifecycle state
- `runtime_state/open_position.json`: tracked open position
- `runtime_state/seen_signals.json`: duplicate signal memory
- `runtime_logs/*.jsonl`: sanitized webhook, order, audit, and error logs

## Setup Flow

1. Frontend opens `/`.
2. User submits Dhan Client ID and Access Token.
3. Backend validates credentials through a safe read-only Dhan endpoint.
4. Backend stores credentials in encrypted vault.
5. Wallet/funds are displayed.
6. User creates a TradingView webhook secret.
7. User sets risk limits.
8. User starts the engine.

## Webhook Flow

1. TradingView posts to `/webhook/tradingview`.
2. Backend validates the saved webhook secret.
3. Backend parses existing Pine `multi_leg_order` or NOVA payloads.
4. Backend blocks duplicate signal IDs.
5. Backend resolves `securityId`.
6. Backend runs risk checks.
7. Backend calls Dhan only when engine is started and live-order safety flags allow it.
8. Dashboard shows lifecycle and sanitized logs.

## Security Model

- Dhan tokens are not stored in the browser.
- Dhan tokens are not returned by API responses.
- Logs are sanitized for tokens and client IDs.
- Debug endpoints return `404` unless `DEBUG_ENABLED=true`.
- `ENABLE_LIVE_ORDERS=false` blocks actual Dhan orders in REAL mode.
- CORS is restricted to `FRONTEND_ORIGIN` in production.
