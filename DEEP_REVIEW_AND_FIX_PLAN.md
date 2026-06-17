# Nova Trading / Layman Signal Router — Deep Production Review & Fix Plan

**Reviewer role:** Principal Architect + Senior Backend + AppSec + FinTech Production-Readiness
**Date:** 2026-06-13
**Method:** Direct code read of `backend/app/**`, `frontend/src/**`, `deploy/**`, `.github/**`, env templates, and the two prior security docs. Static checks run where the sandbox allowed (pytest, compileall, tsc, secret grep). Findings are grounded in code that was actually read, not in the prior docs.

> **One-line verdict:** Functionally rich, but **not production-ready for multi-user real money.** Authentication exists but is **not enforced on the endpoints that matter**, and user-scoped storage **silently falls back to a shared global path**. Today the system behaves as a single-tenant prototype with real-money capability. Safe path: keep it single-user paper/live for one trusted operator while Stage 0–2 below are completed.

---

## 1. Executive Verdict

- **Production readiness verdict:** NOT READY for 100 users or public live trading. Beta-ready for a **single trusted operator** only.
- **Can 100 users safely use this today?** No. Multi-tenant isolation is not enforced end to end (see P0-01, P0-02, P0-03). Two users hitting the backend without per-endpoint auth + scoped storage can read/overwrite each other's credentials, engine state, and positions.
- **Can live money be enabled today?** Only for one operator on a single worker, with `ENABLE_LIVE_ORDERS` flipped deliberately, after manual IP whitelisting. Not for general users.
- **Biggest blockers:**
  1. Auth is not enforced per endpoint — most state-changing routes have no guard (P0-01).
  2. User-scoped files fall back to a global path when user context is missing (P0-02).
  3. Frontend never sends the auth cookie (`credentials` omitted), so the app currently runs effectively unauthenticated cross-origin (P0-03).
  4. Background workers + JSON runtime state are single-process and unscoped; multi-worker = duplicate square-offs and corruption (P0-04).
  5. No webhook replay protection; sensitive runtime artifacts (OAuth client JSON, auth DB, logs) live in the deliverable folder (P0-05, P0-06).
- **Recommended launch mode:** Single-operator **paper** first → single-operator **live** with one whitelisted IP → only then multi-user beta after Stage 1–2.
- **Required before beta (multi-user paper):** P0-01, P0-02, P0-03, P0-04, P0-06, plus CSRF and control-endpoint auth (P1 block).
- **Required before live public launch:** All P0 + all P1, Alembic migrations, per-user egress execution nodes wired and verified, monitoring/alerting, backup/restore drill.

---

## 2. Reviewed Scope

Reviewed in depth:
- `backend/app/` — `main.py`, `config.py`, `auth/*`, `middleware/user_scope.py`, `store/session_token.py`, `routers/*` (setup, engine, control, orders, positions, dashboard, broker, debug, webhook, connections), `api/{session,ws,webhook}.py`, `services/*` (credential_vault, user_connections, user_context, state_store, execution_router, dhan_client, risk_manager, signal_parser), `workers/*`, `tests/*`.
- `backend/.env.example`, `backend/.env.live.example`, `requirements.txt`, `requirements_test.txt`.
- `frontend/src/` — `api.ts`, `lib/backend.ts`, `ws.ts`, `state/sessionStore.ts`, setup components (BrokerForm, ConfirmLaunchCard, RiskForm, StrategyPicker, SetupPanel), QuickActions, message/event cards.
- `frontend/package.json`, `vite.config.ts`.
- `deploy/` — nginx conf, systemd unit, `deploy_vps.sh`, `configure_vps_env.sh`, `enable_layman_api_tls.sh`, `install_restricted_ci_key.sh`, `DIGITALOCEAN_PER_USER_IP_SETUP.md`.
- `.github/workflows/layman-backend-ci-deploy.yml`.
- `SECURITY_REVIEW_2026_06_12.md`, `SECURITY_FIXES_APPLIED_2026_06_12.md`.

Not deeply reviewed (vendored/generated): `.git/`, `frontend/node_modules/`, `frontend/dist/`, `__pycache__/`, `.pytest_cache/`. See §19 / §25 for whether these belong in the repo/deploy package.

---

## 3. Commands Run and Results

| Area | Command | Result | Notes |
|---|---|---|---|
| Backend tests | `pytest app/tests -q` | **172 passed** (after applying the prior fix batch + a Py3.10 sandbox shim) | Sandbox is Python 3.10; project targets 3.14. Two shims needed (`StrEnum`, sqlmodel JSON column) to even import — see Testing Gaps. |
| Backend compile | `python -m compileall app` | **exit 0** | No syntax errors. |
| Frontend types | `tsc -b` | **exit 0** | Type-clean. |
| Frontend build | `npm run build` | **FAILED** | `rolldown` native binding MODULE_NOT_FOUND — `node_modules` was installed on a different OS and synced via OneDrive. Platform artifact, not a code defect; rebuild on the target OS. |
| Frontend console hygiene | `grep console.log src/` | **0 hits** | No console secret leakage. |
| SAST | `bandit -r app` | **not run** | bandit not installed in sandbox. Add to CI. |
| Dependency audit | `pip-audit` / `npm audit` | **not run** (pip-audit absent; npm audit unreliable against synced modules) | Add both to CI. |
| Secret scan | `git ls-files | grep -E 'client_secret|.env|.sqlite|runtime'` | **No secrets tracked in git** | Good. But sensitive files exist **on disk** in the working folder (see P0-06). |

---

## 4. Current Architecture Summary

**Frontend** (Vite + React + TS + Tailwind + Zustand): a chat-style single-page app. `lib/backend.ts` reads `VITE_BACKEND_URL`; `api.ts` calls REST endpoints; `ws.ts` opens a session websocket. State in a Zustand `sessionStore`. Setup flow walks broker connect → webhook secret → risk → strategy → launch.

**Backend** (FastAPI): one process serving REST + websocket. `main.py` builds the app, adds `UserRuntimeScopeMiddleware` (sets a `current_user_id` context var from the auth cookie) and CORS, then mounts routers. Auth is Google OAuth (`auth/router.py`) issuing a signed cookie (HMAC, not JWT) verified in `auth/security.py` against an `auth_sessions` row.

**Broker/trading:** `execution_router.py` is the core — builds the Dhan order payload, runs risk gates, and (in live) is meant to route through a per-user egress node. `dhan_client.py` talks to Dhan with a correlationId for timeout reconciliation. Workers (`option_position_monitor`, `eod_squareoff`, `ghost_position_watcher`) are daemon threads.

**Storage:** auth/users/sessions/egress in SQL (SQLite dev, Postgres prod) via SQLModel. Trading runtime state (app_state, positions, paper portfolio, seen signals, settings) in **per-file JSON** under `runtime_state/`, optionally scoped to `users/<id>/`. Credentials in a Fernet-encrypted `credentials.enc.json` (or in-memory for local mock).

**Deployment:** single VPS, systemd-run uvicorn on `127.0.0.1:8002`, nginx TLS reverse proxy, GitHub Actions test+SSH-deploy on push to main.

**Critical architectural truth:** identity is carried by a context var set in middleware, but **the middleware does not reject unauthenticated requests** — each endpoint is responsible for requiring a user, and **most do not**. This is the root of the top findings.

---

## 5. Current Feature Inventory

