# Stage 5B Shared Queue, Worker, and State Applied

## 1. Summary

Stage 5B adds a PostgreSQL-backed durable queue, a standalone paper worker, SQL-backed paper positions and ledger entries, durable user notifications, operator visibility, and worker-safe asynchronous strategy fanout.

Production signal intake now stores the central signal and queues fanout. The web process does not execute queue jobs. Local/tests may explicitly enable bounded inline draining with `PAPER_QUEUE_INLINE_LOCAL=true`.

Live execution remains disabled. No Dhan execution, DigitalOcean executor, per-user egress routing, payments, or public live launch behavior was added.

## 2. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/app/auth/models.py` | Added worker jobs, heartbeats, paper positions, ledger, and notifications | Durable shared state |
| `backend/alembic/versions/20260614_0004_stage5b_shared_queue_worker_state.py` | Added Stage 5B tables, indexes, checks, and signal status/action expansion | Production schema migration |
| `backend/app/services/queue_service.py` | Added strict enqueue, atomic claim, retry, dead-letter, lock recovery, and counts | Shared PostgreSQL queue |
| `backend/app/services/worker_runtime.py` | Added standalone paper worker, dispatch, heartbeat, concurrency, and shutdown | Process-isolated execution |
| `backend/app/services/worker_policy.py` | Added `paper-worker` role and policy validation | Prevent role confusion |
| `backend/app/services/signal_fanout_service.py` | Replaced synchronous execution with queued fanout and paper jobs | Async fanout |
| `backend/app/services/paper_execution_service.py` | Added SQL position lifecycle, ledger, P&L, and SQL risk checks | Durable paper trading state |
| `backend/app/services/user_notification_service.py` | Added durable user-scoped event delivery records | Queue-safe notifications |
| `backend/app/services/chat_event_publisher.py` | Added generic user-scoped event publishing | Prevent cross-user broadcast |
| `backend/app/routers/strategies.py` | Added async intake, user state APIs, filters, and admin worker APIs | User/operator visibility |
| `backend/app/auth/router.py` | Added authenticated admin flag | Hide admin UI from non-admins |
| `backend/app/config.py`, `backend/.env*.example` | Added queue/worker settings | Explicit safe configuration |
| `deploy/layman-paper-worker.service` | Added hardened standalone worker unit | Production process boundary |
| `deploy/deploy_vps.sh`, `deploy/configure_vps_env.sh` | Installed, enabled, and validated the worker | Repeatable deployment |
| `.github/workflows/layman-backend-ci-deploy.yml` | Added PostgreSQL 17 CI service | Test production database behavior |
| `frontend/src/components/StrategyPlatform.tsx` | Added positions, ledger, queued states, and admin worker console | Operational UX |
| `frontend/src/api.ts`, `frontend/src/types.ts`, `frontend/src/App.tsx`, `frontend/src/index.css` | Added Stage 5B contracts, events, polling, and responsive styling | Complete frontend integration |
| `backend/app/tests/test_stage_5b_shared_queue_worker_state.py` | Added Stage 5B acceptance tests | Concurrency, retry, isolation, and accounting proof |

## 3. Durable Queue

`worker_jobs` stores `queued`, `processing`, `succeeded`, `failed`, `dead`, and `cancelled` jobs. Supported job types are limited to:

- `strategy_signal_fanout`
- `paper_signal_execution`
- `user_notification`

Payload schemas permit one positive integer ID only. Broker credentials, tokens, webhook secrets, cookies, and arbitrary fields are rejected.

Dedupe keys prevent duplicate fanout, paper execution, and notification jobs. Claiming uses one database `UPDATE` with a prioritized candidate subquery and `RETURNING`; PostgreSQL adds `FOR UPDATE SKIP LOCKED`. Expired locks are recovered. Failures use bounded exponential retry and transition to `dead` after `max_attempts`. Persisted error text redacts credential-like values.

## 4. Worker Runtime

Roles are:

- `web`: enqueue and API work only; never drains queue jobs.
- `paper-worker`: executes the three Stage 5B paper-safe job types when explicitly enabled.
- `trading-worker`: remains blocked for authenticated live trading.

The paper worker registers one heartbeat per concurrency slot, updates `last_seen_at`, supports SIGTERM/SIGINT shutdown, and validates production configuration before starting. Unexpected/live job types cannot be enqueued by the queue service.

## 5. Async Signal Fanout

Signal intake saves `strategy_signals`, atomically queues `strategy_signal_fanout`, sets `fanout_queued`, and returns the signal and queue IDs.

The fanout worker:

1. Sets `fanout_started`.
2. Finds active Paper subscribers.
3. Creates unique `user_signal_jobs`.
4. Queues one deduplicated `paper_signal_execution` job per user job.
5. Sets `fanout_completed`, or `fanout_failed` on failure.

