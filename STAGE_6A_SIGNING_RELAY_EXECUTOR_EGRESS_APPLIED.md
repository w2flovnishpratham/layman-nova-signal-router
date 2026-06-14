# Stage 6A Signing Relay and Executor Egress Applied

Date: 2026-06-15

## 1. Summary

Stage 6A adds a guarded TradingView signing relay, manual executor registry, one-user/one-executor assignments, reserved-IP verification, expiring admin approvals, durable live jobs, executor-specific HMAC requests, replay protection, and a controlled live-pilot UI.

A two-account **dry-run** pilot is supported after the real Hostinger, Neon, and DigitalOcean configuration is completed. Real Dhan orders remain disabled by default and fail closed because short-lived per-order credential transport to executors is intentionally deferred to Stage 6B.

## 2. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/app/auth/models.py` | Added executor, assignment, approval, live-job, and executor nonce models | Durable pilot routing and audit state |
| `backend/alembic/versions/20260615_0005_stage6a_live_pilot.py` | Added Stage 6A schema and enabled only `SUPERTREND_FLIP` for controlled Live | Production migration |
| `backend/app/config.py`, `.env*.example` | Added relay, pilot, executor, and dry-run settings with safe defaults | Explicit fail-closed configuration |
| `backend/app/routers/relay.py` | Added token-protected TradingView relay | Authenticated normalized central intake |
| `backend/app/services/executor_registry_service.py` | Added registration, health/egress verification, assignment, and route lookup | Enforce unique per-user egress |
| `backend/app/services/live_pilot_service.py` | Added approvals, expiry, risk-cap, and readiness checks | Controlled user eligibility |
| `backend/app/services/live_execution_service.py` | Added idempotent live jobs and signed executor routing | No direct Hostinger-to-Dhan order path |
| `backend/app/services/executor_signing.py` | Added canonical JSON and HMAC request signing | Request authenticity |
| `backend/app/executor_service/main.py` | Added executor `/health`, `/egress-ip`, and `/execute-order` | Minimal deployable executor contract |
| `backend/app/services/signal_fanout_service.py` | Added isolated Live fanout without changing Paper execution | One central signal, per-user routes |
| `backend/app/services/queue_service.py`, worker runtimes | Added ID-only live job type and dedicated live-pilot worker | Durable separated execution |
| `backend/app/routers/live_pilot.py` | Added admin executor/approval and user readiness APIs | Operator controls |
| `frontend/src/components/LivePilotPanel.tsx` | Added readiness, dry-run activation, assignment, approval, and verification UI | Controlled pilot operations |
| `backend/app/tests/test_stage_6a_signing_relay_executor_egress.py` | Added 26 Stage 6A tests | Routing, signing, isolation, and safety proof |

## 3. Signing Relay

Endpoint:

```text
POST /relay/tradingview/{strategy_code}
```

It is disabled unless `RELAY_ENABLED=true`. Authentication accepts either `Authorization: Bearer ...` or `X-Nova-Relay-Token`. Tokens use constant-time comparison against `RELAY_SHARED_SECRET`.

Only strategies in `RELAY_ALLOWED_STRATEGIES` and `LIVE_PILOT_ALLOWED_STRATEGIES` are accepted. Stage 6A permits only `SUPERTREND_FLIP`. The relay requires a current admin-approved pilot user, validates all TradingView fields, normalizes the signal, creates timestamped HMAC verification metadata, and calls the internal central signal service. It exposes neither users nor credentials.

The live worker recomputes the stored payload hash and relay HMAC before executor contact, so fabricated intake metadata is rejected.

## 4. Executor Registry

`executor_nodes` stores executor identity, URLs, DigitalOcean metadata, reserved IP, health state, and last observed egress IP.

Stage 6A target nodes:

```text
EXECUTOR_001 / nova-exec-user-001 / 64.225.87.19
EXECUTOR_002 / nova-exec-user-002 / 152.42.157.165
```