| Feature | Current Status | Files | Production Concern |
|---|---|---|---|
| Google OAuth login | Implemented | `auth/router.py`, `auth/security.py`, `auth/service.py` | Cookie issued, but not sent by frontend (P0-03); login works in theory, unused in practice. |
| Session cookie + server session | Implemented | `store/session_token.py`, `auth/models.py` | HMAC-signed; revocation added; SameSite=lax, no CSRF (P1). |
| Per-endpoint authorization | **Largely missing** | `routers/{setup,engine,control,orders,positions,dashboard,broker}.py` | 0 auth guards on these routers (P0-01). |
| User-scoped runtime storage | Partial / unsafe | `services/user_context.py`, `state_store.py` | Global fallback when no user (P0-02). |
| Dhan credential vault | Implemented (Fernet) | `services/credential_vault.py` | Plaintext in-memory fallback for local/test; global memory bucket when unscoped (P0-02). |
| Webhook receive + HMAC | Implemented | `routers/webhook.py` | HMAC optional unless forced; no replay/timestamp (P0-05); unknown-secret rejected only when auth enabled. |
| Paper trading | Implemented | `services/paper_broker.py`, `state_store.py` | Portfolio in global JSON unless scoped (P0-02/04). |
| Live trading + egress gate | Implemented (gated off) | `execution_router.py` | Blocks live unless `EXECUTION_NODE_ROUTING_ENABLED`; executor node not built yet (per DO doc). |
| Risk manager | Implemented | `services/risk_manager.py` | Reads runtime settings; settings file global unless scoped. |
| Position reconciler / ghost watcher / EOD square-off | Implemented (threads) | `workers/*`, `services/option_position_monitor.py` | Single-process; operate on **global** files only; multi-worker duplication (P0-04). |
| Market feed websocket | Implemented | `services/dhan_marketfeed_ws.py` | One shared feed; per-user fan-out not modeled. |
| Audit / order / webhook logs | Implemented (JSONL) | `services/audit_logger.py` | Top-level redaction only verified; metadata free-form (P2). |
| Debug endpoints | Gated by `DEBUG_ENABLED` | `routers/debug.py` | Returns 404 when disabled — good. Keep off in prod. |
| Frontend setup chat UI | Implemented | `frontend/src/components/**` | Token field is `type=password` (good); paper/live wording needs hardening (UX). |

---

## 6. Threat Model

| Threat | Asset at Risk | Current Defense | Gap | Priority |
|---|---|---|---|---|
| Unauthenticated state change (connect Dhan, start engine, panic-exit) | Broker creds, positions, money | Endpoint exists but no auth guard | Anyone who can reach the API acts as the global user | **P0** |
| Cross-user data mixing | Creds, positions, PnL | Per-user paths *when* user context set | Falls back to shared global path when context missing | **P0** |
| Cookie never sent → app runs open | All | Cookie auth implemented | `fetch` omits `credentials`; cross-subdomain cookie never transmitted | **P0** |
| Webhook replay | Real orders | `signal_id` dedup (persisted) + optional HMAC | No timestamp/nonce; captured valid payload replayable across workers/restarts | **P0** |
| Multi-worker state corruption / double square-off | Positions, money | `threading.RLock` (in-process), atomic file replace | Locks/threads don't span workers; EOD/exit can fire twice | **P0** |
| Secret artifacts in deliverable folder | OAuth secret, user DB, tokens | `.gitignore` excludes from git | Files still on disk in synced folder; zip/backup leaks them | **P0** |
| CSRF on mutating endpoints | Engine/orders/creds | SameSite=lax | No CSRF token, no Origin check; lax doesn't cover same-site subdomain abuse | **P1** |
| Webhook-secret hash key rotation | Routing integrity | HMAC with `SESSION_TOKEN_SECRET` | Rotating session secret breaks all webhook routing (shared key) | **P1** |
| VPS compromise | Everything | TLS, gitignore | systemd as root, no hardening, Fernet key in `.env` on same box | **P1** |
| Wrong security-id / strike / expiry | Money | Resolver + validation | Needs explicit test coverage for CE/PE/strike/expiry mismatch | **P1** |
| Edge abuse / DoS | Availability | App-level rate limit (in-proc dict) | No nginx rate limit; in-proc counters don't sum across workers | **P2** |

---

## 7. Severity Summary

| Severity | Count | Meaning |
|---|---|---|
| **P0 Critical** | 6 | Must fix before any multi-user or public exposure; direct money/data-leak risk. |
| **P1 High** | 8 | Must fix before public/live launch. |
| **P2 Medium** | 7 | Fix before scale; meaningful hardening. |
| **P3 Low / Cleanup** | 6 | Hygiene and polish. |
| **UX** | 9 | Trust/safety of the trading UI. |
| **Ops** | 7 | Deployment, monitoring, backup, CI. |

---

## 8. P0 Critical Findings

### P0-01 — Authentication is not enforced on state-changing endpoints

- **Area:** Authorization / multi-tenant safety.
- **Files:** `backend/app/routers/setup.py`, `engine.py`, `control.py`, `orders.py`, `positions.py`, `dashboard.py`, `broker.py`; `backend/app/middleware/user_scope.py`.
- **Risk:** The middleware sets a `current_user_id` context var but **never rejects** an unauthenticated request. Of the state-changing routers, only `connections.py` and `api/session.py` call an auth guard. `setup.py` (11 routes incl. `POST /setup/dhan/connect`, `/setup/webhook-secret`, `/setup/risk`), `engine.py` (`POST /engine/start` = enable live trading), and `control.py` (`/emergency-stop`, `/global-kill-switch`, `/reset-state`, `/fresh-start`, `/panic-exit`) have **zero** guards.
- **Exploit / failure scenario:** With `AUTH_REQUIRED=true`, an attacker (or any unauthenticated client) sends `POST /api/setup/dhan/connect` or `POST /api/engine/start {confirm_live_orders:true}` or `POST /api/control/panic-exit`. The middleware sets `user_id=None`, the endpoint calls `active_user_id()` (returns `None`), and proceeds — connecting credentials, starting the engine, or force-closing positions as the global user.
- **Evidence from code:** `user_scope.py::dispatch` sets the context var and returns `call_next` with no auth check. `setup.py::connect_dhan` calls `save_dhan_credentials(...)` unconditionally and only does `upsert_dhan_account_metadata` `if user_id:`. `engine.py::start_engine` has no user dependency. Grep of `require_user_if_auth_enabled|current_user_from_request` returns 0 hits in those routers.
- **Impact on 100 users:** Complete authorization bypass. Any user (or anon) can drive the global trading context — connect a broker, start live mode, or panic-exit. This is the single most dangerous finding.
- **Fix:** Make auth a **global dependency**, not per-endpoint opt-in. Two layers:
  1. In `UserRuntimeScopeMiddleware`, when `auth_enabled()` and the path is not in a small public allowlist (`/api/auth/*`, `/health`, `/api/health`, `/webhook/*` which authenticates by secret), reject with 401 if `current_user_from_request` is None.
  2. Add a FastAPI dependency `require_user = Depends(require_user_if_auth_enabled)` to every mutating router via `APIRouter(dependencies=[...])`, and have endpoints take the returned `User` rather than calling `active_user_id()` which can be None.
- **Suggested tests:** `test_setup_dhan_connect_requires_auth`, `test_engine_start_requires_auth`, `test_control_panic_exit_requires_auth` — each asserts 401 with no cookie when `AUTH_REQUIRED=true`.
- **Acceptance criteria:** With `AUTH_REQUIRED=true`, every non-public endpoint returns 401 without a valid session; no state-changing handler can run with `user_id=None`.

### P0-02 — User-scoped storage silently falls back to a shared global path