Unique database constraints and queue dedupe keys make repeated fanout and paper execution idempotent.

## 6. Paper Execution State

BUY opens one long Paper position per user, subscription, and instrument. A second open attempt is skipped. SELL, EXIT, and CLOSE atomically close the matching open position.

Realized P&L is:

```txt
(exit price - average entry price) * quantity
```

`paper_ledger_entries` records fills, closes, realized P&L, and risk blocks. Daily trade count and realized loss are read from SQL and scoped by both `user_id` and `subscription_id`. Exits are not blocked by entry trade/loss limits. Missing positions produce `paper_rejected / no_open_position`.

## 7. User Notifications

Notifications are durable and user-scoped. Safe event types include:

- `strategy.signal.received`
- `strategy.job.queued`
- `strategy.job.processing`
- `strategy.job.completed`
- `strategy.job.skipped`
- `strategy.job.failed`
- `paper.position.opened`
- `paper.position.closed`

The existing in-memory websocket publisher can deliver only when the process owns the connected session. Therefore, DB notifications and user-scoped polling are authoritative in Stage 5B. No event broadcasts another user's job details.

## 8. Admin/Operator Visibility

Admin-only APIs:

- `GET /api/admin/worker/jobs`
- `GET /api/admin/worker/jobs/{id}`
- `GET /api/admin/worker/heartbeats`
- `POST /api/admin/worker/jobs/{id}/retry`

Compatibility aliases remain for `/api/admin/worker-queue` and `/api/admin/workers`. Non-admin users receive `403`. Queue payloads contain safe IDs only.

User-scoped APIs now include job filters, position list/detail, ledger date/limit filtering, and durable notifications.

## 9. Frontend UX

Strategy history displays queued, processing, filled, skipped, rejected, and failed states. New tabs show durable Paper positions and ledger entries. Authenticated admins additionally see queue counts, worker heartbeat status, failed/dead jobs, and a retry control. Non-admin users do not receive or render worker internals.

## 10. Tests Added

- `test_intake_is_durable_and_async_by_default`
- `test_worker_drains_fanout_and_opens_one_position_idempotently`
- `test_paper_position_close_writes_realized_pnl_ledger`
- `test_exit_without_position_is_auditable_rejection`
- `test_atomic_claim_allows_only_one_worker_to_take_job`
- `test_enqueue_claim_complete_job`
- `test_expired_lock_is_recovered_and_reclaimed`
- `test_failure_retries_then_dead_letters_and_admin_retry_resets`
- `test_worker_payload_schema_rejects_extra_or_secret_fields`
- `test_web_role_does_not_enable_paper_worker`
- `test_live_or_unknown_worker_job_type_is_rejected`
- `test_positions_and_ledger_are_user_scoped`
- `test_sql_daily_loss_is_scoped_per_user_and_subscription`
- `test_admin_queue_endpoints_require_admin`

Existing Stage 5A tests continue proving duplicate fanout/order protection and that Paper execution never calls Dhan or the live executor.

## 11. Verification Results

- `python -m pytest app/tests -q`: `278 passed, 1 skipped`
- Stage 5B tests: `14 passed`
- Clean migration through `20260614_0004`: passed
- `python -m alembic check`: `No new upgrade operations detected`
- `python -m compileall -q app alembic`: passed using a disposable bytecode cache
- `npm run test:security`: passed
- `npm run lint`: passed
- `npm run build`: passed
- `npm audit --omit=dev --audit-level=high`: `found 0 vulnerabilities`
- `python scripts/check_deployment_hardening.py`: passed
- Bash syntax checks for deployment/configuration/backup scripts: passed
- `git diff --check`: passed
- Local `bandit` and `pip-audit`: unavailable; CI installs and runs both

## 12. Remaining Risks

- Public live launch is not approved.
- Live beta remains blocked.
- Stage 6 signing relay and per-user executor/egress routing are not implemented.
- A shared Redis/pub-sub layer may be required when websocket delivery must span processes.
- The production VPS still requires real PostgreSQL migration, paper-worker heartbeat, systemd restart, and backup/timer validation.
- Payments and paid plans are not implemented.
- Queue throughput and connection-pool sizing require load tests before a 100-user production launch.

## 13. Go / No-Go Status

- Multi-user Paper beta: **Conditional GO** after real VPS PostgreSQL migration and worker validation.
- Live beta: **NO-GO**.
- Public live launch: **NO-GO**.
- 100-user production launch: **NO-GO** pending load, recovery, and shared pub/sub validation.

Next recommended stage:

`Stage 6 — Signing Relay + Per-User Executor/Egress Routing`
