# Security Fixes Applied — 2026-06-12

Companion to `SECURITY_REVIEW_2026_06_12.md`. All P0 items plus selected P1/P2 items are now implemented in code. Full backend test suite passes (172 passed).

## Implemented

| Finding | Fix | Files |
|---|---|---|
| C1 | `email_allowed()` fails closed on empty allowlist; production boot requires non-empty `ADMIN_EMAILS` | `auth/service.py`, `main.py` |
| C2 | Production boot refuses `AUTH_REQUIRED=false` | `main.py` |
| C3 | Webhook requires a secret that resolves to a known user whenever auth is enabled; unknown secrets get `403 UNAUTHORIZED` (`WEBHOOK_UNKNOWN_SECRET` audit event). No global-vault fallback under auth. | `routers/webhook.py` |
| C4/M5 | `_database_url()` raises in production when `DATABASE_URL` missing or SQLite; Postgres pool settings (`pool_size=5`, `max_overflow=10`, `pool_recycle=300`, `pool_pre_ping`) | `auth/db.py`, `main.py` |
| H1/M4 | `AuthSession` gains `revoked_at`, `last_used_at`, `client_ip`, `user_agent`; logout revokes the server-side session; revoked/expired sessions rejected; tokens without a session id rejected; sliding `last_used_at` (60s granularity). Additive column migration runs at startup for existing DBs. | `auth/models.py`, `auth/router.py`, `auth/security.py`, `auth/service.py`, `auth/db.py` |
| H2 | `upsert_google_user` matches strictly by `google_sub`; email collision with a different Google identity raises `AuthError` → opaque OAuth error redirect | `auth/service.py`, `auth/router.py` |
| H3 | Production boot requires a valid Fernet `TOKEN_ENCRYPTION_KEY`; in-memory plaintext vault fallback restricted to `APP_ENV in {local, test}` + `DHAN_MODE=MOCK` | `main.py`, `services/credential_vault.py` |
| H4 | Webhook rate limit keyed on `(user_id|anon) + client_host`; unresolved-secret traffic capped at 10/min when auth is enabled | `routers/webhook.py` |
| M9 | Production boot requires `WEBHOOK_HMAC_REQUIRED=true` whenever `ENABLE_LIVE_ORDERS=true`; `.env.example` default flipped to `true` | `main.py`, `.env.example` |
| L1 | `_hash_secret` no longer falls back to a literal key; raises on missing `SESSION_TOKEN_SECRET` | `services/user_connections.py` |
| H7 (partial) | Account-collision auth failures return the generic `not_allowed` code | `auth/router.py` |

`.env.example` updated: `AUTH_REQUIRED=true`, Postgres `DATABASE_URL`, `WEBHOOK_HMAC_REQUIRED=true`, with comments noting the boot-time enforcement.

## Operational actions still required (not code)

1. **Rotate the Google OAuth client secret.** A `client_secret_*.json` sits in the repo folder root. It is gitignored and not tracked, but it lives in a OneDrive-synced directory. Rotate it in Google Cloud Console, then delete the file.
2. Confirm on the VPS: Postgres bound to `127.0.0.1` only; `.env` mode `0600`; key-only SSH; `unattended-upgrades`; fail2ban on SSH.
3. Existing auth cookies issued before this change are invalidated (tokens without `asid` are now rejected) — users must log in again once deployed.

## Remaining backlog (P1/P2, in priority order)

1. **Alembic migrations** — replace `create_all` + additive-migration shim; run `alembic upgrade head` as a deploy step (alembic is already in requirements).
2. **CSRF (H5)** — `SameSite=strict` auth cookie + double-submit `X-CSRF-Token` on mutating endpoints + Origin allowlist check.
3. **Separate `WEBHOOK_HMAC_PEPPER` (H6)** — decouple webhook-secret hashing from `SESSION_TOKEN_SECRET` rotation.
4. **Webhook replay protection (M2)** — timestamp in HMAC + DB-persisted `signal_id` dedup.
5. **Redis-backed rate limiting** once multi-worker.
6. **PKCE (L3)**, token `kid`/rotation (M1/L2), egress-node fingerprint pinning (L4), audit-metadata whitelist (M10).