- **Area:** Multi-tenant isolation / credential storage.
- **Files:** `backend/app/services/user_context.py` (`scoped_file`, `scoped_runtime_dir`, `scoped_log_dir`), `services/state_store.py` (`scoped_runtime_file`), `services/credential_vault.py` (`_memory_key`, `_LOCAL_MEMORY_PAYLOADS["__global__"]`).
- **Risk:** `scoped_file()` returns the **base (global) path** when `current_user_id()` is None. Combined with P0-01, any unscoped request reads/writes the global credential vault, global engine state, global positions, and global paper portfolio. Two users (or one user + one anon request) collide on the same files.
- **Exploit / failure scenario:** User A connects Dhan with no/again-missing context → token written to global `credentials.enc.json`. User B's signal, routed without context, reads A's token and **places an order on A's account**. Or simply: anon `POST /setup/dhan/connect` overwrites the legitimate user's credentials.
- **Evidence from code:** `user_context.py::scoped_file`: `if not user_id: return base_path`. `credential_vault.py::_memory_key`: `return current_user_id() or "__global__"`. The prior review's M7 fix ("raise when auth enabled and no user") was **not implemented** — the function still returns the global path.
- **Impact on 100 users:** Cross-account order placement and credential leakage — the worst-case fintech outcome.
- **Fix:** In `user_context.py`, when `settings.AUTH_REQUIRED` is true and no `user_id` is resolvable, **raise** instead of returning the global path, for `scoped_file`, `scoped_runtime_dir`, `scoped_log_dir`. Remove the `"__global__"` memory bucket under auth-on. Allow the global path only when `APP_ENV in {local,test}` and auth disabled.
- **Suggested tests:** `test_scoped_file_raises_without_user_when_auth_enabled`, `test_no_global_vault_write_under_auth`.
- **Acceptance criteria:** Under `AUTH_REQUIRED=true`, any scoped file/dir/vault access without a bound user raises before touching disk; a CI scan confirms no code path writes the global runtime dir under auth-on.

### P0-03 — Frontend never sends the auth cookie (`credentials` omitted)

- **Area:** Auth wiring / browser↔backend trust boundary.
- **Files:** `frontend/src/api.ts`, `frontend/src/lib/backend.ts`, `frontend/src/ws.ts`.
- **Risk:** All `fetch` calls omit `credentials: 'include'`. With frontend at `layman.manyacare.com` and backend at `layman-api.manyacare.com` (cross-site), the browser will **not** send the auth cookie. The app therefore operates unauthenticated — which is only "fine" today because P0-01 lets unauthenticated requests through. Turning auth on without this fix locks legitimate users out; leaving it off is the current insecure posture.
- **Exploit / failure scenario:** Operator sets `AUTH_REQUIRED=true` expecting protection. Because P0-01 still lets requests through unauthenticated, nothing is actually protected; meanwhile any future correct guard would 401 the real frontend.
- **Evidence from code:** `api.ts` — `fetch(backendHttpUrl('/api/session/start'), { method: 'POST' })` with no `credentials`. Grep `credentials:` across `frontend/src` → 0 hits.
- **Impact on 100 users:** Auth cannot function cross-origin; the product cannot distinguish users in the browser.
- **Fix:** Add `credentials: 'include'` to every backend `fetch` (centralize in one `apiFetch` wrapper in `lib/backend.ts`). Set the auth cookie `Domain=.manyacare.com` (or serve frontend and API same-origin behind one nginx). Tighten CORS to the exact frontend origin with `allow_credentials=True` (already set) and ensure the cookie is `Secure`.
- **Suggested tests:** Frontend unit test asserting the shared fetch wrapper sets `credentials:'include'`; e2e: authenticated request succeeds, unauthenticated 401s.
- **Acceptance criteria:** Logged-in browser sends the cookie on every API call; logged-out browser is rejected once P0-01 lands.

### P0-04 — Single-process workers + JSON state break under multiple workers and don't scope per user

- **Area:** State integrity / trading safety / scaling.
- **Files:** `backend/app/services/state_store.py` (`_LOCK = threading.RLock()`, JSON files), `workers/eod_squareoff.py`, `workers/ghost_position_watcher.py`, `services/option_position_monitor.py`.
- **Risk:** Trading state is per-file JSON guarded by an in-process `RLock`; workers are daemon threads with module-level globals. Under multiple uvicorn workers (the normal way to serve 100 users), each worker spawns its own EOD/ghost/monitor threads and they share no lock → **duplicate square-offs, double exits, and JSON corruption**. Worse, workers run outside any request and so resolve `current_user_id()` to None → they only ever watch the **global** position files, not each user's scoped files.
- **Exploit / failure scenario:** Two workers both hit 15:15 IST EOD square-off → two exit orders for the same position. Or two concurrent webhook writes to `app_state.json` interleave and corrupt it. Per-user positions are never monitored because the monitor only sees global files.
- **Evidence from code:** `state_store.py` uses `threading.RLock` (process-local) + `tmp.replace(path)` (atomic per-write, but not cross-process serialized). Workers in `workers/*` start daemon threads at lifespan startup; they call global state functions with no user context.
- **Impact on 100 users:** Either run a single worker (no horizontal scale, single point of failure) or accept corruption/duplication. Neither is acceptable for money.
- **Fix:** Move trading runtime state into Postgres (or Redis) with row-level locking / optimistic versioning; the settings file already has a `_version` field — extend that pattern DB-side. Run workers as a **single** dedicated process (separate systemd unit), not inside web workers, and make them iterate over **all active users** explicitly rather than relying on a context var. Add a distributed lock (Postgres advisory lock or Redis) around EOD square-off so it fires once.
- **Suggested tests:** `test_eod_squareoff_idempotent_under_two_runners`, `test_concurrent_state_writes_no_corruption`, `test_worker_iterates_all_users`.
- **Acceptance criteria:** With 2+ web workers, no duplicate exits and no corrupted JSON; EOD square-off runs exactly once; each user's positions are monitored.

### P0-05 — Webhook has no replay/timestamp protection

- **Area:** Webhook security / order safety.
- **Files:** `backend/app/routers/webhook.py`, `services/state_store.py` (`add_seen_signal`/`has_seen_signal`).
- **Risk:** Authentication is the static secret (+ optional HMAC over the body). There is no timestamp or nonce in the signed material, so a captured valid `(payload, signature)` pair can be **replayed** to place the same order again. `signal_id` dedup is persisted to JSON but the in-flight `_PROCESSING_SIGNAL_IDS` set is process-local, so a replay against a sibling worker (or after restart, before the persisted set is consulted) can slip through.
- **Exploit / failure scenario:** Attacker captures one valid alert (leaked TradingView screenshot, proxy log) and re-POSTs it during market hours → a duplicate real order.
- **Evidence from code:** `_valid_webhook_signature` HMACs only `raw_body`; no timestamp header is required or checked. Dedup relies on `signal_id` which an attacker replays unchanged.
- **Impact on 100 users:** Replayed live orders; financial loss; hard to attribute.
- **Fix:** Require an `x-nova-timestamp` header (unix seconds), reject if `abs(now-ts) > 60s`, and include `ts` in the HMAC. Persist `signal_id` dedup in Postgres with a unique constraint so any worker rejects a repeat atomically. Keep HMAC mandatory in live (already enforced at boot when `ENABLE_LIVE_ORDERS`).
- **Suggested tests:** `test_webhook_rejects_stale_timestamp`, `test_webhook_rejects_replayed_signal_across_workers`.
- **Acceptance criteria:** A replayed payload (same signature, old timestamp, or duplicate signal_id) is rejected with 4xx and places no order.

### P0-06 — Sensitive runtime artifacts present in the working/deliverable folder

