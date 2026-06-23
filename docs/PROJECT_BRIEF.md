# NOVA / Layman Signal Router — Project Brief

A full-context overview of what this project is, how it flows, how multi-user works,
and what each part of the code does.

> Note: a few files below are described from their name + imports + role in the
> flow rather than a line-by-line read; the core money-path and multi-user files
> were reviewed directly.

---

## 1. What this project is

A **multi-user options trading signal router**. One TradingView strategy (currently
`supertrend` on NIFTY options) fires a single webhook alert; the backend **fans it
out** to every user subscribed to that strategy and executes it **independently for
each user** — with that user's own Dhan credentials, their own lot size, their own
runtime state, and (for real orders) their own whitelisted static IP.

Three execution modes per user:
- **signal_only** — compute/record the signal, place no order.
- **paper_live_data** — real market data, simulated (paper) fills, no real money.
- **real_orders** — real Dhan orders, routed out of the user's whitelisted IP.

The UI is a chat-style "conversational" control panel. The browser is only a control
surface — execution happens entirely server-side, so trading continues even if the
browser is closed.

---

## 2. Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy 2.x, psycopg (PostgreSQL driver), httpx, cryptography (Fernet), itsdangerous (cookie signing). Background work runs as in-process daemon threads.
- **Database:** Neon PostgreSQL (durable multi-user state). Per-user runtime state also kept in JSON files on disk, isolated per user.
- **Frontend:** React 19 + TypeScript + Vite + Tailwind + zustand, talking to the backend over HTTPS + a WebSocket.
- **Infra:** Control-plane VPS (FastAPI + nginx) + one DigitalOcean droplet per live user acting as a static-IP egress proxy for Dhan order placement. Frontend can be hosted on Vercel.

---

## 3. High-level architecture

```
                         ┌─────────────── Browser (chat UI) ───────────────┐
                         │  React app: login, setup, paper/live control     │
                         └───────────────┬──────────────────────────────────┘
                            HTTPS + WSS (cookie auth, credentials: include)
                                         │
TradingView ──webhook──▶  ┌──────────────▼───────────────────────────────┐
(one alert per strategy)  │   Control plane VPS  (FastAPI + Postgres)      │
                          │   auth · vault · fan-out · risk · job queue    │
                          └───────┬───────────────────────────┬───────────┘
                       per-user job (real_orders)        market data (shared TOTP account)
                                  │                            │
                    ┌─────────────▼────────┐         ┌─────────▼─────────┐
                    │ Droplet A (IP_A) proxy│         │ Dhan market-data  │
                    │  → Dhan (User A creds)│         │ (LTP, option chain│
                    └───────────────────────┘         │  WebSocket feed)  │
                    ┌───────────────────────┐         └───────────────────┘
                    │ Droplet B (IP_B) proxy│
                    │  → Dhan (User B creds)│
                    └───────────────────────┘
```

---

## 4. End-to-end flow

### Login
1. User opens the app → `/api/me` says not authenticated → login screen.
2. "Continue with Google" → `/api/auth/google/start` → Google consent → `/api/auth/google/callback`.
3. Backend verifies the Google profile, upserts a `users` row in Neon, creates a `user_sessions` row, and sets a **signed, HTTP-only session cookie**. Browser only ever holds the cookie.
4. Any verified Google account may log in. `ADMIN_EMAILS` only flags `is_admin` (gates `/api/admin/*`) — it never blocks login.

### Setup (per user, via the chat flow)
- Pick mode (paper/live), strategy (`supertrend`), enter **Dhan client id + access token** (stored encrypted), select a **static IP / egress droplet**, set **lots** and **exit rules**, then confirm.
- Dhan credentials are encrypted server-side and never returned to the browser (only masked status).

### Signal → fan-out → execution
1. TradingView posts one alert to `/api/webhook/strategy/{strategy}` with a shared `secret`.
2. Backend validates secret + strategy + payload, **dedupes by `signal_id`** (unique row in `strategy_signals`), and writes **one `strategy_execution_jobs` row per active subscriber** — atomically.
3. The **durable job worker** claims queued jobs (`SELECT … FOR UPDATE SKIP LOCKED`), serializes per user, and for each job binds that user's execution context (creds + egress proxy) and runs the signal through the execution router.
4. ENTRY → BUY=CE / SELL=PE; EXIT → close the tracked position (blocked safely if none open).
5. For **real_orders**, the Dhan HTTP call is routed through the user's droplet proxy so it egresses from their whitelisted IP. The client **fails closed** if routing is on but no proxy is assigned.