Health verification requires `/health` to return the configured executor code. Egress verification requires `/egress-ip` to exactly equal the reserved IP. Production executor URLs must use HTTPS.

`user_executor_assignments` uses partial unique indexes so a non-disabled user has one executor and a non-disabled executor has one user. Assignment starts as `pending_whitelist`; admin verifies it only after the Dhan account IP whitelist is complete.

## 5. Live Pilot Approval

Only an authenticated admin can approve or revoke a user. Approval is strategy/version specific, expires, and caps quantity, daily trades, daily loss, and option side.

A Live subscription requires:

- `LIVE_PILOT_ENABLED=true`;
- `SUPERTREND_FLIP` in the pilot allowlist;
- current admin approval;
- connected user-owned Dhan metadata;
- active executor with matching reserved egress IP;
- verified user/executor assignment;
- complete risk limits within approval caps.

The user readiness API returns every blocker without exposing credentials.

## 6. Live Routing

One verified Supertrend signal creates one `user_signal_job` and one unique `live_order_job` for each active approved Live subscription.

```text
User 1 -> EXECUTOR_001 -> 64.225.87.19
User 2 -> EXECUTOR_002 -> 152.42.157.165
```

The processor reloads the user, subscription, signal, approval, broker metadata, and assignment. It rechecks relay verification, kill switches, market hours, option side, quantity, daily trades, daily loss, duplicate identity, executor code, and reserved IP.

Dry-run jobs send a signed order-shaped request to the assigned executor. They contain a masked broker client ID and no access token. The main Hostinger process does not call Dhan and has no fallback to another executor.

Real jobs require:

```text
LIVE_PILOT_ENABLED=true
LIVE_ORDER_DRY_RUN_ONLY=false
ENABLE_LIVE_ORDERS=true
```

Even with those flags, Stage 6A blocks the real send with `credential_transport_not_configured`. This is intentional until Stage 6B implements and audits short-lived executor credential delivery.

## 7. Executor Service

- `GET /health`: returns `status=ok` and the configured executor code.
- `GET /egress-ip`: checks the executor's actual public egress IP.
- `POST /execute-order`: verifies timestamp, nonce, executor code, raw-body HMAC, and prohibited credential fields.

Signing input:

```text
<timestamp>.<nonce>.<raw_body>
```

Nonce receipts are durable and unique per executor. Replay returns `409`. Dry run never calls Dhan. Real mode requires `EXECUTOR_REAL_ORDERS_ENABLED=true`, but still fails closed until short-lived credential transport exists.

## 8. Frontend/Admin UX

The Live Pilot view shows broker status, approval, executor assignment, reserved-IP verification, Dhan whitelist state, risk state, route code/IP, dry-run status, and the backend blocker.

Admins can:

- inspect executor health, egress, assignment, and reserved IP;
- verify health and egress;
- assign an executor to a selected user;
- confirm assignment after Dhan IP whitelisting;
- approve a user for 24 hours with explicit risk caps.

The activation button remains disabled until backend readiness is true and is labeled `Start verified dry run`.

## 9. Tests Added

The 26 Stage 6A tests cover:

- executor registration, matching egress, and mismatch rejection;
- user assignment and one-user/one-executor exclusivity;
- prevention of routing through another user's executor;
- approval-required Live subscriptions;
- two isolated jobs from one Supertrend signal;
- User 1 to Executor 001 and User 2 to Executor 002;
- dry-run no-Dhan proof;
- real-order-disabled and kill-switch blocks;
- required executor signatures, replay rejection, and wrong-code rejection;
- forged relay-verification metadata rejection before executor contact;
- secret-field rejection without token logging;
- no direct Hostinger Dhan order path;
- idempotent same-user jobs and allowed same-signal multi-user jobs;
- user-scoped Live status;
- admin-only approval and assignment;
- relay token and approval gating.

## 10. Verification Results