- **Area:** Repo/deploy hygiene / secret exposure.
- **Files (on disk, untracked but present):** `client_secret_*.apps.googleusercontent.com (1).json` (repo root), `backend/runtime_state/auth.sqlite3` (real user/session rows), `backend/runtime_state/*.json`, `backend/runtime_logs/{audit_events,order_events,paper_orders}.jsonl`.
- **Risk:** These are correctly `.gitignore`d (verified: `git ls-files` shows none tracked), but they physically sit in a OneDrive-synced folder. Any zip, backup, screen-share, or sync of the folder leaks the **Google OAuth client secret**, the **auth database**, and **order/audit history**.
- **Exploit / failure scenario:** Folder shared/zipped for support → OAuth client secret exfiltrated → attacker can impersonate the app's OAuth client. Auth DB leak exposes sessions/emails.
- **Evidence from code:** Files listed by `ls` on disk; `.gitignore` covers them for git but not for the filesystem/OneDrive.
- **Impact on 100 users:** Credential and PII exposure independent of the running system.
- **Fix:**
  1. **Rotate the Google OAuth client secret** in Google Cloud Console, then delete the `client_secret_*.json` from the folder; load OAuth creds only from `.env`/secret manager.
  2. Move `runtime_state/` and `runtime_logs/` out of any synced/deliverable directory (e.g., `/var/lib/layman/`), `chmod 700`.
  3. Add an explicit "deployment package" allowlist so backups/zips never include secrets, the auth DB, logs, `node_modules`, `dist`, or `.git`.
- **Suggested tests:** CI guard `test_no_secret_files_in_tree` that fails if `client_secret*`, `*.sqlite3`, or `runtime_logs/*` appear under the repo path.
- **Acceptance criteria:** No OAuth client JSON, DB, or logs inside the project/deliverable folder; OAuth secret rotated.

---

## 9. P1 High Findings

### P1-01 — No CSRF protection; cookie is SameSite=lax
- **Area:** Session security. **Files:** `auth/router.py`, all mutating routers.
- **Risk:** Once P0-03 makes cookies flow, `SameSite=lax` + no CSRF token leaves state-changing POSTs exposed to same-site/subdomain CSRF and some cross-site vectors.
- **Fix:** Auth cookie `SameSite=strict`; issue a `__Host-csrf` cookie and require a matching `X-CSRF-Token` header (double-submit) on all non-GET; reject requests whose `Origin`/`Referer` isn't the allowlisted frontend.
- **Test:** `test_state_change_without_csrf_rejected`. **Acceptance:** mutating requests without a valid CSRF token → 403.

### P1-02 — Dangerous control endpoints unauthenticated and unconfirmed
- **Area:** Trading safety. **Files:** `routers/control.py`.
- **Risk:** `panic-exit`, `global-kill-switch`, `reset-state`, `fresh-start` run with no auth (P0-01 subset) and no confirmation token. `reset-state`/`fresh-start` can wipe runtime state.
- **Fix:** Require auth (via P0-01), and require an explicit confirm field for destructive actions; audit-log actor + IP.
- **Test:** `test_control_endpoints_require_auth_and_confirm`. **Acceptance:** destructive control endpoints need auth + `confirm=true`.

### P1-03 — Webhook-secret hash shares the session signing key
- **Area:** Secrets management. **Files:** `services/user_connections.py::_hash_secret`.
- **Risk:** `webhook_secret_hash = HMAC(SESSION_TOKEN_SECRET, secret)`. Rotating the session secret silently breaks every user's webhook routing; reusing one key across two domains weakens both. (The literal `"change-me"` fallback was removed — good — but the shared-key coupling remains.)
- **Fix:** Add a dedicated `WEBHOOK_HMAC_PEPPER`; hash with it; document an offline re-hash migration for rotation.
- **Test:** `test_hash_secret_uses_dedicated_pepper`. **Acceptance:** rotating `SESSION_TOKEN_SECRET` does not change webhook hashes.

### P1-04 — No Alembic; schema via `create_all` + ad-hoc column patcher
- **Area:** Database. **Files:** `auth/db.py`.
- **Risk:** `create_all` plus the new additive-`ALTER TABLE` shim is a stopgap. No down-migrations, no drift detection, race on multi-worker startup. `alembic` is already a dependency but unconfigured.
- **Fix:** `alembic init`, autogenerate baseline from models, run `alembic upgrade head` as a deploy step; make `init_database()` a no-op in production; add `alembic check` to CI.
- **Test:** CI `alembic upgrade head && alembic check`. **Acceptance:** schema managed by migrations; app boot doesn't mutate schema in prod.

