# Stage 5A Strategy Subscription and Paper Fanout Applied

Date: 2026-06-14

## 1. Summary

Stage 5A adds the product and persistence layer for Nova's multi-user Paper
strategy flow:

```text
central strategy signal
-> one durable strategy_signal
-> one isolated user_signal_job per active subscription
-> per-user risk decision
-> durable simulated Paper order or explicit skipped/rejected reason
```

This enables the intended authenticated strategy marketplace, Paper
subscription, central signal fanout, user-scoped history, and auditable Paper
fill flow. Signals are shared; subscription risk, jobs, orders, and
notifications remain user scoped.

Live trading is not enabled. `ENABLE_LIVE_ORDERS=false` remains the default,
all seeded strategies have `is_live_allowed=false`, Live subscriptions are
rejected, and Stage 5A does not call Dhan or an executor node.

## 2. Files Changed

| File | Change | Reason |
| --- | --- | --- |
| `backend/app/auth/models.py` | Added strategy, version, subscription, central signal, user job, and Paper order SQL models with constraints. | Durable multi-user Paper fanout state. |
| `backend/alembic/versions/20260614_0003_stage5a_strategy_paper_fanout.py` | Added schema migration, indexes, uniqueness constraints, checks, and five seeded strategies. | Production PostgreSQL schema and catalog bootstrap. |
| `backend/app/config.py` | Added `FREE_MAX_ACTIVE_PAPER_STRATEGIES`. | Configurable free/default plan limit. |
| `backend/.env.example`, `backend/.env.live.example` | Added the safe free-plan default. | Environment documentation. |
| `deploy/configure_vps_env.sh` | Writes the free-plan strategy limit and keeps Live disabled. | Reproducible VPS configuration. |
| `backend/app/services/strategy_catalog_service.py` | Added idempotent catalog/version seeding and lookup. | Safe local/test bootstrap and catalog reads. |
| `backend/app/services/subscription_service.py` | Added subscribe, list, pause, resume, disable, user locking, and plan enforcement. | User-scoped Paper subscription lifecycle. |
| `backend/app/services/strategy_signal_service.py` | Added normalized central signal persistence, recursive redaction, hashing, and duplicate rejection. | Store one safe signal record. |
| `backend/app/services/signal_fanout_service.py` | Added synchronous active-subscriber fanout and per-job failure isolation. | One durable job per eligible user. |
| `backend/app/services/paper_execution_service.py` | Added risk checks and SQL-backed simulated signal-price fills. | Paper execution without Dhan or executor routing. |
| `backend/app/routers/strategies.py` | Added catalog, subscription, history, job detail, and admin intake APIs. | Authenticated product API surface. |
| `backend/app/main.py` | Seeds the catalog at startup and mounts the strategy router. | Runtime integration. |
| `backend/app/domain/events.py` | Added the `strategy.job` event type. | Typed user job notifications. |
| `backend/app/store/redis_session.py` | Added active-session lookup by `user_id`. | Prevent cross-user websocket fanout. |
| `backend/app/services/chat_event_publisher.py` | Added user-filtered strategy job notification publishing. | Fast UI refresh without shared events. |
| `backend/app/tests/conftest.py` | Added Stage 5A defaults, seeding, and SQL cleanup. | Deterministic test isolation. |
| `backend/app/tests/test_stage_5a_strategy_paper_fanout.py` | Added 19 Stage 5A acceptance/security tests. | Fanout, auth, isolation, risk, and no-Dhan proof. |
| `frontend/src/components/StrategyPlatform.tsx` | Added marketplace, risk form, subscriptions, controls, and signal history. | Usable Paper strategy product flow. |
| `frontend/src/api.ts`, `frontend/src/types.ts` | Added typed authenticated strategy APIs and data contracts. | Safe frontend/backend integration. |
| `frontend/src/App.tsx` | Mounted the platform and refreshes on user-specific job events. | Dashboard integration. |
| `frontend/src/index.css` | Added responsive operational strategy UI styles. | Desktop and mobile usability. |
| `frontend/scripts/check-ui-safety.mjs` | Added static Paper/Live lock, reason, confirmation, and secret checks. | Prevent UI safety regressions. |

