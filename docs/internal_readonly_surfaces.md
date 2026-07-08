# Internal Strategy Surfaces

This document records the current internal observability surfaces for v2 paper
fanout and multi-strategy catalog visibility. Phase 2F-1 adds one narrow
debug-gated mutation exception for paper strategy instance lifecycle only:
create born paused, pause, and resume. These are not execution surfaces.

## Panels And Flags

| Panel | Frontend flag | Default | Route |
| --- | --- | --- | --- |
| V2 Paper Status | `VITE_ENABLE_V2_PAPER_STATUS=true` | Off | `#v2-paper-status` |
| Strategy Catalog Status | `VITE_ENABLE_STRATEGY_CATALOG_STATUS=true` | Off | `#strategy-catalog-status` |
| Paper instance lifecycle controls | `VITE_ENABLE_STRATEGY_INSTANCE_MUTATION=true` | Off | `#strategy-catalog-status` |

Both flags are frontend visibility guards only. Backend auth, debug flags, and
endpoint gates remain the real protection.

When a flag is off:

- The header/menu item is hidden.
- Direct hash route renders an internal-only disabled state.
- The panel does not call its internal APIs.

## Endpoints

V2 Paper Status calls:

- `GET /api/debug/v2/paper-fanout/status`
- `GET /api/debug/v2/paper-fanout/jobs`
- `GET /api/debug/v2/paper-fanout/instances`

Strategy Catalog Status calls:

- `GET /api/strategies/catalog`
- `GET /api/strategies/instances`

Paper instance lifecycle controls call:

- `POST /api/debug/v2/instances`
- `POST /api/debug/v2/instances/{id}/pause`
- `POST /api/debug/v2/instances/{id}/resume`

The lifecycle endpoints require backend debug gates and `confirm_paper_only`.
Create always writes `execution_mode="paper_live_data"` and `status="paused"`.
Pause/resume only flip eligible paper or signal-only instance status between
`active` and `paused`.

## Never Allowed In These Surfaces

Do not add controls or API calls for:

- subscribe or unsubscribe
- activate or deactivate
- run, process, or retry jobs
- generate webhooks or rotate secrets
- go live or place paper/live orders
- square off or exit positions
- worker, scheduler, or startup wiring
- Dhan live placement

Phase 2F-1 allows only:

- create paper instance born paused
- pause eligible paper/signal-only instance
- resume eligible paper/signal-only instance

It does not add execution controls, webhook generation, live mode, Dhan
placement, workers, schedulers, or startup wiring.

## Sensitive Fields

These surfaces must not show or return credential material, secret hashes, raw
payloads, request/response bodies, headers, or tokens. Examples:

- `secret`
- `access_token`
- `token`
- `client_id`
- `webhook_key_hash`
- `message_secret_hash`
- `credential_hash`
- `proxy_url`
- `raw_payload`
- `signal_payload`
- `headers`
- `response_json`
- `config_json`
- `risk_config`
- `metadata_json`

Backend endpoints should curate response fields. Frontend reads also sanitize
internal status payloads before rendering.

## Manual Smoke

Use a safe local or staging environment only.

Default-off smoke:

```bash
npm --prefix frontend run build
```

Expected:

- No internal nav items are visible.
- Direct internal hash routes show disabled state.
- No internal API calls are made by disabled panels.

Single-panel smoke:

```bash
VITE_ENABLE_V2_PAPER_STATUS=true npm --prefix frontend run build
VITE_ENABLE_STRATEGY_CATALOG_STATUS=true npm --prefix frontend run build
```

Expected:

- Enabled panel calls only its GET endpoints.
- The other internal panel remains hidden/disabled if its flag is off.
- No sensitive fields are visible.

Both-panel smoke:

```bash
VITE_ENABLE_V2_PAPER_STATUS=true VITE_ENABLE_STRATEGY_CATALOG_STATUS=true npm --prefix frontend run build
```

Expected:

- Both internal panels build successfully.
- Observed network traffic remains GET-only for internal endpoints.

Lifecycle smoke:

```bash
VITE_ENABLE_STRATEGY_CATALOG_STATUS=true VITE_ENABLE_STRATEGY_INSTANCE_MUTATION=true npm --prefix frontend run build
```

Expected:

- Lifecycle controls appear only in the catalog status panel.
- New instances are created paused and paper-only.
- Pause/resume require confirmation.
- No execution, webhook, live, worker, scheduler, or Dhan calls occur.

## Required Review Before Further Mutation

Any future phase beyond the Phase 2F-1 lifecycle triplet that adds subscription,
activation, webhook setup, paper execution, live execution, or job processing
must be reviewed separately before implementation. That review should define
auth, entitlements, idempotency, audit logging, staging verification, rollback,
and Dhan/live safety gates first.
