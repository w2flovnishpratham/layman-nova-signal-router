# Stage 0 and Stage 1 Security Fixes Applied

Date: 2026-06-13

Scope: only Stage 0 and Stage 1 from `DEEP_REVIEW_AND_FIX_PLAN.md`.
Stages 2 through 5 are intentionally not implemented.

## What Was Fixed

### Stage 0

- Live trading remains disabled by default.
- Production startup now refuses `ENABLE_LIVE_ORDERS=true` until Stage 2 is complete.
- Production runtime state and logs must use directories outside the repository.
- Production startup rejects runtime directories inside the repository or at a filesystem root.
- Deployment config now uses:
  - `/var/lib/layman/state`
  - `/var/lib/layman/logs`
- Deployment creates those directories with mode `0700`.
- `.gitignore` now covers OAuth client JSON, SQLite/DB files, runtime state, runtime logs, and generated frontend/Python artifacts.
- `scripts/check_repo_hygiene.py` rejects sensitive or generated files if they are tracked.
- CI and VPS deployment run the repository hygiene check.

### Stage 1

- Global middleware returns `401` before protected handlers run when no valid session exists.
- Setup, engine, control, orders, positions, dashboard, broker, connection, debug, and chat-session routers also have auth dependencies.
- Public routes are limited to health, OAuth/status, safe API docs, and authenticated webhook routes.
- Webhooks do not inherit browser-cookie identity. They bind runtime scope only after their webhook secret resolves to a user.
- User-scoped runtime paths raise `RuntimeScopeError` when no user exists under authenticated or non-local operation.
- The credential vault cannot use the `"__global__"` memory bucket when auth is enabled.
- Authenticated mode creates user runtime files lazily under `users/<user_id>/`; it does not initialize shared user state.
- Unsafe global background trading workers are disabled in authenticated multi-user mode until Stage 2 redesigns them.
- All frontend HTTP requests now use one `apiFetch` wrapper with `credentials: "include"`.
- Mutating frontend requests automatically send `X-CSRF-Token`.
- OAuth login and `/api/auth/status` issue/refresh the CSRF token.
- Browser mutations require matching CSRF cookie/header values.
- Mutations with an unapproved `Origin` or `Referer` are rejected.
- Auth cookies now use `HttpOnly`, production `Secure`, and `SameSite=strict`.
- Webhook routes are exempt from browser CSRF because they retain webhook-secret/HMAC authentication.

## Tests Added

- Protected endpoint `401` checks for setup, engine, control, orders, positions, and dashboard.
- Runtime-path guard checks.
- Credential-vault no-user guard checks.
- No-global-runtime-write check.
- CSRF missing, invalid, valid, and hostile-origin checks.
- Authenticated CSRF bootstrap check.
- Webhook CSRF exemption plus webhook-auth rejection check.
- Cross-user setup, position, order, credential, state, and runtime-path isolation checks.
- Webhook-secret-to-user runtime binding check.
- Frontend request-wrapper security script.
- Repository/package hygiene script.

## Production Environment Checklist

```env
APP_ENV=production
AUTH_REQUIRED=true
ADMIN_EMAILS=<explicit allowlist>
DATABASE_URL=postgresql+psycopg://...
BACKEND_PUBLIC_BASE_URL=https://layman-api.manyacare.com
FRONTEND_ORIGIN=https://layman.manyacare.com
SESSION_TOKEN_SECRET=<32+ random characters>
TOKEN_ENCRYPTION_KEY=<valid Fernet key>
WEBHOOK_HMAC_REQUIRED=true
ENABLE_LIVE_ORDERS=false
EXECUTION_NODE_ROUTING_ENABLED=false
RUNTIME_STATE_DIR=/var/lib/layman/state
RUNTIME_LOG_DIR=/var/lib/layman/logs
DEBUG_ENABLED=false
```

Required filesystem permissions:

```bash
install -d -m 700 /var/lib/layman
install -d -m 700 /var/lib/layman/state
install -d -m 700 /var/lib/layman/logs
chmod 600 /root/layman-nova-signal-router/backend/.env
```

## How To Verify

```bash
cd backend
python -m pytest app/tests -q
python -c "from pathlib import Path; files=list(Path('app').rglob('*.py')); [compile(p.read_text(encoding='utf-8'), str(p), 'exec') for p in files]; print(len(files))"

cd ../frontend
npm run test:security
npm run lint
npm run build

cd ..
python scripts/check_repo_hygiene.py
bash -n deploy/deploy_vps.sh
bash -n deploy/configure_vps_env.sh
```

To audit physical files in a local working folder:

```bash
python scripts/check_repo_hygiene.py --worktree
```

That stricter command is expected to fail until local OAuth JSON, runtime DBs,
and runtime logs have been moved or deleted.

## Verification Results

Run on 2026-06-13:

- Backend test suite: `197 passed in 227.40s`.
- Stage 0/1 security tests: `17 passed`.
- Python source compilation: `92 files` compiled successfully.
- Alembic: `upgrade head` and `check` passed.
- Frontend API security checks: passed.
- Frontend ESLint: passed.
- Frontend production build: passed with Vite 8.0.16; 2,168 modules transformed.
- Repository tracked-file hygiene check: passed.
- Deployment shell syntax checks: passed with Git Bash.
- Git whitespace/error check: passed; only Windows LF-to-CRLF notices were emitted.
- No OAuth client JSON, SQLite database, runtime state, or runtime log file is tracked.

The optional `--worktree` hygiene scan currently reports ignored files generated
by the backend test suite under `backend/runtime_state` and
`backend/runtime_logs`. This is not a tracked-file leak, but those local files
must still be deleted or moved before treating that checkout as clean.

## Manual Operator Actions

These actions were not automated because they affect real secrets and operator data:

1. Rotate the Google OAuth client secret in Google Cloud Console.
2. Update only the production secret environment value.
3. Delete every `client_secret*.json` from the project/OneDrive folder.
4. Remove the deleted OAuth JSON from OneDrive history/recycle bin where possible.
5. Move or securely archive existing `backend/runtime_state` and `backend/runtime_logs` outside the repository.
6. Do not copy the legacy global runtime files into a user's new scoped directory without reviewing ownership.
7. Confirm `/var/lib/layman`, `/var/lib/layman/state`, and `/var/lib/layman/logs` are mode `0700`.
8. Keep `ENABLE_LIVE_ORDERS=false`.
9. Re-login after deployment so the browser receives the stricter auth and CSRF cookies.

## Not Fixed

The following remain Stage 2 or later:

- Webhook timestamp/nonce replay protection.
- Postgres/Redis trading-state migration.
- Distributed locks and multi-worker-safe trading workers.
- Per-user executor/egress-node order routing.
- Redis-backed rate limiting and session/pub-sub infrastructure.
- Dedicated webhook HMAC pepper rotation.
- Non-root/hardened systemd service.
- KMS-backed broker credential encryption.
- Backup/restore, monitoring, and incident automation.

## Risk Status

- **Authenticated multi-user paper beta:** acceptable only as a constrained beta with live orders disabled and authenticated-mode background trading workers disabled. Per-user HTTP/webhook runtime state and credentials are isolated.
- **Public live launch:** unsafe. Stage 2 execution, replay, worker, state, and egress controls are still required.