## 3. Strategy Catalog

The migration and idempotent startup seed create:

- `ORB_PORTAL`
- `SUPERTREND_FLIP`
- `SUPPORT_RESISTANCE_REVERSAL`
- `OOPS_GAP_REVERSAL`
- `NIFTY_MOMENTUM_SCALPER`

Each strategy is active, Paper allowed, and Live disallowed. Each has one
active version with a basic signal schema. Subscriptions and signals reference
the resolved active version, allowing future versions to be activated or
retired without rewriting historical jobs.

## 4. User Subscriptions

`POST /api/me/strategy-subscriptions` accepts Paper mode and explicit quantity,
daily loss, daily trade, and CE/PE/BOTH controls. Live mode returns HTTP 403.

Pause, resume, and remove operations always include both subscription ID and
the authenticated `user_id`. Remove is a soft disable so historical jobs and
orders remain auditable.

Before activation/resume, the service locks the user row and counts active
Paper subscriptions. The default free-plan limit is one. Paused subscriptions
do not count as active. A second active strategy returns HTTP 403 with a clear
message.

## 5. Central Signal Intake

Endpoint:

```text
POST /api/strategy-signals/intake
```

The endpoint requires a valid authenticated session and an email present in
`ADMIN_EMAILS`. Empty admin configuration fails closed. Therefore anonymous,
unsigned production intake is rejected.

The payload contains strategy code, signal ID, instrument fields, timeframe,
optional signal price, and source. Extra payload fields may be retained only
after recursive redaction of token, secret, authorization, cookie, Dhan client,
and webhook-secret keys.

The database unique key is:

```text
(strategy_id, strategy_version_id, signal_id)
```

A duplicate returns HTTP 409 and creates no additional signal, job, or order.
The success response contains only the central signal ID and aggregate created
and skipped counts; it exposes no subscriber identity or user risk data.

## 6. Paper Fanout

Fanout selects only active Paper subscriptions for the exact strategy and
active version. It creates one `user_signal_job` per subscription using:

```text
(user_id, strategy_signal_id, subscription_id)
```

as the database uniqueness boundary.

Jobs transition through queued and processing to a durable final status:

- `paper_filled`
- `paper_rejected`
- `skipped_risk_not_set`
- `skipped_daily_trade_limit`
- `skipped_daily_loss_limit`
- `skipped_market_closed`
- `skipped_invalid_instrument`
- `skipped_duplicate`
- `skipped_subscription_paused`
- `failed`

Subscriptions already paused before fanout receive no job. Execution rechecks
subscription state to handle a pause race safely. An unexpected failure is
recorded on that user job and does not stop other subscribers from processing.

No fanout code imports or invokes Dhan order placement or executor routing.

## 7. Paper Execution

Paper execution:

- revalidates `user_id`, subscription status, and Paper mode;
- requires configured quantity, daily loss, and daily trade controls;
- restricts signals to valid NIFTY option fields and the allowed option side;
- applies market-hours policy when enabled;
- checks today's per-subscription fill count and realized loss;
- fills at the supplied positive signal price;
- records missing price as `paper_rejected`;
- stores a `paper_strategy_orders` row containing `user_id`;
- links the durable Paper order to the user job.

Stage 5A stores realized P&L for future lifecycle work, but it does not yet
implement a complete strategy-position exit/P&L engine. Daily-loss enforcement
uses realized P&L already present in the SQL ledger.

User-specific websocket notifications are sent only to matching active
sessions. The frontend also polls every 15 seconds, which is the authoritative
update path until Stage 5B provides shared pub/sub.

## 8. Frontend UX

The authenticated dashboard now includes:

