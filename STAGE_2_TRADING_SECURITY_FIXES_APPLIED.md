# Stage 2 Trading Security Fixes Applied

Date: 2026-06-13

Scope: Stage 2 only. Stage 3, Stage 4, and Stage 5 were not implemented.

## 1. Summary

Stage 2 added timestamped webhook HMAC authentication, SQL-backed webhook
replay/dedup records, optional single-use nonces, durable live-order route
attempts, stable per-user correlation IDs, final broker-call safety checks,
worker-role startup guards, and a live instrument validation test matrix.

`ENABLE_LIVE_ORDERS=false` remains the default in code, environment templates,
and VPS configuration.

This does not approve a public live launch. Trading runtime state remains
partly JSON-backed, authenticated multi-user trading workers remain disabled,
the signing relay is not deployed, and per-user executor/egress routing is not
implemented end to end.

## 2. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/app/auth/models.py` | Added webhook receipt, nonce, and order-route-attempt tables | Durable cross-worker idempotency |
| `backend/alembic/versions/20260613_0002_stage2_trading_security.py` | Added Stage 2 SQL migration | Production Postgres schema |
| `backend/app/services/trading_security.py` | Added atomic claims, payload hashes, and completion updates | Central trading idempotency service |
| `backend/app/routers/webhook.py` | Added timestamped HMAC, nonce claims, SQL signal claims, generic auth errors | Replay protection |
| `backend/app/services/execution_router.py` | Added stable correlation IDs, durable route claims, final safety guards, live instrument checks | Prevent duplicate or unsafe broker calls |
| `backend/app/services/security_id_resolver.py` | Verifies supplied IDs against symbol/expiry/strike/CE-PE | Prevent wrong-contract orders |
| `backend/app/services/worker_policy.py` | Added web/trading-worker role policy and explicit user iteration helper | Prevent unsafe threads in web workers |
| `backend/app/main.py` | Added worker/startup guards and production live prerequisites | Fail closed on unsafe configuration |
| `backend/app/config.py` | Added webhook, worker, signal-ID, expiry, and instrument-master settings | Explicit production policy |
| `backend/.env.example` | Added Stage 2 safe defaults | Deployment reference |
| `backend/.env.live.example` | Added Stage 2 safe defaults | Live-test reference |
| `deploy/configure_vps_env.sh` | Forces web role, workers off, signed webhooks, instrument validation | Safe VPS defaults |
| `.github/workflows/layman-backend-ci-deploy.yml` | Added Alembic upgrade/check gates | Prevent schema drift |
| `backend/app/tests/test_stage_2_trading_security.py` | Added 34 Stage 2 tests | Acceptance coverage |
| `backend/app/tests/conftest.py` | Added safe Stage 2 test defaults and SQL cleanup | Deterministic isolation |
| `backend/app/tests/security_test_helpers.py` | Added Stage 2 isolated defaults | Auth test compatibility |
| `backend/app/tests/test_compliance_audit.py` | Updated live webhook tests for timestamped HMAC | New production contract |
| `backend/app/tests/test_chat_production_bridge.py` | Updated HMAC unit contract | Timestamp is now signed |
| `backend/app/tests/test_paper_mode.py` | Configured a valid secret before mode-order assertion | Authentication now occurs first |
| `docs/TRADINGVIEW_WEBHOOK_FORMAT.md` | Documented headers, signing relay, replay behavior, nonce decision | Operator/client integration |
| `docs/API_CONTRACT.md` | Documented webhook authentication contract | API consumers |
| `docs/SAFETY_RULES.md` | Documented durable dedup, final checks, worker policy | Safety operations |
| `README.md` | Added production signing-relay warning | Prevent unsigned deployment |

## 3. Webhook Replay Protection

- Required production/live timestamp header: `X-Nova-Timestamp`.
- Required signature header: `X-Nova-Signature`.
- Signing input: `<timestamp>.<exact raw request body>`.
- Algorithm: HMAC-SHA256 using the user's webhook secret.
- Default tolerance: 60 seconds.
- Missing, malformed, stale, future, or mismatched authentication is rejected
  with a generic error that does not expose secrets or expected signatures.
- Legacy unsigned/body-only behavior is possible only when
  `WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL=true` in local/test mode while live orders
  are disabled. It is rejected in production.
- Live mode requires an explicitly supplied `signal_id`.
- Duplicate signal IDs are rejected from SQL before order routing.
- Reusing a signal ID with changed payload data is marked suspicious.

Nonce decision:

- `X-Nova-Nonce` is optional.
- When present, it must be 8-128 characters and is atomically single-use per
  user.
- It is not mandatory because TradingView cannot reliably create dynamic
  random headers.
- Timestamped HMAC plus SQL-backed signal-ID dedup remains mandatory for live.

TradingView cannot calculate HMAC or attach these custom headers directly.
Production use therefore requires a trusted signing relay. That relay was not
implemented or deployed in Stage 2.

## 4. Order Idempotency

- `webhook_signal_receipts` has a unique `(user_id, signal_id)` constraint.
- `order_route_attempts` has a unique `(user_id, correlation_id)` constraint.
- SQL unique constraints, not process-local sets, are the final authority.
- Correlation IDs derive from user ID, signal ID, strategy code, action, and
  instrument identity.
- The exact sanitized order payload is hashed before the broker call.
- Same correlation plus same hash is a duplicate and is blocked.
- Same correlation plus different hash is suspicious and is blocked.
- Route status is persisted as pending, blocked, placed, failed, or broker
  exception.
