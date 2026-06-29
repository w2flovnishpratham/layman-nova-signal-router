# Multi-User SaaS Upgrade — Backend Foundation (Phase 1)

This document describes the additive multi-user layer added on top of the existing
single-tenant NOVA / Layman Signal Router. **Paper mode is unchanged and all 170
existing tests still pass.** The upgrade adds Google login, Neon PostgreSQL, per-user
encrypted credentials, per-user runtime isolation, guarded live mode, and per-user
webhooks.

> Scope note: This phase delivers the **backend foundation + APIs + tests + docs**.
> The frontend auth UI (login screen, header, credential page, live screen) is a
> tight follow-up that consumes the API contract documented below. Nothing here
> changes the existing paper-mode UI or APIs.

---

## 1. Summary of files changed

### New files (backend)
```
app/db/__init__.py
app/db/engine.py                     # Neon engine + URL normalizer + dev/test init_db
app/db/models.py                     # users, user_sessions, user_credential_vaults, user_runs, audit_logs
app/db/crud.py                       # user/session/run/audit data-access helpers
app/auth/__init__.py
app/auth/session.py                  # signed HTTP-only cookie + OAuth state signing
app/auth/dependencies.py             # get_current_user / get_optional_user / require_admin
app/auth/google.py                   # /api/auth/google/start|callback, /api/auth/logout, /api/me
app/services/user_context.py         # CurrentUser, dev-user fallback, per-user runtime paths
app/services/user_credential_vault.py# per-user encrypted Dhan/webhook vault (DB)
app/services/live_engine.py          # live-mode safety gating + per-user run registry
app/routers/user_credentials.py      # /api/user/credentials (GET status / POST / DELETE)
app/routers/live.py                  # /api/live/start|stop|status|logs|readiness
app/routers/user_webhook.py          # /api/webhook/user/{user_id} (HMAC + replay protection)
app/routers/admin.py                 # /api/admin/users|runs|health (admin-gated)
scripts/init_db.py                   # legacy local/dev schema helper
app/tests/conftest_multiuser.py      # shared fixtures (SQLite) for the new tests
app/tests/test_db_url_normalizer.py
app/tests/test_auth_login.py
app/tests/test_user_credential_vault.py
app/tests/test_user_isolation.py
app/tests/test_live_guard.py
.env.production.example
```

### Modified files (backend) — all additive
```
app/config.py        # new settings: DATABASE_URL, AUTH_REQUIRED, APP_SECRET_KEY,
                     #   CREDENTIAL_ENCRYPTION_KEY, GOOGLE_*, ADMIN_EMAILS, cookie flags
app/main.py          # include new routers; CORS allow_credentials=True;
                     #   production fail-loud checks for secrets, DB, and Alembic state
requirements.txt     # + SQLAlchemy, psycopg[binary], itsdangerous
.env.example         # documented all new env vars
.gitignore (backend) # allow .env.production.example
```

### Action required by you (could not be automated)
- **Delete the stray file** `client_secret_…apps.googleusercontent.com (1).json` from the
  repo root. It is **not** in git history and is gitignored, but should not sit in the
  working tree. As a precaution, **rotate that OAuth client secret** in Google Cloud Console.

---

## 2. Exact environment variables

See `backend/.env.example` (local) and `backend/.env.production.example` (prod). The key new ones:

| Variable | Purpose | Prod requirement |
|---|---|---|
| `DATABASE_URL` | Neon Postgres URL (`postgresql://…?sslmode=require&channel_binding=require`) | **Required** |
| `AUTH_REQUIRED` | `true` = real Google login; `false` = dev user fallback | `true` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth app credentials | Required if AUTH_REQUIRED |
| `GOOGLE_REDIRECT_URI` | e.g. `https://api.example.com/api/auth/google/callback` | Required if AUTH_REQUIRED |
| `FRONTEND_URL` | where users land after login | recommended |
| `ADMIN_EMAILS` | comma list; gates `/api/admin/*` only — **never blocks login** | optional |
| `APP_SECRET_KEY` | signs session cookie (≥32 chars) | **Required** |
| `CREDENTIAL_ENCRYPTION_KEY` | Fernet key for per-user vault | **Required** |
| `ENABLE_LIVE_ORDERS` | global gate for real order placement | keep `false` until ready |
| `WEBHOOK_TRADING_ENABLED` | enables `/api/webhook/user/*` | `false` until ready |
| `WEBHOOK_HMAC_REQUIRED` | require per-user webhook secret + signature | `true` |