### Live-mode safety gates (all must pass for a real order)
`ENABLE_LIVE_ORDERS=true` · `DHAN_MODE=REAL` · `DHAN_READ_ONLY_REAL_DATA=false` ·
`EXECUTION_NODE_ROUTING_ENABLED=true` · user has saved Dhan creds · user has an
egress node **assigned and verified** (`observed_ip == whitelisted_ip` within 24h) ·
worker running. Anything short of this is blocked or downgraded to paper.

---

## 5. Multi-user isolation (how User A and User B stay separate)

- **Identity:** each user = one `users` row (UUID). Session cookie → `user_sessions` → `CurrentUser`.
- **Credentials:** per-user Fernet-encrypted vault (`user_credential_vaults`). Decryption is server-side only; frontend sees masked status.
- **Runtime state:** every runtime file read/write is routed through `scoped_runtime_path()`, which keys off the active user's execution context and redirects to `runtime_state/users/<user_id>/…` and `runtime_logs/users/<user_id>/…`. So positions, wallet, logs are physically separate per user. (The local dev user maps to the global folders for backward compatibility.)
- **Execution:** the job worker processes each user's job under a per-user lock with that user's creds + egress bound via a `ContextVar`, so two users' orders never cross.
- **Egress / compliance:** unique static IP per user (`user_egress`, DB-enforced one-IP-per-user), each whitelisted in that user's Dhan account.
- **Market data is shared** (the one thing intentionally global): a dedicated data-only Dhan account (auto-refreshed via TOTP) serves LTP/feed for everyone, so paper users don't need their own token.

---

## 6. Database tables (Neon)

| Table | Purpose |
|---|---|
| `users` | One per Google user (email, google_sub, is_admin, timestamps). |
| `user_sessions` | Server-side cookie sessions (expiry, revocation). |
| `user_credential_vaults` | Per-user encrypted Dhan client id / token / webhook secret. |
| `strategy_subscriptions` | Which user is subscribed to which strategy, with lots + execution_mode. |
| `strategy_signals` | One row per inbound TradingView alert (idempotency key = signal_id). |
| `strategy_execution_jobs` | One durable per-user job per signal (status, attempts, result). |
| `user_egress` | Per-user static IP + encrypted proxy URL + verification state. |
| `user_runs` | Per-user run history (paper/live, status, errors). |
| `audit_logs` | Per-user security/trade audit trail. |

Plus per-user JSON runtime files: `open_position.json`, `paper_portfolio.json`,
`app_state.json`, `settings.json`, etc., under `runtime_state/users/<user_id>/`.

---

## 7. File-by-file (backend)

### Entry + config
- `app/main.py` — FastAPI app: lifespan startup (validate prod config, init DB schema, start workers: shared-token, EOD square-off, strategy jobs, position monitors), CORS (credentials enabled, explicit origins), and wiring of every router. Production startup **fails loudly** if required secrets/DB are missing.
- `app/config.py` — All settings via env (`pydantic-settings`): DB URL, auth/Google, secrets + encryption keys (with legacy fallbacks), Dhan flags, live/webhook gates, shared-data account, egress nodes JSON, runtime dirs.

### Database
- `app/db/engine.py` — Neon URL normalizer (`postgresql://` → `postgresql+psycopg://`, preserving query params), lazy engine, `session_scope()`, `init_db()`.
- `app/db/models.py` — SQLAlchemy models for all tables above (portable UUID + JSON types so the same models run on Postgres and on SQLite in tests).
- `app/db/crud.py` — Data-access helpers: user upsert, sessions, runs, audit logs.

### Auth
- `app/auth/session.py` — Signed HTTP-only session cookie + OAuth state signing (itsdangerous).
- `app/auth/google.py` — Routes: `/api/auth/google/start|callback`, `/api/auth/logout`, `/api/me`. Validates Google profile, upserts user, sets cookie.
- `app/auth/dependencies.py` — `get_current_user` / `get_optional_user` / `require_admin` / WebSocket variant; resolves cookie → DB session → `CurrentUser`, with dev-user fallback when `AUTH_REQUIRED=false`.