- Different users may use the same signal ID because user ID is part of both
  uniqueness boundaries and correlation generation.
- Dhan does not expose a general idempotency-key API, so NOVA performs durable
  internal idempotency before calling Dhan.

## 5. Worker Safety

- `WORKER_ROLE=web` never starts EOD square-off, ghost watcher, or option
  position monitor threads.
- `WORKER_ROLE=trading-worker` starts legacy single-tenant workers only when
  `ENABLE_TRADING_WORKERS=true`.
- Production refuses enabled workers without
  `TRADING_WORKER_DISTRIBUTED_LOCK_ENABLED=true`.
- Authenticated multi-user workers still fail startup even with that flag,
  because their trading state and loops are not yet safely DB-backed.
- `run_for_active_users()` provides explicit DB user iteration and binds each
  user's context without a global fallback.

Remaining worker work:

- Migrate trading state from JSON to Postgres/Redis.
- Implement and verify a real distributed leader lock.
- Convert every worker loop to explicit per-user iteration.
- Add cross-process EOD and exit idempotency.

## 6. Live Order Safety

Immediately before the Dhan order method, NOVA now re-reads and checks:

- `ENABLE_LIVE_ORDERS`
- current engine mode
- webhook intake state for entries
- emergency stop
- global kill-switch policy
- entry/exit enable flags
- maximum quantity
- option-side filter
- daily trade count
- daily loss
- market hours

Before that final check, NOVA atomically claims the durable route attempt.
Blocked attempts are audit logged and never call the Dhan order method.

Live instrument guards cover:

- CE/PE mismatch
- wrong strike
- wrong expiry
- stale expiry
- missing security ID
- security ID mapped to another symbol/contract
- quantity not matching lot size
- zero or negative quantity
- unsupported exchange segment
- unsupported live product type
- market closed
- non-NIFTY instruments
- malformed webhook payloads
- unverified security IDs when production live validation is required

Production live startup also requires scrip-master auto-resolution, no default
security-ID override, explicit live signal IDs, and fresh expiry validation.

## 7. Tests Added

The Stage 2 test module adds 34 tests covering:

- missing/malformed/stale/future timestamp rejection
- timestamp/body HMAC binding
- valid signed webhook acceptance
- SQL-backed duplicate routing prevention
- suspicious changed-payload detection
- nonce replay rejection
- explicit live signal-ID requirement
- atomic concurrent signal claims
- cross-user signal isolation
- web-role worker suppression
- explicit trading-worker enable flag
- production distributed-lock guard
- explicit worker user context
- final emergency-stop, kill-switch, and live-flag races
- durable route-attempt idempotency
- cross-user correlation isolation
- suspicious changed order payload
- CE/PE, strike, expiry, symbol, lot-size, exchange, product, NIFTY, stale
  expiry, missing ID, and unverified-ID blocking
- market-hours final recheck
- audit event generation for blocked live instruments
- malformed and non-positive quantity rejection

## 8. Verification Results

Run on 2026-06-13:

| Command | Result |
|---|---|
| `python -m pytest app/tests -q` | `231 passed in 158.00s` |
| `python -m pytest app/tests/test_stage_2_trading_security.py -q` | `34 passed in 17.56s` |
| `python -m alembic upgrade head` | Passed |
| `python -m alembic check` | Passed: no new upgrade operations |
| Python built-in compile of every `app/**/*.py` | `95 files` compiled |
| `python scripts/check_repo_hygiene.py` | Passed |
| `npm run test:security` | Passed |
| `npm run lint` | Passed |
| `npm run build` | Passed; Vite 8.0.16, 2,168 modules |
| Deployment shell syntax checks | Passed |
| `git diff --check` | Passed; Windows line-ending notices only |

`python -m compileall app` was run but could not write bytecode because the
temporary checkout contains inherited Windows-owned `__pycache__` directories.
The source-only compile check successfully compiled all 95 Python files.

`bandit` and `pip-audit` were not installed, so those optional scans were not
run.

## 9. Remaining Risks

- Public live launch is not approved.
- Trading runtime state is still partly JSON-backed and is not safe for
  multiple trading workers.
- Full Postgres/Redis trading-state migration and distributed locks are still
  required.
- A trusted webhook signing relay must be implemented and operated.
- Per-user executor-node/unique-egress routing must be implemented and verified
  with real Dhan test orders.
- Redis-backed rate limiting, worker coordination, and websocket pub/sub remain
  future work.
- Monitoring, backups, restore drills, non-root systemd, nginx hardening, log
  retention, and incident automation remain Stage 4/5 concerns.
- Broker timeout reconciliation reduces uncertainty but cannot provide Dhan
  server-side idempotency where the broker does not support it.

## 10. Go / No-Go Status

- **Multi-user paper beta:** Conditional GO with authentication enabled, live
  orders disabled, web role selected, and trading workers disabled.
- **Single trusted operator live:** NO-GO in the current deployment. Requires a
  deployed signing relay, verified instrument master, executor/egress routing,
  and a controlled real-order validation.
- **Public live launch:** NO-GO.
- **100-user production launch:** NO-GO.

Next recommended stage: **Stage 3**, adding trustworthy live/paper UI state,
typed live confirmation, pending-action protection, and operator-visible
safety status. Stage 4/5 infrastructure work remains mandatory before public
or 100-user live launch.