- a five-strategy marketplace;
- risk level, timeframe, Paper availability, and Live lock state;
- quantity, loss, trade-count, and option-side controls before activation;
- a disabled Live button with executor/signing-relay explanation;
- the free-plan active-strategy limit message;
- My Strategies with active/paused state, daily counts, and last signal;
- pause, resume, and typed `REMOVE` confirmation;
- signal history with instrument, status, Paper fill, and stored reason;
- immediate refresh from user-scoped websocket events plus safe polling.

The existing Stage 3 safety banner and readiness views remain unchanged and
continue to state that Live is disabled.

## 9. Tests Added

The Stage 5A test module covers:

- seeded strategy catalog and active versions;
- authentication on catalog APIs;
- Paper subscription creation;
- Live subscription rejection;
- free-plan one-active-strategy limit;
- pause and resume;
- central signal single-save and duplicate rejection;
- multi-user fanout and separate user orders;
- paused subscription receiving no job/order;
- subscription and job cross-user isolation;
- duplicate fanout protection;
- Paper execution without Dhan or executor calls;
- unexpected per-user failure recording and continued fanout;
- daily trade-limit skip reason;
- missing-price Paper rejection;
- user-scoped signal history;
- unauthenticated production intake rejection;
- recursive signal payload redaction;
- user-scoped soft removal.

Frontend security checks cover Paper activation, disabled Live controls, free
plan messaging, skipped-reason rendering, typed removal confirmation, shared
authenticated API use, and absence of token rendering.

## 10. Verification Results

| Command | Result |
| --- | --- |
| `python -m pytest app/tests/test_stage_5a_strategy_paper_fanout.py -q` | Passed: `19 passed in 7.38s`. |
| `python -m pytest app/tests -q` | Passed: `264 passed, 1 skipped in 82.31s`. |
| `python -m alembic upgrade head` on a copied Stage 2 SQLite database | Passed; upgraded to `20260614_0003`. |
| `python -m alembic check` | Passed: `No new upgrade operations detected.` |
| Migration seed query | Passed: five strategies and five active versions. |
| `python -m compileall app` | Could not write inherited Windows-owned `__pycache__` files. |
| Source-only Python compile | Passed: `106 Python files compiled from source`. |
| `python scripts/check_repo_hygiene.py` | Passed. |
| `python scripts/check_deployment_hardening.py` | Passed. |
| `bash -n deploy/configure_vps_env.sh` | Passed. |
| `npm.cmd run test:security` | Passed: API and UI safety checks. |
| `npm.cmd run lint` | Passed. |
| `npm.cmd run build` | Passed: Vite 8.0.16, 2,173 modules transformed. |
| `npm.cmd audit --omit=dev --audit-level=high` | Passed: zero vulnerabilities. |
| `git diff --check` | Passed; Windows LF-to-CRLF notices only. |
| `bandit -r app -ll -ii` | Not run: Bandit is not installed locally. |
| `pip-audit -r requirements.txt` | Not run: pip-audit is not installed locally. |

## 11. Remaining Risks

- Public Live launch is not approved.
- Single trusted operator Live remains blocked.
- Stage 5B shared queue, distributed worker coordination, shared websocket
  pub/sub, and broader SQL trading-state migration remain required.
- Stage 6 signing relay and executor/per-user IP routing remain required.
- Payments and paid-plan enforcement are not implemented.
- Full Paper strategy position lifecycle and realized P&L updates remain
  limited; Stage 5A focuses on signal-price entry/fill auditing.
- Synchronous fanout is appropriate for the current constrained beta but must
  move to a durable shared queue before larger scale or multi-worker use.
- Real VPS PostgreSQL migration, backup/restore, nginx, systemd, monitoring,
  and Paper beta behavior still require deployment validation.

## 12. Go / No-Go Status

| Launch target | Decision |
| --- | --- |
| Multi-user Paper beta | **Conditional GO** after real VPS migration, readiness, backup/restore, monitoring, and end-to-end Paper validation. |
| Live beta | **NO-GO**. |
| Public Live launch | **NO-GO**. |
| 100-user production launch | **NO-GO**. |

Next recommended stage: **Stage 5B - Shared Queue, Worker, and Trading State Foundation**