### P1-05 — systemd runs as root with no hardening; secrets on same box
- **Area:** Deployment hardening. **Files:** `deploy/layman-nova-signal-router.service`.
- **Risk:** `User=root`, `WorkingDirectory=/root/...`, no `NoNewPrivileges`, `ProtectSystem`, `ProtectHome`, `PrivateTmp`. The Fernet `TOKEN_ENCRYPTION_KEY` lives in `.env` on the same host, so disk capture defeats at-rest encryption.
- **Fix:** Dedicated non-root `layman` user; `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, `ReadWritePaths=/var/lib/layman`; `EnvironmentFile=` with `0600` perms; move key to a secrets manager when budget allows.
- **Test:** deploy lint / manual. **Acceptance:** service runs unprivileged with hardening directives; `.env` is `0600` owned by the service user.

### P1-06 — nginx lacks security headers and edge rate limiting
- **Area:** Deployment. **Files:** `deploy/nginx/layman-api.manyacare.com.conf`.
- **Risk:** No HSTS, `X-Content-Type-Options`, `X-Frame-Options`/CSP, no `limit_req`. The proxy forwards everything to `127.0.0.1:8002` (note: app default port is 8001 — confirm the deploy overrides it, else mismatch).
- **Fix:** Add `add_header Strict-Transport-Security ...`, `X-Content-Type-Options nofocus`→`nosniff`, frame-deny/CSP; add `limit_req_zone` for `/webhook/` and `/api/auth/`. Verify the uvicorn port matches.
- **Test:** `curl -I` shows headers; load test shows 429 past limit. **Acceptance:** headers present; webhook/auth rate-limited at the edge.

### P1-07 — CI deploys on every push to main; hardcoded VPS IP; no SAST/dep-audit
- **Area:** CI/CD. **Files:** `.github/workflows/layman-backend-ci-deploy.yml`.
- **Risk:** Any push to `main` SSH-deploys to prod (no manual gate/environment approval beyond `environment: production`). VPS IP and host key are hardcoded in the workflow. No `bandit`/`pip-audit`/`npm audit` gates.
- **Fix:** Require manual approval on the `production` environment; move IP/host key to repo/Environment variables; add bandit, pip-audit, npm audit (fail on high) as required checks before deploy.
- **Test:** workflow dry-run. **Acceptance:** deploy needs approval; security scans gate the pipeline.

### P1-08 — Live-trade correctness needs explicit guard tests (security-id / strike / expiry / lot)
- **Area:** Order safety. **Files:** `services/execution_router.py`, `services/security_id_resolver.py`, `services/signal_parser.py`.
- **Risk:** Wrong `securityId`, CE/PE side, strike, expiry, or lot multiple = wrong real order. There's a correlationId and lot-multiple check, but no dedicated test matrix proving each mismatch is blocked.
- **Fix:** Add a parametrized test matrix asserting that malformed/mismatched instrument fields are rejected before any Dhan call; assert `qty % lot_size == 0` enforcement and CE/PE-strike-expiry consistency.
- **Test:** `test_execution_blocks_wrong_instrument_fields`. **Acceptance:** every instrument-field mismatch is blocked with a clear reason and no order is sent.

---

## 10. P2 Medium Findings

### P2-01 — In-process rate limiting doesn't sum across workers
- **Files:** `routers/webhook.py`, `routers/setup.py`. **Risk:** per-process dict counters under-count under multiple workers. **Fix:** move counters to Redis. **Test:** `test_rate_limit_shared_across_workers`. **Acceptance:** limits enforced cluster-wide.

### P2-02 — Audit metadata is free-form JSON
- **Files:** `auth/models.py::OrderRouteAudit.metadata_json`, callers. **Risk:** tokens/PII can be stuffed in and persisted forever. **Fix:** `sanitize_audit_metadata()` whitelisting keys; never store `secret`/`access_token`/`authorization`. **Test:** `test_audit_metadata_whitelist`. **Acceptance:** sensitive keys never persisted.

### P2-03 — Webhook log redaction depth not proven
- **Files:** `routers/webhook.py::_safe_raw_body_for_log`. **Risk:** redaction is recursive in code but lacks nested-fixture tests; regressions could leak nested `access_token`. **Fix:** add nested-payload redaction tests. **Acceptance:** nested secrets redacted.

### P2-04 — Cookie TTL 7 days, no idle timeout / fingerprint / kid
- **Files:** `config.py`, `store/session_token.py`. **Risk:** long-lived cookie amplifies theft; no rotation primitive. **Fix:** 24h TTL + sliding refresh (last_used_at already added), idle timeout, coarse UA/IP fingerprint, `kid` in token. **Acceptance:** idle sessions expire; key rotation possible.

### P2-05 — OAuth has no PKCE; error reasons partly leaked
- **Files:** `auth/router.py`. **Risk:** state-only flow; `?oauth_error=<reason>` reveals which rung failed. **Fix:** add PKCE (S256); collapse to opaque error codes + server-side trace id. **Acceptance:** PKCE present; only opaque codes returned.

### P2-06 — Single shared market-feed websocket; no per-user fan-out model
- **Files:** `services/dhan_marketfeed_ws.py`, `api/ws.py`. **Risk:** at 100 users, one feed + per-session sockets need a fan-out/backpressure design. **Fix:** central feed → per-user pub/sub (Redis) → websocket fan-out. **Acceptance:** documented and load-tested fan-out.

### P2-07 — Port/config drift risk
- **Files:** `config.py` (default 8001), systemd/nginx (8002). **Risk:** silent mismatch if env not set. **Fix:** single source of truth; assert at boot that bound port matches expected. **Acceptance:** no port ambiguity.

---

## 11. P3 Low / Cleanup Findings

- **P3-01 Dead code:** `ConfirmLaunchCard.tsx` is marked dead ("live setup uses DeploymentSummary"). Remove to avoid confusion about which live-confirm path is real.
- **P3-02 Duplicate webhook mounts:** `webhook.router` mounted at both `/webhook` and `/api/webhook` — keep one canonical path or document why both.
- **P3-03 Email normalization:** no IDNA/NFKC normalization before allowlist compare (homograph edge case).
- **P3-04 `.env.live.example` mixes legacy flat creds** (`DHAN_CLIENT_ID`, `WEBHOOK_SECRET`) with the per-user model — clarify it's a single-user route-test file only.
- **P3-05 Token `aud`/`jti` absent** — no audience binding or per-token revocation id (covered partly by session row).
- **P3-06 node_modules / dist present in folder** — ensure excluded from any deploy package; rebuild on target OS (the synced native binaries break `vite build`).

---

## 12. Data Isolation Review

- **User-specific state:** Intended via `scoped_file`/`scoped_runtime_dir`, but the **global fallback** (P0-02) defeats it whenever context is missing — which is the default given P0-01/P0-03.
- **Global fallback risks:** `scoped_file` returns base path on `None`; credential vault uses a `"__global__"` memory bucket. Both must hard-fail under auth.
- **Credential isolation:** DB enforces one Dhan client per user (`upsert_dhan_account_metadata` rejects cross-user client reuse) — good — but the **actual token** lives in the scoped JSON vault, which is only isolated if context is always bound.
- **Websocket isolation:** `api/ws.py` checks `session.user_id == token.uid` when auth enabled and scopes the context var — this path is correct; keep it.
- **Paper/live isolation:** Paper portfolio and live positions are separate files but both global-fallback-prone.
- **Database isolation:** SQL tables are user-keyed with good indexes and partial-unique egress constraints — the strongest part of the system.
- **Runtime file isolation:** Weak — JSON per file, global fallback, process-local lock.
- **Multi-worker risks:** P0-04. Workers and in-proc locks/maps do not span workers.

---

## 13. Credential and Secret Handling Review

- **Dhan credentials:** Fernet-encrypted at rest in `credentials.enc.json`; production boot now requires a valid key (verified in `main.py`). In-memory plaintext fallback restricted to `APP_ENV in {local,test}` + MOCK (verified). Good — **conditional on** context always being bound (P0-02).
- **Webhook secrets:** hashed (HMAC) in DB, raw kept in the scoped vault for comparison. Shared signing key with sessions (P1-03).
- **Encryption keys:** `TOKEN_ENCRYPTION_KEY` from `.env` on the same VPS — single point of compromise (P1-05).
- **OAuth secrets:** **client_secret JSON physically present in the folder** (P0-06) — rotate + remove.
- **Session secret:** strong-length enforced in prod boot; doubles as webhook pepper (P1-03).
- **Logs:** top-level + recursive redaction in webhook log; audit metadata free-form (P2-02). No `console.log` in frontend (verified).
- **Repo hygiene:** git tree clean of secrets (verified). Filesystem is not (P0-06).

---

## 14. Webhook and Trading Safety Review

- **HMAC:** supported; mandatory in prod when live (boot-enforced). Good.
- **Replay protection:** **absent** (P0-05) — no timestamp/nonce.
- **Duplicate prevention:** `signal_id` dedup persisted; in-flight set process-local (weak across workers).
- **Rate limits:** per-(user|anon, IP) with strict anon bucket when auth on (implemented); in-proc only (P2-01).
- **Live order safety:** gated by `ENABLE_LIVE_ORDERS`, `UNIQUE_EGRESS_PER_USER_REQUIRED`, `EXECUTION_NODE_ROUTING_ENABLED`; live blocked until a unique egress node is assigned — good design, executor node not built yet.
- **Market hours:** `REQUIRE_MARKET_HOURS` / `MARKET_CLOSED_DEBUG` gates present.
- **Kill switch:** `emergency_stop`, `global_kill_switch`, `ENABLE_LIVE_ORDERS=false` — but kill-switch is read at call time (TOCTOU on in-flight orders; re-check at the Dhan call site).
- **Order idempotency:** correlationId for timeout reconciliation, not a broker-enforced idempotency key; lot-multiple check present. Needs the P1-08 test matrix.
- **Restart recovery:** `startup_reconciler` + ghost watcher exist; correctness under multi-worker unverified (P0-04).

---

## 15. Frontend UI/UX Review

| Screen/Component | Issue | Risk | Fix | Priority |
|---|---|---|---|---|
| `lib/backend.ts` / `api.ts` | `fetch` omits `credentials:'include'` | Auth can't work; app runs open | Central `apiFetch` with credentials; one base URL | **P0 (UX-critical)** |
| `BrokerForm.tsx` | Access-token field is `type=password` (good); but no masked confirmation after save, no "token expires in ~24h" hint inline | User confusion, stale-token live failures | Show masked token + age/expiry countdown after connect | UX-P1 |
| `ConfirmLaunchCard.tsx` | Marked dead code; a second live-confirm path exists | Wrong/duplicate confirm path may mislead | Remove dead component; keep one canonical live-confirm | UX-P2 |
| Live confirm flow | Live start requires `confirm_live_orders` server-side, but UI confirmation copy not hardened | User may enable live thinking it's paper | Explicit red "REAL MONEY" modal with typed confirmation ("type LIVE") | **UX-P0** |
| `QuickActions.tsx` | Buttons fire `onSend` with no disabled/loading guard | Double-click → duplicate actions (pause/resume/risk) | Disable while pending; debounce; show spinner | UX-P1 |
| Panic / kill actions | No confirmation dialog | Accidental position close / global halt | Require confirm dialog for destructive actions | UX-P1 |
| Paper vs Live indicator | Mode shown but not persistently prominent | Mistaken-mode trading | Persistent mode banner (green PAPER / red LIVE) on every screen | **UX-P0** |
| Engine status / listening | Status states present | Stale state after backend restart can mislead | Reconcile UI state from `/engine/status` on focus/reconnect | UX-P2 |
| Wallet/funds display | Shows funds from Dhan | Stale funds after token expiry | Show "as of <time>"; grey out when token stale | UX-P2 |
| Mobile responsiveness | Chat layout; not audited on device | Fat-finger on small screens for money actions | Test ≤375px; enlarge destructive-action targets + confirm | UX-P2 |

**Recommended setup journey:** Login → connect Dhan (mask + verify) → assign/whitelist unique IP (show IP + Dhan steps + verify button) → set risk (with hard caps) → pick strategy → **Paper dry-run required** → explicit Live confirmation modal (typed "LIVE") → engine start. Block Live until a paper trade has been observed.

**Paper/Live warning improvements:** persistent colored banner; Live start behind a typed-confirmation modal; disable Live until IP whitelisting verified end-to-end.

**Dangerous-button confirmation rules:** any action that can place/close real orders or halt the engine requires (a) disabled-while-pending, (b) a confirm step, (c) an audit toast echoing what happened.

**Trust-building UI:** show "encrypted at rest", last-login, active-session list with revoke, token age/expiry, assigned execution IP, and a clear "you are in PAPER" state.

---

## 16. Backend/API Review

| Endpoint/Service | Issue | Risk | Fix | Priority |
|---|---|---|---|---|
| `POST /api/setup/dhan/connect` | No auth guard; writes vault | Anon credential overwrite | Require user (P0-01) | P0 |
| `POST /api/engine/start` | No auth guard; enables live | Anon live enable | Require user + live confirm | P0 |
| `POST /api/control/*` | No auth; destructive | Anon panic/kill/reset | Require user + confirm (P1-02) | P0/P1 |
| `GET /api/orders`, `/positions`, `/dashboard` | No user scoping | Reads global data | Require user; scope queries | P0 |
| `POST /webhook/tradingview` | No replay protection | Replayed orders | Timestamp+nonce (P0-05) | P0 |
| `scoped_file()` | Global fallback | Cross-user mixing | Raise under auth (P0-02) | P0 |
| Workers | In-process, unscoped | Dup exits / corruption | Single worker process + DB locks (P0-04) | P0 |
| `/api/auth/google/callback` | Reason leak partly fixed | Calibration aid | Opaque codes + PKCE (P2-05) | P2 |
| `/api/debug/*` | Gated by `DEBUG_ENABLED` → 404 | OK if off in prod | Keep `DEBUG_ENABLED=false` in prod; CI assert | P2 |
| Health checks | `/health` + `/api/health` exist | OK | Add readiness (DB + vault) probe | P2 |

---

## 17. Database and Runtime State Review

- **Postgres required in prod:** Enforced — `_database_url()` raises in production if `DATABASE_URL` missing or SQLite (verified). Good.
- **SQLite fallback:** allowed only in non-prod (verified).
- **Migrations:** **not** managed by Alembic; `create_all` + additive `ALTER TABLE` shim (P1-04). Alembic is a dependency but unused.
- **`create_all` danger:** runs every boot; hides drift; multi-worker race. Gate behind local/test; use migrations in prod.
- **Runtime JSON files for 100 users:** unsafe (P0-04). Atomic per-write (`tmp.replace`) but no cross-process serialization; global fallback.
- **Atomic writes / locks:** atomic single-file writes yes; `threading.RLock` is process-local only.
- **Concurrent corruption:** possible across workers.
- **Backup/restore:** none defined. Add nightly encrypted `pg_dump` off-box + monthly restore drill.
- **State consistency after restart:** reconciler exists; multi-worker correctness unverified.
- **Audit immutability:** JSONL append; not tamper-evident. Consider write-once storage / hash-chaining for fintech audit.
- **Retention/PII:** no policy; define retention for emails, order logs, audit.

---

## 18. Logging, Audit, and Data Leak Review

- **Logged:** audit events, order events, paper orders (JSONL under `runtime_logs/`), webhook raw (redacted).
- **Never log:** access tokens, raw webhook secrets, cookies, Authorization headers, full Dhan responses with tokens.
- **Redaction gaps:** webhook redaction recursive (good) but untested for nesting (P2-03); audit `metadata_json` free-form (P2-02).
- **Order logs:** contain instrument/qty/PnL — sensitive; secure file perms + retention.
- **Debug endpoints:** 404 when `DEBUG_ENABLED=false` — verify it's false in prod.
- **Exception traces in prod:** ensure FastAPI doesn't return stack traces; set `debug=False` and a generic 500 handler.
- **Log file permissions:** move logs to `/var/lib/layman/logs` `0700`; out of synced folder (P0-06).
- **Retention:** define (e.g., 90d hot, 1y cold for audit).

---

## 19. Deployment and VPS Hardening Review

- **nginx:** TLS + websocket upgrade OK; **missing** security headers and edge rate limiting (P1-06); confirm proxy port (8002) matches app.
- **TLS:** Let's Encrypt via `enable_layman_api_tls.sh` — good.
- **systemd:** runs as **root**, no hardening (P1-05).
- **Env file handling:** ensure `EnvironmentFile` `0600`, service-user owned; never world-readable.
- **Firewall:** assumed but not codified — document `ufw` (allow 22 from admin IP, 80/443 public, deny rest); Postgres bound `127.0.0.1` only.
- **CI/CD:** auto-deploy on push, hardcoded IP, no SAST (P1-07).
- **GitHub secrets:** SSH key via `secrets.VPS_SSH_KEY` (good); IP/host key hardcoded (move to vars).
- **Log rotation:** none defined — add `logrotate` for `runtime_logs` and journald limits.
- **Backup:** none (add pg_dump off-box).
- **Monitoring:** none — add health/uptime, audit-event alerts, vault/DB readiness alerts.
- **Domain/frontend URL:** `VITE_BACKEND_URL` cross-subdomain — drives the cookie problem (P0-03); consider same-origin behind one nginx.
- **Deploy package contents:** ensure it excludes `node_modules`, `dist`, `.git`, `runtime_state`, `runtime_logs`, `client_secret*.json`, `.env`.

---

## 20. Testing Gaps

| Missing Test | Why Needed | Suggested Test File | Priority |
|---|---|---|---|
| Auth required on every mutating endpoint | Prove P0-01 closed | `tests/test_endpoint_authz.py` | P0 |
| No global runtime path under auth | Prove P0-02 closed | `tests/test_runtime_scope_guard.py` | P0 |
| Frontend sends credentials | Prove P0-03 closed | `frontend` unit test on `apiFetch` | P0 |
| EOD idempotency under 2 runners | Prove P0-04 | `tests/test_eod_idempotent.py` | P0 |
| Webhook replay/timestamp rejection | Prove P0-05 | `tests/test_webhook_replay.py` | P0 |
| CSRF rejection | Prove P1-01 | `tests/test_csrf.py` | P1 |
| Cross-user isolation (A can't read B) | Multi-tenant safety | `tests/test_cross_user_isolation.py` | P0 |
| Broker failure / timeout handling | Live robustness | `tests/test_dhan_failure_modes.py` | P1 |
| Wrong instrument fields blocked | Order correctness | `tests/test_instrument_guards.py` | P1 |
| Frontend button loading/disabled states | Double-order prevention | `frontend` component tests | UX-P1 |
| 100-user concurrency simulation | Scale readiness | `tests/load/` (locust) | P1 |
| Py3.12/3.14 CI parity | Tests need shims on 3.10 | CI matrix | Ops |

> Note: the suite **passed 172** only after adding a `StrEnum` shim and a sqlmodel JSON-column shim to run on the sandbox's Python 3.10. Pin CI to the project's actual runtime (3.12/3.14) so tests reflect production.

---

## 21. 100-User Scaling Plan

- **DB:** move trading runtime state from JSON files into Postgres (or Redis for hot state); keep connection pooling (added). One source of truth.
- **Redis:** rate-limit counters, signal dedup, websocket pub/sub, distributed locks (EOD square-off).
- **Workers:** run a **single** worker process (separate systemd unit) iterating all active users; do not spawn workers inside web processes.
- **Websocket fan-out:** central Dhan feed → Redis pub/sub → per-user socket fan-out with backpressure.
- **Per-user egress IP:** wire the executor-node path (`EXECUTION_NODE_ROUTING_ENABLED`) per the DO doc; one whitelisted IP per user.
- **Background jobs:** idempotent + distributed-locked.
- **Monitoring:** health/readiness, audit-event alerting, Dhan error-rate, per-user order-route-IP mismatch alert.
- **Incident response:** documented kill-switch runbook; DB restore drill; egress-node replacement runbook.

---

## 22. Recommended Target Architecture

```
                       ┌─────────────────────────────┐
   Browser ── HTTPS ──▶│  nginx (same-origin)        │
                       │  TLS, headers, limit_req    │
                       └───────────┬─────────────────┘
                                   │
                 ┌─────────────────┴───────────────────┐
                 │  FastAPI web tier (N workers)        │
                 │  auth (global dep), CSRF, CORS       │
                 │  REST + WS gateway                   │
                 └───┬───────────┬───────────┬──────────┘
                     │           │           │
              ┌──────▼───┐ ┌─────▼─────┐ ┌───▼────────────┐
              │ Postgres │ │  Redis    │ │ Credential     │
              │ users,   │ │ ratelimit │ │ vault (KMS/    │
              │ sessions,│ │ dedup,    │ │ Fernet+kid)    │
              │ state,   │ │ pubsub,   │ └────────────────┘
              │ audit    │ │ locks     │
              └──────────┘ └─────┬─────┘
                                 │ pub/sub
        ┌────────────────────────┴───────────────────────┐
        │  Worker tier (single, leader-locked)            │
        │  EOD square-off, ghost watcher, monitors        │
        │  iterates ALL active users                      │
        └───────────────┬─────────────────────────────────┘
                        │ signed order request
        ┌───────────────▼───────────────┐   one IP per user
        │ Executor nodes (per-user)      │──▶ Dhan API (whitelisted IP)
        │ DigitalOcean droplet + ReservedIP
        └────────────────────────────────┘

   Webhook gateway (TradingView) ─HMAC+timestamp─▶ web tier ─▶ Redis dedup ─▶ worker route
   Monitoring/Alerting ◀── audit events, health, route-IP mismatch
   Admin panel ◀── session revoke, user block, egress assign/release
```

Key shifts from today: same-origin to fix cookies; auth as a global dependency; state in Postgres/Redis not JSON; a single leader-locked worker tier; executor nodes for per-user egress; KMS-backed key with rotation.

---

## 23. Fix Roadmap

### Stage 0 — Stop dangerous leaks immediately
| Task | Files | Owner Skill | Complexity | Acceptance |
|---|---|---|---|---|
| Rotate Google OAuth secret; delete client_secret JSON from folder | repo root, Google Console | Ops | Low | No client JSON in folder; new secret in `.env` only |
| Move `runtime_state`/`runtime_logs` out of synced folder; `chmod 700` | deploy, `config.py` | Ops | Low | No DB/logs in deliverable folder |
| Keep `ENABLE_LIVE_ORDERS=false` until Stage 2 done | `.env` | Ops | Low | Live disabled by default |

### Stage 1 — Make auth and data isolation production safe
| Task | Files | Owner Skill | Complexity | Acceptance |
|---|---|---|---|---|
| Global auth enforcement (middleware reject + router deps) | `middleware/user_scope.py`, all mutating routers | Backend | Med | 401 without session on all non-public routes |
| Hard-fail scoped storage under auth | `services/user_context.py`, `credential_vault.py` | Backend | Med | No global path/bucket under auth |
| Frontend `credentials:'include'` + same-origin/cookie domain | `frontend/src/lib/backend.ts`, nginx | Frontend | Low | Cookie flows; logged-out 401s |
| CSRF + SameSite=strict + Origin check | `auth/router.py`, routers | Backend | Med | Mutating requests need CSRF token |
| Cross-user isolation tests | `tests/` | Backend | Med | A cannot read/write B |

### Stage 2 — Make trading execution safe
| Task | Files | Owner Skill | Complexity | Acceptance |
|---|---|---|---|---|
| Webhook timestamp+nonce; DB-persisted dedup | `routers/webhook.py`, models | Backend | Med | Replays rejected |
| Single leader-locked worker tier; iterate all users | `workers/*`, systemd | Backend/Ops | High | One exit per position; per-user monitored |
| Move runtime state to Postgres/Redis | `state_store.py` | Backend | High | No JSON corruption under N workers |
| Instrument-field guard tests; kill-switch re-check at call site | `execution_router.py`, tests | Backend | Med | Wrong order blocked; TOCTOU closed |
| Wire executor-node egress path | `execution_router.py`, DO scripts | Backend/Ops | High | Live order leaves user's IP |

### Stage 3 — Make UI trustworthy
| Task | Files | Owner Skill | Complexity | Acceptance |
|---|---|---|---|---|
| Persistent PAPER/LIVE banner; typed LIVE confirm modal | `frontend/src/components/**` | Frontend | Med | Cannot enable live without typed confirm |
| Disable-while-pending + confirm on destructive buttons | `QuickActions.tsx`, etc. | Frontend | Low | No double-fire; confirms required |
| Token age/expiry, session list + revoke, assigned IP display | frontend + `auth` endpoints | Full-stack | Med | Trust signals visible |

### Stage 4 — Make deployment production-grade
| Task | Files | Owner Skill | Complexity | Acceptance |
|---|---|---|---|---|
| Alembic migrations; no `create_all` in prod | `auth/db.py`, `alembic/` | Backend | Med | `alembic check` clean in CI |
| systemd hardening + non-root user | service file | Ops | Low | Unprivileged, hardened |
| nginx headers + edge rate limit | nginx conf | Ops | Low | HSTS/CSP + 429 past limit |
| CI: manual prod approval, SAST, dep-audit, IP→vars | workflow | Ops | Med | Scans gate; deploy approved |
| Backups + logrotate + monitoring/alerts | deploy | Ops | Med | Nightly pg_dump; alerts live |

### Stage 5 — 100-user readiness
| Task | Files | Owner Skill | Complexity | Acceptance |
|---|---|---|---|---|
| Redis rate-limit/dedup/pubsub/locks | backend | Backend | High | Cluster-correct limits/dedup |
| Websocket fan-out design | `api/ws.py`, feed | Backend | High | Load-tested fan-out |
| 100-user load + chaos (broker outage) tests | `tests/load/` | Backend | High | Meets latency/error SLOs |
| Admin panel (revoke/block/egress) | new | Full-stack | High | Ops can manage users |

---

## 24. Final Go/No-Go Checklist

**Beta launch (multi-user paper):**
- [ ] P0-01 auth enforced everywhere
- [ ] P0-02 no global fallback under auth
- [ ] P0-03 frontend sends cookie; same-origin/domain cookie
- [ ] P0-04 single worker tier or DB-backed state (no corruption)
- [ ] P0-06 secrets out of folder; OAuth rotated
- [ ] CSRF + cross-user isolation tests green

**Public launch:**
- [ ] All P1 closed (CSRF, control-auth, pepper, Alembic, systemd, nginx, CI gates, instrument tests)
- [ ] Backups + monitoring + log rotation live
- [ ] Load test at target concurrency passes

**Live trading launch:**
- [ ] Executor-node per-user egress wired + verified end-to-end (one real test order from the user's whitelisted IP)
- [ ] Webhook replay protection live; HMAC mandatory
- [ ] Kill-switch re-checked at Dhan call site; daily-loss/qty caps enforced
- [ ] OrderRouteAudit shows correct egress IP per order
- [ ] Restore drill completed; incident runbook signed off

---

## 25. Appendix

### Suspicious files (remove from folder / deploy package)
- `client_secret_*.apps.googleusercontent.com (1).json` — **rotate + delete**
- `backend/runtime_state/auth.sqlite3`, `*.json` — move to `/var/lib/layman`
- `backend/runtime_logs/*.jsonl` — move + `0700` + logrotate
- `frontend/node_modules/`, `frontend/dist/`, `.git/`, `__pycache__/`, `.pytest_cache/` — exclude from deploy package; rebuild `node_modules` on target OS

### Commands to add to CI
```yaml
- run: pip install bandit pip-audit
- run: bandit -r app -ll
- run: pip-audit -r requirements.txt
- working-directory: frontend
  run: npm ci && npm audit --omit=dev && npm run build
- run: alembic upgrade head && alembic check
- run: pytest app/tests -q   # on Python 3.12/3.14, not 3.10
```

### Recommended env template (prod, additions/overrides)
```
APP_ENV=production
AUTH_REQUIRED=true
ADMIN_EMAILS=ops@yourdomain.com
DATABASE_URL=postgresql+psycopg://layman:***@127.0.0.1:5432/layman
TOKEN_ENCRYPTION_KEY=<valid-fernet>
SESSION_TOKEN_SECRET=<32+ random>
WEBHOOK_HMAC_PEPPER=<32+ random, NEW>          # P1-03
WEBHOOK_HMAC_REQUIRED=true
ENABLE_LIVE_ORDERS=false                        # flip only after Stage 2
UNIQUE_EGRESS_PER_USER_REQUIRED=true
EXECUTION_NODE_ROUTING_ENABLED=false            # true only when executor nodes live
DEBUG_ENABLED=false
RUNTIME_STATE_DIR=/var/lib/layman/state
RUNTIME_LOG_DIR=/var/lib/layman/logs
```

### Recommended `.gitignore` (confirm present)
```
.env
.env.*
!.env.example
!.env.live.example
client_secret*.json
*.sqlite3
*.db
backend/runtime_state/
backend/runtime_logs/
frontend/node_modules/
frontend/dist/
__pycache__/
.pytest_cache/
```

### Recommended monitoring alerts
- `WEBHOOK_UNKNOWN_SECRET` > 5/min, `WEBHOOK_AUTH_FAILED` > 10/min, any `OAUTH_INVALID_STATE`.
- Any `OrderRouteAudit` where `source_ip` ≠ user's assigned egress IP.
- `vault_ready()` or DB readiness false.
- Distinct active sessions spike (onboarding abuse).
- Dhan API error-rate / 429 rate.
- Disk/perm change on `.env`, `runtime_state`, `authorized_keys`, `sshd_config`.

---

## Existing Security Review Verification

| Claimed Fix | Verified in Code? | Evidence | Still Risky? | Next Action |
|---|---|---|---|---|
| C1 `email_allowed` fails closed | **Yes** | `auth/service.py`: empty allowlist → `False`; `main.py` prod guard requires `ADMIN_EMAILS` | Low | Add IDNA/NFKC (P3-03) |
| C2 `AUTH_REQUIRED` default true / prod guard | **Partial** | `main.py` rejects `AUTH_REQUIRED=false` in prod, **but** endpoints don't enforce auth (P0-01) and `config.py` default is still `False` | **HIGH** | Implement global enforcement (P0-01) |
| C3 Webhook rejects unknown secret / no global fallback | **Partial** | `webhook.py` returns 403 on unknown secret **only when `auth_enabled()`**; the broader global-vault/global-path fallback persists via `scoped_file` | **HIGH** | Close P0-02; make webhook user-resolution mandatory regardless |
| C4 Postgres required, no SQLite in prod | **Yes** | `auth/db.py` raises on missing/SQLite URL in prod; pool args added | Low (no Alembic) | Add Alembic (P1-04) |
| H1 Logout revokes session | **Yes** | `auth/router.py` logout → `revoke_auth_session`; `security.py` rejects revoked/sessionless tokens | Low | Add "revoke all sessions" UI |
| H2 Strict google_sub matching | **Yes** | `service.py` raises `AuthError` on email collision with different sub | Low | — |
| H3 Encryption key required in prod; in-mem fallback restricted | **Yes** | `main.py` requires valid Fernet in prod; `credential_vault.py` limits fallback to local/test | Low | KMS + `kid` later (P2-04) |
| H4 Rate-limit compound key + anon bucket | **Yes** | `webhook.py` keys on `(user|anon, host)`; in-proc only | Medium | Move to Redis (P2-01) |
| M9 `WEBHOOK_HMAC_REQUIRED` default true | **Yes** | `.env.example` true; prod boot requires it when live | Low | — |
| L1 No literal hash fallback | **Yes** | `user_connections.py` raises if `SESSION_TOKEN_SECRET` empty | Low | Separate pepper (P1-03) |
| H5 CSRF / SameSite=strict | **No** | Still `SameSite=lax`, no CSRF token | **HIGH** | Implement (P1-01) |
| H6 Separate webhook pepper | **No** | Still HMAC with `SESSION_TOKEN_SECRET` | Medium | Add `WEBHOOK_HMAC_PEPPER` (P1-03) |
| M2 Webhook replay protection | **No** | No timestamp/nonce | **HIGH** | Implement (P0-05) |
| M7 `scoped_runtime_dir` global fallback closed | **No** | `user_context.py` still returns global path when no user | **HIGH** | Close (P0-02) |
| Alembic adopted | **No** | `create_all` + ad-hoc ALTER | Medium | Adopt (P1-04) |

**Bottom line on prior docs:** the *auth-model and config-boot* fixes are real and verified. The *enforcement and isolation* fixes (C2/C3/M7) are **incomplete** — boot-time guards exist, but the runtime request path still allows unauthenticated, unscoped, global-fallback access. The prior "fixes applied" doc overstates closure of C2/C3; treat those as open until P0-01 and P0-02 land.
