# API Contract

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
