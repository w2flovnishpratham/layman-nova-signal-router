# NOVA Runtime Reliability and Minor Bug Fix Sprint

Status after isolated-staging acceptance:

`RUNTIME_RELIABILITY_AND_MINOR_BUGS_STAGING_SMOKE_COMPLETED`

## Scope and safety boundary

This sprint hardens the existing engine. It does not create another execution
engine, another position authority, or another credential store.

- The owner-scoped runtime JSON files remain the execution read authority.
- `StrategyInstancePosition` remains a durability/parity shadow.
- Dhan credentials remain encrypted in `UserCredentialVault`; frontend
  responses contain only the masked client ID and token-age metadata.
- Pine conversion, Prompt V3.1, Transport V2, Claude schemas/providers/parsing,
  strategy verification, webhook credentials, and webhook routing authority
  are unchanged.
- Browser state is never engine authority.
- Paper and Live configuration are isolated in `paper_settings.json` and
  `live_settings.json` under each user's existing runtime directory.

No database migration is required. The existing persisted runtime and run
models can express the lifecycle safely, and the legacy settings file is
copied once into each mode-specific file without deleting or rewriting it.

## Lifecycle contract

The backend owns these lifecycle states:

- `STARTING`
- `RUNNING`
- `STOPPING`
- `STOPPED`

`RUNNING` plus `webhook_trading_enabled=true` is the only state that accepts
new signals. Login, refresh, WebSocket reconnect, browser close, and dashboard
navigation do not start or stop the engine.

Normal Stop:

- blocks new entries;
- transitions through `STOPPING` to `STOPPED`;
- preserves the tracked open position;
- keeps position and LTP state available to background monitoring;
- writes a final P&L snapshot;
- never sends an exit order.

Reconfigure:

- explicitly stops the engine;
- never automatically restarts it;
- preserves an open position;
- blocks mode, lot, SL, and TP changes until flat.

Logout offers:

1. Keep engine running and log out — writes a non-final checkpoint.
2. Stop engine and log out — performs normal Stop and writes a final snapshot.
3. Cancel — performs no server mutation.

## Exit and reversal guarantees

`POST /api/runtime/square-off` enters `STOPPING`, blocks new entries first, and creates one
server-owned `RUNTIME_EXIT_<uuid>` operation. The operation is persisted before
routing the exit.

- Repeated calls while `EXIT_PENDING` return the existing operation.
- Repeated calls while `RECONCILIATION_REQUIRED` also return the existing
  operation and never place a second order.
- `CONFIRMED_FLAT` is set only after the tracked position authority is flat.
- Partial or pending fills remain `EXIT_PENDING`.
- Unknown or failed broker outcomes become `RECONCILIATION_REQUIRED`.
- The lifecycle reaches `STOPPED` only after the tracked position is confirmed
  flat; pending or reconciliation-required exposure remains `STOPPING`.

The existing opposite-signal reversal path is retained. It exits the current
option first, waits for confirmed execution, clears the tracked position, and
only then submits the opposite entry. Pine `EXIT` remains an exit instruction;
visual-only Pine output remains non-executable.

## Configuration and Paper reset

Stopped-and-flat users can update mode-specific settings atomically:

- lots: 1–20;
- stop loss: 0–100 percent;
- target profit: 0–1000 percent.

Changing selected mode never starts the engine. A tracked position retains the
quantity, entry price, and accepted exit levels it owned when opened.

Paper reset is a distinct explicit action. It is rejected while the engine is
running, while a Paper position is open, or while an exit is pending. A final
snapshot is written before the Paper portfolio is reset. Live state and Live
configuration are not modified.

## Hydrated status API

`GET /api/runtime/status` is owner-scoped and returns:

- backend lifecycle, signal-acceptance state, and active mode;
- explicit `POSITION OPEN — ENGINE STOPPED` display state;
- exit operation state;
- tracked position quantity and identity;
- realized, unrealized, and session P&L;
- LTP value, source, received time, age, status, and stale flag;
- separate Paper and Live settings;
- masked Dhan client ID and token age/expiry metadata;
- server safety gates (`DHAN_MODE`, live-order enablement).

LTP is never fabricated. Missing quotes remain unavailable; quotes older than
15 seconds are marked stale.

Account refresh uses the current owner's encrypted credential record on the
server. In `DHAN_MODE=MOCK` it returns the controlled
`UNAVAILABLE_IN_MOCK` result and does not alter lifecycle state.

## Endpoints

- `GET /api/runtime/status`
- `POST /api/runtime/stop`
- `POST /api/runtime/square-off`
- `PUT /api/runtime/mode`
- `PUT /api/runtime/config`
- `POST /api/runtime/paper/reset`
- `POST /api/runtime/account/refresh`
- `POST /api/auth/logout` with `engine_action=keep_running|stop_engine`