| Command | Result |
|---|---|
| `python -m pytest app/tests/test_stage_6a_signing_relay_executor_egress.py -q` | `26 passed in 10.17s` |
| `python -m pytest app/tests -q` | `304 passed, 1 skipped in 111.36s` |
| Stage 5B -> 6A Alembic downgrade/upgrade round-trip | Passed |
| `python -m alembic check` | `No new upgrade operations detected.` |
| `python -m compileall -q app alembic` | Passed |
| `python scripts/check_repo_hygiene.py` | Passed |
| `python scripts/check_deployment_hardening.py` | Passed |
| Deployment Bash syntax checks | Passed |
| `npm run test:security` | Passed |
| `npm run lint` | Passed |
| `npm run build` | Passed, 2,174 modules transformed |
| `npm audit --omit=dev --audit-level=high` | Zero vulnerabilities |
| `git diff --check` | Passed; line-ending notices only |

## 11. Deployment Notes For Tomorrow

Required topology:

```text
Hostinger main app and live-pilot worker
Neon PostgreSQL central database
Executor 001: 64.225.87.19
Executor 002: 152.42.157.165
Dhan Account 1 whitelist: 64.225.87.19
Dhan Account 2 whitelist: 152.42.157.165
```

Manual dry-run sequence:

1. Back up Neon and run `python -m alembic upgrade head` from Hostinger.
2. Generate three independent 32+ character secrets: relay, Executor 001, Executor 002.
3. Deploy `app.executor_service.main:app` on each droplet behind HTTPS.
4. Configure each droplet with its own `EXECUTOR_CODE`, `EXECUTOR_RESERVED_IP`, and `EXECUTOR_SHARED_SECRET`.
5. Keep `EXECUTOR_REAL_ORDERS_ENABLED=false`.
6. On Hostinger set `EXECUTOR_SHARED_SECRETS_JSON` with both executor-code/secret pairs.
7. Register the two executor URLs through the admin UI/API.
8. Verify `/health`, then verify `/egress-ip`.
9. User 1 connects Dhan Account 1; User 2 connects Dhan Account 2.
10. Add the matching reserved IP to each Dhan account whitelist.
11. Assign User 1 to Executor 001 and User 2 to Executor 002; confirm both assignments.
12. Approve both users for `SUPERTREND_FLIP` with tiny risk limits and 24-hour expiry.
13. Set Hostinger dry-run flags:

```text
RELAY_ENABLED=true
LIVE_PILOT_ENABLED=true
LIVE_ORDER_DRY_RUN_ONLY=true
ENABLE_LIVE_ORDERS=false
EXECUTION_NODE_ROUTING_ENABLED=true
ENABLE_LIVE_PILOT_WORKERS=true
```

14. Start the dedicated worker with `python -m app.services.live_worker_runtime`.
15. Configure TradingView to post to `/relay/tradingview/SUPERTREND_FLIP` with the relay token.
16. Confirm each executor receives only its assigned user's dry-run request and reports its expected egress IP.

Do not switch to real flags in Stage 6A.

## 12. Remaining Risks

- Public Live launch is not approved.
- Automated DigitalOcean provisioning is not implemented.
- Executor deployment, TLS, firewall rules, and service supervision require real VPS testing.
- Short-lived secure credential transport to executors is not implemented; real orders therefore remain blocked.
- The first future real order must use tiny quantity and continuous manual monitoring.
- Load, broker-outage, retry, and recovery tests are still required before scale.

## 13. Go / No-Go Status

| Target | Decision |
|---|---|
| Paper beta | **Conditional GO** after production DB/worker/backup validation |
| 2-account live dry-run | **Conditional GO** after both executor deployments and real egress verification |
| 2-account tiny live pilot | **NO-GO in Stage 6A** |
| Public Live | **NO-GO** |
| 100-user production | **NO-GO** |

Next recommended stage:

`Stage 6B - Executor Deployment Scripts + Controlled Real Live Pilot Runbook`
