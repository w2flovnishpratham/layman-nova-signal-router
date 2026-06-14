# API Contract

## Health and readiness

`GET /health` and `GET /api/health` are public liveness probes and return only:

```json
{"status": "ok"}
```

`GET /api/readiness` is a public, non-secret production dependency probe. It
returns HTTP `200` only when database connectivity, Alembic revision, vault,
runtime directories, authentication policy, disabled-Live policy, worker
policy, and debug policy are ready. Otherwise it returns HTTP `503`.

The readiness response contains categorical values only. It never returns
database URLs, environment values, filesystem paths, tokens, cookies, user
data, or encryption material.

Base URL: `https://api.yourdomain.com`

The frontend does not call Dhan. It calls this backend only.

## Setup

### GET /api/setup/status

Returns Dhan connection status, masked client ID, webhook secret status, risk settings, engine status, wallet snapshot, webhook URL, outgoing backend IP, and safety readiness.

### POST /api/setup/dhan/connect

```json
{ "client_id": "string", "access_token": "string" }
```

Validates Dhan through a read-only endpoint and stores credentials in the encrypted server-side vault. The response never includes the full token.

### POST /api/setup/dhan/disconnect

Clears stored Dhan credentials and stops the engine.

### POST /api/setup/webhook-secret

```json
{ "webhook_secret": "at-least-16-random-characters" }
```

Stores the webhook secret server-side. The backend rejects short or low-entropy secrets. The response returns only `webhook_secret_set`.

### POST /api/setup/risk

```json
{
  "max_qty_per_order": 1,
  "max_trades_per_day": 1,
  "daily_loss_limit": 500,
  "allow_entry": true,
  "allow_exit": true
}
```

### POST /api/setup/scrip-master/refresh

Starts a background Dhan scrip master refresh job and returns immediately.

```json
{ "success": true, "accepted": true, "job_id": "string", "status": "RUNNING" }
```

### GET /api/setup/scrip-master/status

Returns configured/fallback file status, last download metadata, and `refresh_job` with `IDLE`, `RUNNING`, `SUCCEEDED`, or `FAILED`.

## Engine

### POST /api/engine/start

```json
{ "confirm_live_orders": false }
```

Checks setup readiness, validates Dhan, then enables webhook trading. If `DHAN_MODE=REAL` and `ENABLE_LIVE_ORDERS=true`, confirmation is required.

### POST /api/engine/stop

Disables webhook trading. New entries are blocked; exits remain controlled by risk settings.

### GET /api/engine/status

Returns current app state and setup status.

## Runtime

### POST /webhook/tradingview

Accepts TradingView alerts. Existing Pine `multi_leg_order` and NOVA payloads are supported.

Required production/live headers:

```text
X-Nova-Timestamp: <unix epoch seconds>
X-Nova-Signature: sha256=<HMAC-SHA256 of timestamp + "." + raw body>
X-Nova-Nonce: <optional unique random value>
```

The default timestamp tolerance is 60 seconds. Signal IDs and optional nonces
are claimed atomically in SQL before an order can route. Duplicate signal IDs,
changed payloads under the same ID, stale signatures, and reused nonces are
rejected.

### GET /api/dashboard/summary

Returns clean dashboard state, wallet, latest logs, open position, mode, and webhook URL.

### GET /api/orders

Returns sanitized order events.

### GET /api/positions

Returns the tracked open position.

### GET /api/logs

Returns sanitized webhook, order, audit, and error logs.

## Safety Controls

### POST /api/control/emergency-stop

Immediately blocks new entries.

### POST /api/control/resume

Clears emergency stop.

### POST /api/control/global-kill-switch

```json
{ "enabled": true }
```

Toggles the global kill switch.

## Debug

Debug routes under `/api/debug/*` return `404` unless `DEBUG_ENABLED=true`.