Existing `/api/engine/start`, `/api/engine/stop`,
`/api/engine/reconfigure`, and `/api/engine/status` remain compatible and now
publish the authoritative lifecycle contract.

## Regression evidence

Backend coverage includes lifecycle transitions, owner scoping, normal Stop
with an open position, mode isolation, atomic configuration validation, Paper
reset gates, LTP freshness, P&L hydration, masked credentials, controlled MOCK
account refresh, square-off confirmation, pending/reconciliation idempotency,
snapshot finality, and concurrent Stop calls.

Frontend coverage includes server lifecycle hydration, masked identity,
open-position/stopped display, stale LTP, distinct Stop and Square Off actions,
hold-to-confirm Square Off, reconfigure confirmation, three-way logout,
visible Paper/Live controls, open-position guards, atomic lot/SL/TP save,
explicit Paper reset, and account refresh without lifecycle mutation.

The staging acceptance record must include the exact commit SHA, environment
and safety gates, backend/full frontend results, manual flows A–G, service and
Nginx health, and rollback references. No real broker order is permitted.

## Isolated-staging deployment and acceptance

- Starting deployed SHA: `20ae3f2d4e54b1c09e41885c26d13be35bc42f46`
- Branch: `phase-runtime-reliability-and-minor-bugs`
- Database migration: none
- Alembic: `0017_expand_admin_review_status (head)`
- Backend service: `layman-nova-signal-router.service`
- Frontend root: `/var/www/layman-c1c2`
- Backup: `/root/backups/nova-runtime-20260719T200937Z`
- Backup contents: PostgreSQL custom dump, `/etc/layman/layman.env`, frontend
  static archive, predeploy SHA, and `SHA256SUMS`
- Safety gates: `APP_ENV=isolated_staging`, `DHAN_MODE=MOCK`,
  `ENABLE_LIVE_ORDERS=false`,
  `PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED=false`
- Service health: active
- Public backend health: HTTP 200 / `status=ok`
- Public frontend health: HTTP 200
- Runtime status without authentication: HTTP 401
- Nginx configuration: valid before and after static deployment

Acceptance results:

- Flow A: Stop Engine & Logout persisted `STOPPED`; no square-off occurred.
- Flow B: Keep Engine Running & Logout persisted `RUNNING` across a new login.
- Flow C: Reconfigure stopped the engine, allowed flat configuration, and did
  not restart it.
- Flow D: the live after-hours attempt failed closed at the market-hours guard
  and correctly remained `STOPPING`, not falsely `STOPPED`. A deterministic
  Paper-only staging harness then confirmed `ORDER_PLACED`,
  `CONFIRMED_FLAT`, and terminal `STOPPED`.
- Flow E: the live after-hours attempt failed closed with the CE position
  unchanged. The deterministic Paper-only staging harness then confirmed
  `REVERSAL_ORDER_PLACED`, final side PE, and one tracked position only.
- Flow F: Paper `[3 lots, 7% SL, 14% TP]` and Live
  `[5 lots, 9% SL, 18% TP]` remained independent; switching back to Paper did
  not start the engine.
- Flow G: MOCK account refresh returned controlled
  `UNAVAILABLE_IN_MOCK`; the engine remained stopped.

The deterministic D/E harness patched only in-process market/time and Paper
quote/token providers for a synthetic, deleted smoke user. It asserted MOCK
mode and disabled Live orders before execution. It did not change the service
environment, call a Dhan order endpoint, or place a real broker order.

## Known non-blocking test findings

- `test_dhan_payload_validation_passes_after_security_id_resolution` fails
  identically on the deployed baseline and this branch. It is a documented
  pre-existing baseline failure; unrelated Dhan code was not changed.
- The `+301` future-timestamp boundary case in
  `test_production_strategy_webhook_rejects_stale_or_future_timestamp` can
  cross its one-second boundary during a grouped run. It passed five of five
  immediate isolated repeats.
- Full frontend ESLint retains pre-existing findings in unrelated components.
  The changed Header and its runtime suite lint clean; Ruff and Python compile
  checks pass.
- Browser-control tooling was unavailable in this Codex runtime. The rendered
  three-choice logout, reconfigure, lifecycle, mode, reset, LTP, and account
  controls were validated by 26 focused DOM behaviors within 61 passing
  frontend tests. Authenticated staging lifecycle flows were exercised over
  the deployed HTTP API.

## Rollback

1. Check out starting SHA
   `20ae3f2d4e54b1c09e41885c26d13be35bc42f46`.
2. Restore `/var/www/layman-c1c2` from
   `layman-c1c2-static.tar.gz`.
3. Restore `/etc/layman/layman.env` only if environment drift is detected.
4. Database restore is not normally required because this sprint added no
   migration. If separately necessary, use `database.dump`.
5. Restart only `layman-nova-signal-router.service`, run `nginx -t`, and repeat
   backend/frontend health checks.