### Multi-user services
- `app/services/user_context.py` — `CurrentUser`, deterministic dev user, `is_admin_user()`, and per-user runtime path helpers.
- `app/services/user_credential_vault.py` — Per-user encrypted (Fernet) Dhan/webhook credentials; masked status; server-side decrypt.
- `app/services/execution_context.py` — Request-local `ContextVar` carrying the active user + their proxy/egress IP, so deep code (Dhan client, state store) routes per user.
- `app/services/strategy_fanout.py` — The fan-out core: subscriptions, single-signal claim + per-user job creation, egress assignment/selection/verification, and `dispatch_signal_job()` (runs one user's job with all safety gates re-checked).
- `app/services/shared_market_data.py` — Dedicated data-only Dhan account; mints/refreshes its token from a TOTP secret on a background thread; `market_data_credentials()` so paper/data reads don't need per-user tokens.
- `app/services/live_engine.py` — Live readiness checklist + per-user run registry (start/stop/status/logs), defense-in-depth gating.

### Trading engine (shared core, made per-user via context)
- `app/services/execution_router.py` — Heart of order routing: builds the Dhan order (with `correlationId`), pre-trade reconciliation (blocks duplicate/known open positions), token preflight (now paper-aware), risk checks, then places via the right broker client.
- `app/services/dhan_client.py` — Real Dhan API client; builds the httpx client with the **per-user egress proxy** for live orders (fails closed without a proxy when routing is enabled); also the mock/paper path selector.
- `app/services/paper_broker.py` / `paper_portfolio.py` — Simulated fills, slippage, charges, and paper wallet/portfolio.
- `app/services/risk_manager.py` — Per-user risk limits (max trades, max loss, side, qty, kill switches).
- `app/services/signal_parser.py` / `signal_validator.py` — Parse + normalize TradingView payloads into a `NormalizedSignal`; validate (NIFTY-only, intraday, etc.).
- `app/services/state_store.py` — File-backed runtime state with `scoped_runtime_path()` for **per-user isolation**; app state, open position, settings, daily risk, logs.
- `app/services/instrument_resolver.py` / `security_id_resolver.py` — Resolve option contract → Dhan security id (scrip master).
- `app/services/option_position_monitor.py` / `position_reconciler.py` / `startup_reconciler.py` — Track open option position, reconcile local vs Dhan, recover on startup.
- `app/services/wallet_service.py` — Funds/wallet snapshot (real + paper).
- `app/services/audit_logger.py` — JSONL audit/webhook/order/error logs with secret masking.
- `app/services/dhan_marketfeed_ws.py` — Dhan market-data WebSocket (uses shared market creds).
- `app/services/dhan_rate_limiter.py`, `dhan_error_interpreter.py`, `normalized_errors.py`, `dhan_debugger.py` — Rate limiting, human-readable Dhan errors, outgoing-IP debug.
- `app/services/credential_vault.py` — Legacy single (global) vault; still used for the shared/data fallback and token-age metadata.
- `app/services/portfolio_analytics.py`, `chat_event_publisher.py` — Dashboard analytics; pushes engine events into the chat session feed.

### Workers (daemon threads)
- `app/workers/strategy_job_worker.py` — Durable executor for fan-out jobs: claim with `SKIP LOCKED`, per-user lock, retries, stale-job recovery, signal-summary rollup.
- `app/workers/eod_squareoff.py` — End-of-day square-off; iterates active routing users and exits each user's position from their egress.
- `app/workers/ghost_position_watcher.py` — Watches for broker positions not tracked locally (drift / manual trades).

### Routers (HTTP/WS APIs)
- `app/routers/strategies.py` — Subscriptions, the single fan-out webhook (`/api/webhook/strategy/{name}`), egress options/select/verify, admin egress assign.
- `app/routers/live.py` — `/api/live/start|stop|status|logs|readiness` (per-user).
- `app/routers/user_credentials.py` — `/api/user/credentials` GET status / POST / DELETE (masked only).
- `app/routers/user_webhook.py` — Per-user HMAC webhook variant (`/api/webhook/user/{id}`) with replay protection.
- `app/routers/admin.py` — `/api/admin/users|runs|health` (admin-gated).
- `app/routers/market.py` — Market snapshot / shared-data status.
- `app/routers/setup.py`, `engine.py`, `control.py`, `broker.py`, `orders.py`, `positions.py`, `dashboard.py`, `debug.py`, `webhook.py` — The original single-tenant engine/setup/order/position/control surface (still used by the chat flow).
- `app/api/ws.py` — WebSocket session: receives chat commands (`setup.confirm_live`, pause, etc.), enforces the live arm gates, drives the engine; `app/api/session.py` bootstraps a chat session.

### Other
- `app/store/session_token.py`, `redis_session.py` — Chat session token + in-memory session store.
- `app/strategies/supertrend.py` — Strategy definition/metadata.
- `app/schemas/*` — Pydantic request/response models.
- `scripts/init_db.py` — Create + verify the Neon schema. `scripts/check_env.py` — Validate env config.
- `app/tests/*` — Test suite (paper-mode regression, fan-out, auth, isolation, live guards, vault, etc.).

---

## 8. Frontend (key files)

- `src/App.tsx` — Root: auth gate, session bootstrap, WebSocket wiring, central `send()` command dispatch (with double-click guard), navigation.
- `src/api.ts` — All HTTP calls (`credentials: 'include'`): `/api/me`, login URL, logout, session, egress options/select/verify, manual orders, market/health.
- `src/ws.ts` — WebSocket client (reconnect, dedupe, status).
- `src/state/sessionStore.ts` — zustand store: messages, setup draft, engine mode, wallet/PnL, reduces server events into UI state.
- `src/components/AuthScreen.tsx` — Login screen. `Header.tsx` — user menu, logout, kill switch, health.
- `src/components/setup/*` — Setup chat cards (strategy, broker, risk, exits, confirm-launch).
- `src/components/ManualOrderPanel.tsx` — Manual buy/exit/reverse (pending-guarded; hold-to-confirm for live).
- `src/components/messages/SetupInfoCard.tsx` — Static-IP (egress) select + verify UI.
- `src/dashboard/*` — Portfolio dashboard.

---

## 9. Configuration (env) — the important ones

- **DB:** `DATABASE_URL` (Neon).
- **Auth:** `AUTH_REQUIRED`, `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI`, `FRONTEND_URL`, `ADMIN_EMAILS`.
- **Secrets:** `APP_SECRET_KEY` (cookie signing), `CREDENTIAL_ENCRYPTION_KEY` (Fernet) — with `SESSION_TOKEN_SECRET` / `TOKEN_ENCRYPTION_KEY` as legacy fallbacks.
- **Live gates:** `ENABLE_LIVE_ORDERS`, `DHAN_MODE`, `DHAN_READ_ONLY_REAL_DATA`, `EXECUTION_NODE_ROUTING_ENABLED`.
- **Fan-out / egress:** `STRATEGY_WEBHOOK_SECRET`, `EGRESS_NODES_JSON`.
- **Shared market data:** `DHAN_SHARED_DATA_ENABLED`, `DHAN_SHARED_CLIENT_ID`, `DHAN_SHARED_PIN`, `DHAN_SHARED_TOTP_SECRET`.

See `backend/.env.example` and `backend/.env.production.example`. Validate with `python -m scripts.check_env`.

---

## 10. Current state & known gaps (for context)

Working: Google login, per-user encrypted vault, per-user runtime isolation, paper
mode (shared-data token), fan-out + durable job queue, per-user egress proxy routing
with IP verification, live safety gates, EOD square-off per user, double-click guards.

Worth hardening before scaling beyond a couple of trusted users (see `ARCHITECTURE_REVIEW`
if present): strategy webhook uses a static shared secret (consider HMAC+timestamp+nonce
+ IP allowlist); at-least-once job execution has a small duplicate-order race window
(mitigated by reconciliation + correlationId); stale-job recovery runs at worker startup
only; adopt Alembic migrations instead of `create_all`; add a master kill switch and
per-user notional caps; multi-strategy-at-once needs per-(user,strategy) state keying first.