Generate secrets:
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"                         # APP_SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # CREDENTIAL_ENCRYPTION_KEY
```

The backend **fails to start in production** if `APP_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`,
or `DATABASE_URL` are missing (see `validate_production_configuration` in `app/main.py`).

---

## 3. Run locally

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then edit .env

# Optional: initialize/update DB schema (only if DATABASE_URL is set)
python -m alembic upgrade head

uvicorn app.main:app --reload --port 8001
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

> Python 3.11+ is required (the codebase uses `enum.StrEnum`).

### Two local modes
- **Pure paper mode, no DB (unchanged):** leave `AUTH_REQUIRED=false` and `DATABASE_URL` empty.
  A deterministic **dev user** owns the existing global `runtime_state/` files — your current
  paper setup works exactly as before.
- **Full multi-user dev:** set `DATABASE_URL` to a Neon (or local Postgres) URL, set
  `AUTH_REQUIRED=true`, configure `GOOGLE_*`, and log in with Google.

---

## 4. Run on the VPS

Deployment is unchanged — this is the same FastAPI app with extra routers. Use your
existing systemd unit (`deploy/layman-nova-signal-router.service`) or Docker.

```bash
# on the VPS, in the repo
cd backend
source .venv/bin/activate
pip install -r requirements.txt          # picks up SQLAlchemy / psycopg / itsdangerous
python -m alembic upgrade head           # one-time and after migrations

# restart your service (systemd example)
sudo systemctl restart layman-nova-signal-router
sudo systemctl status  layman-nova-signal-router
# logs
journalctl -u layman-nova-signal-router -f
```

If you use `docker-compose.yml`, add the new env vars to the backend service’s
environment / env_file and `docker compose up -d --build`.

---

## 5. How to test Google login locally

1. In Google Cloud Console → APIs & Services → Credentials, create/locate an **OAuth 2.0
   Client ID** (type: Web application).
2. Add Authorized redirect URI: `http://localhost:8001/api/auth/google/callback`.
3. Put `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, that `GOOGLE_REDIRECT_URI`, and
   `AUTH_REQUIRED=true` + a real `DATABASE_URL` in `backend/.env`.
4. Visit `http://localhost:8001/api/auth/google/start` → complete Google consent →
   you’re redirected to `FRONTEND_URL` with a session cookie set.
5. `GET http://localhost:8001/api/me` (with cookies) returns your user. A brand-new
   Google user is created in the `users` table with `is_admin=false` — **no allowlist
   blocks login**. If your email is in `ADMIN_EMAILS`, `is_admin=true`.

For local http, set `SESSION_COOKIE_SECURE=false` in `.env`.

---

## 6. How to connect Neon

1. Create a Neon project → copy the connection string. It looks like:
   `postgresql://user:password@ep-xxxx.region.aws.neon.tech/dbname?sslmode=require&channel_binding=require`
2. Put it in `DATABASE_URL` (env only — never hardcode).
3. The backend auto-normalizes `postgresql://` → `postgresql+psycopg://` and **keeps**
   `sslmode` and `channel_binding`. No manual change needed.
4. Run `python -m alembic upgrade head` to create/update tables. Production startup
   verifies Alembic state and does not use SQLAlchemy `create_all()` as schema authority.

---

## 7. How to save Dhan credentials (per user)

Logged-in user calls:
```
POST /api/user/credentials
{ "dhan_client_id": "1100xxxxxx", "dhan_access_token": "…", "webhook_secret": "…(optional)…" }
```
- Values are **encrypted (Fernet) before** the DB insert.
- Response returns **masked status only** (`dhan_client_id_masked`, `has_dhan_access_token`,
  `has_webhook_secret`, …). Secrets are **never** returned.
- `GET /api/user/credentials/status` → masked status.
- `DELETE /api/user/credentials` → removes credentials and stops any active live run.

---

## 8. How to run paper mode

