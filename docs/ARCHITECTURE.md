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

## Process Topology (production)

Two systemd units run the **same** application, split by route in nginx.

| | Engine (`layman-nova-signal-router`, :8002) | Intake (`layman-nova-signal-intake`, :8003) |
|---|---|---|
| `BACKGROUND_WORKER_RUNNER_ENABLED` | `true` | `false` |
| Singleton workers (Dhan WS, option monitor, EOD square-off, ghost watcher, strategy job worker, shared-token refresh) | yes — **exactly one process may run these** | no |
| Routes | everything else, incl. UI/API, manual orders, `/api/webhook/tradingview` | `/api/webhook/strategy/*`, `/api/webhook/user/*`, `/api/webhooks/private` |

**Why:** the app runs one uvicorn worker per process with a *synchronous* DB
layer, so every request that touches Postgres blocks that process's whole event
loop for the round-trip. Near-simultaneous TradingView alerts queued behind each
other until TradingView's delivery timeout fired (nginx `499`, no trace in the
app log because the request never got to run).

**What may be routed to intake:** only endpoints that enqueue a
`StrategyExecutionJob` and return. They never call `route_signal()`, so they
never touch `paper_portfolio`'s process-local `threading.Lock`; their duplicate
protection is a Postgres unique constraint, which holds across processes. The
engine's job worker polls every `STRATEGY_JOB_WORKER_POLL_SECONDS` (0.5s), so it
picks up intake-enqueued jobs without a cross-process wake signal.

**What must never be routed to intake:** anything that executes a trade inline —
manual orders (`/api/orders/*`) and `/api/webhook/tradingview` both call
`route_signal()` directly and depend on that process-local lock. Splitting them
across processes would reintroduce the duplicate-execution race the lock exists
to prevent. `test_deploy_env_config.py` asserts this routing invariant.

**Degradation:** nginx lists the engine as a `backup` upstream for the intake
routes, so a dead intake worker loses concurrency isolation but does not drop
webhooks.

## Security Model

- Dhan tokens are not stored in the browser.
- Dhan tokens are not returned by API responses.
- Logs are sanitized for tokens and client IDs.
- Debug endpoints return `404` unless `DEBUG_ENABLED=true`.
- `ENABLE_LIVE_ORDERS=false` blocks actual Dhan orders in REAL mode.
- CORS is restricted to `FRONTEND_ORIGIN` in production.