Unchanged. The existing paper endpoints/UI work as before. With the multi-user layer:
- Paper runs belong to the logged-in user (dev user when `AUTH_REQUIRED=false`).
- Real users get isolated `runtime_state/users/<user_id>/` folders; the dev user keeps
  using the existing global `runtime_state/`.

---

## 9. How to run live mode safely (signal-only)

```
POST /api/live/start
{ "strategy_name": "supertrend", "symbol": "NIFTY", "quantity": 1,
  "risk_config": {}, "execution_mode": "signal_only" }
```
- `execution_mode` options: `signal_only` (no orders), `paper_live_data` (live data → paper
  broker), `real_orders` (gated).
- `GET /api/live/readiness?execution_mode=signal_only` returns the safety checklist with no
  side effects. `signal_only` / `paper_live_data` only require saved, decryptable credentials.
- `GET /api/live/status` and `GET /api/live/logs` return **only the current user’s** data.
- `POST /api/live/stop {"run_id": "…"}` stops **only your** run (cross-user stop → 404).

---

## 10. How to enable REAL live orders (after safety checks)

Real orders are blocked unless **every** check passes:
1. User logged in.
2. User has saved Dhan credentials.
3. Vault decrypts successfully.
4. Basic Dhan token validation passes.
5. `ENABLE_LIVE_ORDERS=true` (env).
6. `DHAN_MODE=REAL` and `DHAN_READ_ONLY_REAL_DATA=false` (env).
7. For webhook trading: `WEBHOOK_TRADING_ENABLED=true`, and if `WEBHOOK_HMAC_REQUIRED=true`
   the user must have a saved webhook secret.

Then start with `"execution_mode": "real_orders"`. Inspect readiness first:
```
GET /api/live/readiness?execution_mode=real_orders
```
`real_orders_allowed: true` confirms it will place real orders. A blocked request returns
HTTP **412** with the list of blockers — nothing is sent to Dhan.

---

## Frontend integration contract (for the follow-up UI work)

- **All API calls must send cookies:** `fetch(url, { credentials: "include" })`.
- **Login:** redirect the browser to `GET {API}/api/auth/google/start` (full-page navigation,
  not fetch). After consent the backend sets the cookie and 302s to `FRONTEND_URL`.
- **Current user:** `GET {API}/api/me` → `{ authenticated, user: { id, email, name, picture_url,
  is_admin, is_dev }, auth_required }`. 401 means show the login screen.
- **Logout:** `POST {API}/api/auth/logout`.
- **Credentials:** `GET/POST/DELETE {API}/api/user/credentials[/status]`.
- **Live:** `GET {API}/api/live/readiness`, `POST {API}/api/live/start|stop`,
  `GET {API}/api/live/status|logs`.
- **Auth guard:** don’t render dashboard/live screens until `/api/me` returns authenticated.
- Make `real_orders` visually serious with a confirmation modal; show the readiness checklist.

---

## Per-user webhook (TradingView etc.)

```
POST /api/webhook/user/{user_id}
{ "user_id":"…", "strategy":"…", "symbol":"NIFTY",
  "signal":"BUY|SELL|EXIT", "timestamp": <unix s>, "nonce":"…", "signature":"<hex>" }
```
- HMAC-SHA256 over `user_id|strategy|symbol|signal|timestamp|nonce` using **that user’s**
  saved webhook secret (`build_signature` in `app/routers/user_webhook.py`).
- Replay protection: stale timestamp (>5 min) → 400; duplicate `(user_id, nonce)` → 409.
- One user’s webhook can never trigger another user’s order (signature is bound to the user).

---

## Test results

```
backend/  →  195 passed   (170 existing + 25 new)
```
New tests: `test_db_url_normalizer`, `test_auth_login` (open login + admin detection + dev
fallback), `test_user_credential_vault` (encryption + masked status + decrypt), `test_user_isolation`
(A can’t read B creds / stop B run / see B logs), `test_live_guard` (real-orders gating +
no-credentials block + webhook HMAC/replay).

Run them:
```bash
cd backend
pytest app/tests/test_db_url_normalizer.py app/tests/test_auth_login.py \
       app/tests/test_user_credential_vault.py app/tests/test_user_isolation.py \
       app/tests/test_live_guard.py -v
pytest app/tests/test_paper_mode.py -v     # paper-mode regression
```
