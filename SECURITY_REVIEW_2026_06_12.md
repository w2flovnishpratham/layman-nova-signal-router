# Production-Readiness & Security Review — Auth / DB / Multi-User Update

**Reviewer:** Senior SDE + AppSec  
**Date:** 2026-06-12  
**Scope:** `backend/app/auth/*`, `backend/app/middleware/user_scope.py`, `backend/app/services/{user_context, user_connections, credential_vault, state_store}.py`, `backend/app/routers/{webhook, connections}.py`, `backend/app/api/{session, ws}.py`, `backend/app/config.py`, `backend/app/store/session_token.py`, deploy CI.  
**Benchmarks:** OWASP ASVS 5.0.0, OWASP API Top 10 2023, OWASP Cheat Sheets (Auth / Session / Secrets / Input Val / Logging / SSRF), NIST SSDF, PostgreSQL hardening.  
**Threat model assumed adversarial:** auth bypass, webhook abuse, `user_id` tampering, cookie theft, leaked secrets, replayed orders, DB flooding, malformed payloads, egress-routing abuse.

---

## Verdict

**NOT production-ready for real-money / multi-user. Beta-ready for single trusted user (`w2f.lovnish@gmail.com`) only, with the P0 fixes below applied first.**

Three independent fail-open paths currently allow a fully unauthenticated request to act as a privileged "global" user. The recent additions are well-structured at the model layer (good indexes, per-user runtime profile, route audit table) but the wiring still treats authentication as optional, treats the global vault as a fallback, and has no migration discipline. Until P0 closes, the only thing protecting live money is the `ENABLE_LIVE_ORDERS=False` kill switch and a manually-managed email allowlist.

---

## Severity-ranked findings

| # | Severity | Title | Affected |
|---|---|---|---|
| C1 | **CRITICAL** | `email_allowed()` fails open when `ADMIN_EMAILS=""` | `auth/service.py`, `config.py` |
| C2 | **CRITICAL** | `AUTH_REQUIRED: bool = False` is the default | `config.py`, `middleware/user_scope.py`, `auth/security.py` |
| C3 | **CRITICAL** | Webhook accepts global vault secret as fallback (cross-user / no-user routing) | `routers/webhook.py`, `services/credential_vault.py` |
| C4 | **CRITICAL** | Schema is created via `SQLModel.metadata.create_all` — no Alembic, SQLite fallback | `auth/db.py` |
| H1 | High | Logout does not invalidate `AuthSession` row — stolen cookie valid for 7d | `auth/router.py`, `auth/models.py` |
| H2 | High | `upsert_google_user` matches by `email` when `google_sub` differs — account takeover on SSO mismatch | `auth/service.py` |
| H3 | High | Dhan `access_token` encryption-at-rest depends on `TOKEN_ENCRYPTION_KEY` env, but `local_mock_without_key_allowed()` silently downgrades to in-memory plaintext | `services/credential_vault.py`, `config.py` |
| H4 | High | Webhook rate-limit keyed on `client_host` only — one abusive IP can DoS all users; no per-user / per-secret limit | `routers/webhook.py` |
| H5 | High | No CSRF protection on state-changing endpoints; cookie is `SameSite=lax` (not `strict`) | `auth/router.py`, all `POST /api/*` |
| H6 | High | `SESSION_TOKEN_SECRET` doubles as webhook-secret HMAC key — rotating one breaks the other | `services/user_connections.py::_hash_secret`, `store/session_token.py` |
| H7 | High | OAuth callback leaks validation outcome via `?oauth_error=<reason>` query param | `auth/router.py::_frontend_redirect` |
| M1 | Medium | Auth cookie TTL = 7 days, no idle timeout, no IP/UA fingerprint binding, no rotation/`kid` | `config.py`, `store/session_token.py`, `auth/models.py` |
| M2 | Medium | No replay protection on webhook (no timestamp window / nonce; only `signal_id` dedup) | `routers/webhook.py` |
| M3 | Medium | Webhook redacts only top-level keys; nested `secret`/`access_token` will land in `webhook_raw.log` | `routers/webhook.py::_safe_raw_body_for_log` |
| M4 | Medium | `AuthSession` table lacks `revoked_at`, `last_used_at`, `client_ip`, `user_agent` columns — no forensics, no targeted revocation | `auth/models.py` |
| M5 | Medium | `_database_url()` falls back to SQLite when env vars empty — production-misconfiguration safety net is missing | `auth/db.py` |
| M6 | Medium | `init_database()` runs `create_all` on every startup — race in multi-worker, hides drift | `auth/db.py`, `main.py` |
| M7 | Medium | `scoped_runtime_dir()` returns the **global** dir when no `user_id` — silent cross-user collision in any path that forgets to bind | `services/user_context.py` |
| M8 | Medium | `_LOCAL_MEMORY_PAYLOADS["__global__"]` is process-wide and shared across requests under no-user contexts | `services/credential_vault.py` |
| M9 | Medium | `WEBHOOK_HMAC_REQUIRED: bool = False` — HMAC enforcement is opt-in | `config.py`, `routers/webhook.py` |
| M10 | Medium | `OrderRouteAudit.metadata_json` (free-form JSON) — risk of inadvertent token / PII spill | `auth/models.py`, callers in `user_connections.py` |
| L1 | Low | Default `SESSION_TOKEN_SECRET = "change-me-in-production"` is a literal sentinel; production guard exists but `_hash_secret` falls back to the same literal | `services/user_connections.py::_hash_secret`, `config.py` |
| L2 | Low | Token format has no `kid`, no `aud`, no `jti` — no rotation primitive, no audience binding, no per-token revocation | `store/session_token.py` |
| L3 | Low | OAuth state cookie not bound to PKCE — Google's `code` exchange relies solely on opaque state | `auth/router.py` |
| L4 | Low | `internal_base_url` and `public_ip` on `EgressNode` are unauthenticated trust roots — no signed pinning | `auth/models.py`, `services/execution_router.py` |
| L5 | Low | CI workflow hardcodes VPS IP; `actions/checkout@v4` / `actions/setup-python@v5` Node 20 deprecation | `.github/workflows/*` |
| L6 | Low | `email_allowed()` does case normalization but no Unicode NFKC normalization (homograph trick) | `auth/service.py` |

---

## Detailed findings

### C1 — `email_allowed()` fails open when `ADMIN_EMAILS=""`

**Risk.** ASVS V2.1.5 / API1:2023. Any Google-authenticated email becomes an authorized user when the allowlist is empty, because the function returns `True` on empty set.

**Exploit.** Attacker registers any Gmail / Workspace identity → clicks "Continue with Google" → OAuth completes → `email_allowed("attacker@gmail.com") → True` (allowlist empty) → full `AuthSession` issued → reaches `/webhook`, `/connections`, `/orders` as that new user. On a 1-user VPS this means the entire production instance is open to anyone with a Google account if `ADMIN_EMAILS` is ever cleared, never set, or whitespace.

**Affected.** `backend/app/auth/service.py::email_allowed`; default `ADMIN_EMAILS: str = ""` in `config.py`.

**Fix.**
```python
def email_allowed(email: str) -> bool:
    allowed = admin_emails()
    if not allowed:
        # Fail closed. Operator must explicitly set ADMIN_EMAILS.
        return False
    return email.strip().lower() in allowed
```
Plus a startup guard in `main.py`:
```python
if settings.APP_ENV.lower() == "production" and not admin_emails():
    raise RuntimeError("ADMIN_EMAILS must be set in production.")
```

**Verifying test.** Add `backend/app/tests/test_auth_allowlist.py`:
```python
def test_email_allowed_empty_admin_emails_denies(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "")
    assert email_allowed("attacker@example.com") is False

def test_email_allowed_admin_emails_set_allows_listed(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "ops@example.com")
    assert email_allowed("ops@example.com") is True
    assert email_allowed("other@example.com") is False
```

---

### C2 — `AUTH_REQUIRED` defaults to `False`

**Risk.** ASVS V1.4 / API5:2023. The middleware (`UserRuntimeScopeMiddleware`) and guard (`require_user_if_auth_enabled`) both no-op when `AUTH_REQUIRED=False`. Forgetting one env var in a deploy disables every authorization check in the app.

**Exploit.** A `.env` typo (`AUTH_REQUIRED=` instead of `AUTH_REQUIRED=true`, or missing entirely) on a redeploy. After that, every `POST /api/connections/dhan`, `POST /api/connections/webhook`, `POST /webhook/tradingview` accepts unauthenticated traffic, writes to the **global** runtime dir, and overwrites the legitimate user's credentials.

**Affected.** `config.py:79`, `middleware/user_scope.py:15`, `auth/security.py::require_user_if_auth_enabled`.

**Fix.** Invert the default. Make `AUTH_REQUIRED: bool = True`. Make the "disabled" path local-dev-only via explicit `APP_ENV == "local"` gating:
```python
def auth_enabled() -> bool:
    if not settings.AUTH_REQUIRED:
        if settings.APP_ENV.lower() == "production":
            raise RuntimeError("AUTH_REQUIRED cannot be false in production.")
        return False
    return True
```
And remove the silent `return None` from `require_user_if_auth_enabled` — callers should always receive a `User` or 401.

**Verifying test.**
```python
def test_auth_required_default_is_true():
    assert Settings().AUTH_REQUIRED is True

def test_auth_disabled_in_production_raises(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    with pytest.raises(RuntimeError):
        auth_enabled()
```

---

### C3 — Webhook still falls back to global vault secret

**Risk.** API2:2023, OWASP Webhook Cheat Sheet. After `_bind_webhook_runtime_scope` tries per-user lookup, the handler calls `expected_secret = get_webhook_secret()` (line 308). `get_webhook_secret()` resolves via `scoped_runtime_file(CREDENTIALS_FILE)` which returns the **global** file path when no user is bound. So if any global webhook secret exists (legacy install, ops test), an attacker who guesses or replays it gets accepted with **no user context**, routes through global credentials, global state, and global egress.

**Exploit.** (a) Operator runs a one-shot test with a global secret, never deletes it. (b) Attacker discovers it (leaked TradingView screenshot, prior log, weak entropy). (c) Posts to `/webhook/tradingview` — no auth cookie required; `find_user_id_by_webhook_secret` returns None (no profile matches); `get_webhook_secret()` returns the global; `payload.secret == expected_secret`; order placed against whatever credentials the global vault holds.

**Affected.** `routers/webhook.py:202-402`, `services/credential_vault.py::get_webhook_secret`, `services/user_context.py::scoped_runtime_dir`.

**Fix.** Require a resolved user for the webhook endpoint. Drop the global-fallback read.
```python
# routers/webhook.py
def _bind_webhook_runtime_scope(raw_body: str) -> str | None:
    ...
    user_id = find_user_id_by_webhook_secret(secret)
    if not user_id:
        return None
    set_current_user_id(user_id)
    return user_id

@router.post("/tradingview")
async def tradingview_webhook(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    user_id = _bind_webhook_runtime_scope(raw_body)
    if user_id is None:
        log_audit_event("WEBHOOK_UNKNOWN_SECRET", "...", severity="WARNING")
        return _response(403, WebhookResponse(accepted=False, status="UNAUTHORIZED",
                                               message="Webhook rejected: unknown secret."))
    ...
```
Also move webhooks under path-scoped URLs: `/webhook/u/{user_id_public}/tradingview` to make TV configs per-user inspectable, and make `find_user_id_by_webhook_secret` validate that the path user matches the secret-resolved user.

Harden the credential vault: in `scoped_runtime_file`, **raise** instead of returning the global path when `current_user_id()` is None and `auth_enabled()`. Add a unit test that proves no path returns global under auth-on.

**Verifying test.**
```python
def test_webhook_rejects_unknown_secret(client, monkeypatch):
    r = client.post("/webhook/tradingview", json={"secret": "not-a-real-user-secret",
                                                  "signal_id": "x", "action": "BUY"})
    assert r.status_code == 403
    assert r.json()["status"] == "UNAUTHORIZED"

def test_webhook_with_only_global_secret_is_rejected(client, set_global_vault_only):
    # legacy: global webhook secret exists, no per-user profile
    set_global_vault_only(webhook_secret="legacy-global-secret-1234567890")
    r = client.post("/webhook/tradingview", json={"secret": "legacy-global-secret-1234567890",
                                                  "signal_id": "x", "action": "BUY"})
    assert r.status_code == 403
```

---

### C4 — No Alembic, SQLite fallback, `create_all` on every boot

**Risk.** NIST SSDF PS.1 / OPS-1. Production schema drift becomes unrecoverable; any model change in code silently mismatches the live DB; column adds happen at startup race in multi-worker; if both DB env vars are unset on a fresh deploy, the app silently writes to a SQLite file under `runtime_state/` and looks fine until the next restart wipes the volume.

**Exploit.** Operator rolls back a code change but the DB column added by the prior deploy stays. Or fresh container on a new node has no `DATABASE_URL` env → SQLite. Auth works locally on that container; users created there don't exist in the "real" Postgres on the next container; cookies issued by the SQLite container are accepted nowhere else.

**Affected.** `backend/app/auth/db.py` (entire file), `backend/app/main.py` (calls `init_database()` on startup).

**Fix.**
1. Add Alembic. `alembic init alembic`, autogenerate initial revision from current models, commit `alembic/versions/0001_*.py`. Make `init_database()` a no-op in production; run `alembic upgrade head` from a deploy step, not from app startup.
2. Fail closed on missing DB URL in production:
```python
def _database_url() -> str:
    configured = settings.DATABASE_URL.strip() or settings.AUTH_DATABASE_URL.strip()
    if configured:
        return configured
    if settings.APP_ENV.lower() == "production":
        raise RuntimeError("DATABASE_URL is required in production.")
    RUNTIME_STATE_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{RUNTIME_STATE_DIR / 'auth.sqlite3'}"
```
3. Add `pool_size`, `max_overflow`, `pool_recycle=300` to `create_engine` for Postgres.

**Verifying test.**
```python
def test_database_url_required_in_production(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DATABASE_URL", "")
    monkeypatch.setattr(settings, "AUTH_DATABASE_URL", "")
    with pytest.raises(RuntimeError):
        _database_url()
```
Plus a CI check: `alembic upgrade head` then `alembic check` (verifies models match schema).

---

### H1 — Logout does not invalidate `AuthSession`

**Risk.** ASVS V3.3.1, V3.3.4. Cookie theft (XSS payload bypassing CSP, malicious browser extension, leaked log file, stolen device) gives the attacker a 7-day usable session even after the legitimate user clicks "Sign out".

**Exploit.** Steal cookie → user logs out → attacker keeps using the cookie for up to 7 days. The `auth_sessions` row is still valid; `current_user_from_request` finds it; `expires_at` hasn't passed.

**Affected.** `auth/router.py::logout`, `auth/models.py::AuthSession`, `auth/security.py::current_user_from_request`.

**Fix.** Add `revoked_at: datetime | None` column to `AuthSession`. On logout, set it to `now()`. In `current_user_from_request`, treat a non-null `revoked_at` as invalid.
```python
class AuthSession(SQLModel, table=True):
    ...
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    client_ip: str | None = None
    user_agent: str | None = None
```
On logout:
```python
with session_scope() as s:
    row = s.get(AuthSession, asid)
    if row and row.revoked_at is None:
        row.revoked_at = utc_now_dt()
        s.commit()
```
Add an "All other sessions" sign-out and an admin endpoint to revoke a `user_id`.

**Verifying test.**
```python
def test_logout_revokes_session(client, fake_user):
    client.cookies.set("layman_auth", issue_cookie_for(fake_user))
    r = client.post("/api/auth/logout"); assert r.status_code == 200
    client.cookies.set("layman_auth", issue_cookie_for(fake_user))  # replay stolen cookie
    r = client.get("/api/me"); assert r.status_code == 401
```

---

### H2 — `upsert_google_user` account-takeover via email rebind

**Risk.** Google can (rarely) rotate `sub` for an account; more importantly, an attacker who controls an email address that Google later re-issues, or who registers an unverified email in a Workspace tenant, can collide with an existing email row. Current `upsert_google_user` matches by `google_sub` first, then **falls back to email**, then overwrites the existing row's `google_sub` with the new one.

**Exploit.** Mainly relevant if the operator ever switches OAuth providers (Workspace migration) or if Google ever re-mints `sub`. Real-world likelihood is low but the consequence is a silent owner-change of the user record (and thus its `DhanAccount`, `UserRuntimeProfile`, `OrderRouteAudit`).

**Affected.** `auth/service.py::upsert_google_user`.

**Fix.** Match strictly by `google_sub`. On `email`-only match with different `google_sub`, raise; require operator action.
```python
def upsert_google_user(google_sub, email, ...):
    with session_scope() as s:
        user = s.exec(select(User).where(User.google_sub == google_sub)).first()
        if user:
            return _update_login(user, ...)
        existing_email = s.exec(select(User).where(User.email == email)).first()
        if existing_email:
            raise AuthError("Email already linked to another Google identity. Contact support.")
        return _create_user(google_sub=google_sub, email=email, ...)
```
Additionally enforce `email_verified=True` (already checked in callback — keep).

**Verifying test.**
```python
def test_upsert_rejects_email_collision_with_different_sub():
    upsert_google_user("sub-A", "x@example.com", ...)
    with pytest.raises(AuthError):
        upsert_google_user("sub-B", "x@example.com", ...)
```

---

### H3 — Dhan token encryption silently downgrades to in-memory plaintext

**Risk.** `local_mock_without_key_allowed()` returns True when `APP_ENV != "production"` **and** `DHAN_MODE == "MOCK"`. In that mode `_write_payload` skips Fernet and stashes credentials in `_LOCAL_MEMORY_PAYLOADS` (plaintext, process-wide). Operationally this is fine for local dev, but: (a) it's keyed off env strings that are easy to misset; (b) on a staging box with `APP_ENV=staging` or `DHAN_MODE=MOCK` but real tokens entered, tokens never touch disk — they also never persist across restart, so users will silently lose connection; (c) if `TOKEN_ENCRYPTION_KEY` is later wiped from `.env`, the vault becomes undecryptable and the next save will throw — UX is bad and there's no rotation key support.

**Exploit / failure mode.** Operator deploys to staging with `APP_ENV=staging`, real Dhan token saved, app restart loses token. Or: production deployer forgets `TOKEN_ENCRYPTION_KEY`, fresh boot raises `VaultError("TOKEN_ENCRYPTION_KEY is missing.")` only at first save (not at boot). No startup guard.

**Affected.** `services/credential_vault.py`, `config.py:103`.

**Fix.**
1. Startup guard: when `APP_ENV=="production"`, require `TOKEN_ENCRYPTION_KEY` to be a valid Fernet key. Fail boot otherwise.
2. Add key-rotation envelope: store `key_id` next to ciphertext; support a `TOKEN_ENCRYPTION_KEYS` map (`{kid: key}`) so an operator can rotate.
3. Move the Dhan access-token storage out of a per-user JSON file into a dedicated `dhan_credentials` table column `encrypted_access_token BYTEA NOT NULL`, `kid TEXT NOT NULL`, with a Postgres-side encryption envelope (pgcrypto optional). The current `DhanAccount.access_token_present: bool` should become `dhan_credentials.encrypted_access_token IS NOT NULL`.
4. Remove the in-memory fallback or restrict it to `APP_ENV=="local"` only.

**Verifying test.**
```python
def test_production_requires_encryption_key(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError):
        ensure_vault_ready_for_production()
```
Operationally: alert on any `WRITE` to the credential vault where `vault_ready() is False`.

---

### H4 — Rate-limit keyed only on `client_host`

**Risk.** API4:2023. `_webhook_rate_limited(client_host)` deduplicates by IP only. (a) TradingView routes through a small pool of egress IPs — multiple legitimate users share the same `client_host`, so one noisy account silently rate-limits everyone. (b) An attacker behind a cloud-NAT shares the IP with a real user; abusive requests from one CIDR don't get isolated.

**Affected.** `routers/webhook.py:74-86`.

**Fix.** Compound key on `(user_id or "unknown") + ":" + client_host`. Add a separate "unauthenticated" bucket with a much stricter limit (e.g., 10/min) so failed-secret traffic can't drown out valid users. Persist counters in Redis (recommended) instead of a per-process dict so multi-worker counts add up.
```python
def _rate_key(user_id: str | None, client_host: str) -> str:
    return f"{user_id or 'anon'}|{client_host}"
```

**Verifying test.**
```python
def test_rate_limit_isolates_users(client, two_users):
    # user A exhausts limit; user B should still succeed
    for _ in range(LIMIT): post_as(two_users.a)
    r_a = post_as(two_users.a); assert r_a.status_code == 429
    r_b = post_as(two_users.b); assert r_b.status_code != 429
```

---

### H5 — Missing CSRF protection on state-changing endpoints

**Risk.** ASVS V13.2.3 / OWASP CSRF Cheat Sheet. Cookie is `SameSite=lax`, which protects against most cross-site POSTs from third-party origins but **not** against same-site subdomain attacks (`evil.manyacare.com`), reflected XSS that triggers same-origin fetches, or browser quirks. State-changing endpoints (`POST /api/auth/logout`, `POST /api/connections/dhan`, `POST /api/orders/*`, `POST /api/engine/start`) accept cookies without a CSRF token.

**Affected.** `auth/router.py` (logout), `routers/connections.py`, every router that mutates state.

**Fix.**
1. Tighten cookie: `SameSite=strict` for the auth cookie (OAuth state cookie stays `lax`).
2. Issue a `__Host-csrf` cookie (`Secure; SameSite=strict; HttpOnly=False; Path=/`) on login; require a matching `X-CSRF-Token` header on all non-GET / non-HEAD requests (double-submit pattern).
3. Reject any request with `Origin` or `Referer` outside the allowlist (`FRONTEND_ORIGIN`).

**Verifying test.**
```python
def test_state_changing_request_without_csrf_token_rejected(client, fake_user):
    client.cookies.set("layman_auth", issue_cookie_for(fake_user))
    r = client.post("/api/connections/dhan", json={...})  # no X-CSRF-Token
    assert r.status_code == 403
```

---

### H6 — `SESSION_TOKEN_SECRET` reused as HMAC key for webhook secret hashes

**Risk.** `user_connections._hash_secret` uses `settings.SESSION_TOKEN_SECRET` as the HMAC key for storing `webhook_secret_hash`. Rotating `SESSION_TOKEN_SECRET` (e.g., after a suspected leak) silently invalidates every user's webhook routing (their saved hash no longer matches any incoming secret), and that breakage will surface only as "webhook rejected" with no clear cause. Conversely, reusing a single secret for two security domains weakens both.

**Affected.** `services/user_connections.py:37-39`.

**Fix.** Add a distinct setting `WEBHOOK_HMAC_PEPPER` (separate Fernet/random 32-byte key). Compute `webhook_secret_hash = HMAC(WEBHOOK_HMAC_PEPPER, normalized_secret)`. Document rotation procedure (offline re-hash via a migration script that has both old and new pepper).

**Verifying test.** Negative test: assert `_hash_secret` no longer references `SESSION_TOKEN_SECRET`. Plus a migration unit test.

---

### H7 — OAuth error reason leaked via query string

**Risk.** `_frontend_redirect(error="invalid_state")` redirects to `FRONTEND_ORIGIN/?oauth_error=invalid_state`. That reveals which validation rung failed (state, code exchange, email_verified, allowlist), letting an attacker calibrate. Also ends up in browser history, Referer headers on next-link navigation, and analytics.

**Affected.** `auth/router.py::_frontend_redirect`.

**Fix.** Use opaque error codes (`oauth_error=e_generic`) plus an internal trace-id; record the actual reason server-side keyed by trace-id.

---

### M1 — 7-day auth cookie, no idle timeout, no fingerprint, no `kid`

**Risk.** ASVS V3.4. Long-lived cookies amplify every other risk (theft, replay, lost-device). No `kid` means no rotation primitive: if `SESSION_TOKEN_SECRET` leaks, the only response is to rotate it and log every user out (which is the right answer but lacks gradual migration).

**Fix.**
- Reduce `AUTH_COOKIE_TTL_SECONDS` to 24 h, refresh sliding window on activity.
- Add idle-timeout column `AuthSession.last_used_at`; reject if `(now - last_used_at) > IDLE_MAX`.
- Bind to a coarse client fingerprint (UA family + /24 of IP) recorded at issue; on mismatch require re-auth (warn first, enforce later).
- Add `kid` to signed token payload; resolve key from `SESSION_TOKEN_KEYS: dict[str, str]`.

---

### M2 — No replay protection on webhook beyond `signal_id` dedup

**Risk.** API8:2023. `signal_id` dedup is in-memory (`add_seen_signal`/`has_seen_signal` in `state_store`) — survives a single process, not a restart. An attacker who captured a valid `(payload, signature)` pair can replay across restarts or against a sibling worker that hasn't seen the id.

**Fix.** Require `x-nova-timestamp` header (unix seconds), reject if `abs(now - ts) > 60`, include `ts` in the HMAC. Persist `signal_id` dedup in Postgres (`seen_signals` table with TTL via partial index on `created_at`).

---

### M3 — Webhook redaction is shallow

**Risk.** `_safe_raw_body_for_log` redacts only top-level keys named `secret`/`access_token`/`token`/etc. Nested payloads (`{"meta": {"access_token": "..."}}`) pass through into `webhook_raw.log`.

**Fix.** Recursive redaction (already partially implemented in `redact` but called from a top-level loop only — verify and add tests with nested fixtures).

---

### M4 — `AuthSession` lacks revocation / forensics columns

Covered by H1 fix. Add `revoked_at`, `last_used_at`, `client_ip`, `user_agent`.

---

### M5 — SQLite fallback in `_database_url`

Covered by C4 fix.

---

### M6 — `create_all` on every startup

Covered by C4 fix. Additionally remove `init_database()` from the FastAPI startup hook; gate it behind `APP_ENV in {"local","test"}`.

---

### M7 — `scoped_runtime_dir` global fallback

**Risk.** Every helper that reaches `scoped_runtime_dir()` while `current_user_id()` is None silently writes to the **shared global** directory. The webhook path covers the visible cases, but any background task, scheduler, or imported helper that forgets to bind the context-var (e.g., a startup migration, a websocket fan-out, an order-monitor thread spawned outside the request lifecycle) will read/write the wrong directory.

**Fix.** Add a strict mode tied to `AUTH_REQUIRED`:
```python
def scoped_runtime_dir(user_id: str | None = None) -> Path:
    user_id = user_id or current_user_id()
    if not user_id:
        if settings.AUTH_REQUIRED:
            raise RuntimeError("scoped_runtime_dir called outside a user context")
        return RUNTIME_STATE_DIR
    return RUNTIME_STATE_DIR / "users" / safe_user_path_segment(user_id)
```
Audit every caller via `grep -r "scoped_runtime" backend/app` and confirm each runs under a bound user. Add a CI test that scans for module-level reads from the global runtime dir.

---

### M8 — `_LOCAL_MEMORY_PAYLOADS["__global__"]` is process-wide

When `auth_enabled()` is False (or any path enters with no user-id), all "users" share one in-memory entry under `__global__`. Compounded by M7. Fix together with C2 + M7.

---

### M9 — `WEBHOOK_HMAC_REQUIRED: bool = False` default

HMAC enforcement is opt-in. For production with real money, the default should be on.

**Fix.** Flip default to True. Document the TradingView side (HMAC header generation) in the setup flow before letting the user enable live.

---

### M10 — `OrderRouteAudit.metadata_json` free-form JSON

**Risk.** Callers can stuff arbitrary content (entire payload, headers, broker response). Anything containing access tokens or PII ends up persisted indefinitely.

**Fix.** Add a `sanitize_audit_metadata(d: dict) -> dict` helper that whitelists keys (`signal_id`, `action`, `qty`, `strike`, `option_side`, `payload_format`, `client_host`) and never permits `secret`, `access_token`, `authorization`, `cookie`. Call it on every insert.

---

### L1 — `_hash_secret` falls back to literal `"change-me-in-production"`

`user_connections._hash_secret` has `key = settings.SESSION_TOKEN_SECRET.strip() or "change-me-in-production"`. The production guard for `SESSION_TOKEN_SECRET` in `main.py` is the only thing preventing this from being hit; if that guard ever regresses, the HMAC key becomes a known string.

**Fix.** Remove the fallback. Raise instead.

---

### L2 — Token format has no `kid`, `aud`, `jti`

Covered by M1.

---

### L3 — No PKCE in OAuth flow

State cookie + code exchange is acceptable for confidential clients but PKCE costs ~10 lines and closes one more class of attacks.

**Fix.** Generate `code_verifier` per request, send `code_challenge=S256(verifier)` in `/auth?...`, store verifier in the state cookie (signed), include `code_verifier` in `/token` exchange.

---

### L4 — `EgressNode` trust roots are unauthenticated

`public_ip` and `internal_base_url` are inserted via `register_egress_node` with no signed pinning; whoever has DB write access could rebind a user's egress.

**Fix.** Add a `pubkey_fingerprint` column populated from the node's SSH host key at registration; verify it on every routing call.

---

### L5 — CI workflow uses deprecated runner actions and hardcodes VPS IP

**Fix.** Bump `actions/checkout` and `actions/setup-python` to `@v5`/`@v6` once GitHub publishes Node 22 versions. Move VPS IP to a repo variable.

---

### L6 — Email normalization

Add `email.encode("idna").decode("ascii")` plus NFKC normalization before allowlist comparison.

---

## Prioritized remediation plan

### P0 — Must fix before any real user / live trading

1. **C1** Fail-closed `email_allowed()`; production startup guard requires `ADMIN_EMAILS` non-empty.
2. **C2** Flip `AUTH_REQUIRED` default to `True`; production guard rejects `False`; remove silent `return None` from `require_user_if_auth_enabled`.
3. **C3** Remove the global-vault fallback in the webhook handler; require resolved user; reject unknown secrets with `403/UNAUTHORIZED`.
4. **C4** Add Alembic, fail boot on missing `DATABASE_URL` in production, run migrations from deploy step.
5. **H1** Add `revoked_at` to `AuthSession`; logout writes it; `current_user_from_request` rejects revoked.
6. **H3** Production startup guard requires valid `TOKEN_ENCRYPTION_KEY`; restrict in-memory fallback to `APP_ENV=="local"`.
7. **M9** Flip `WEBHOOK_HMAC_REQUIRED` default to `True`; update setup flow to enforce TradingView HMAC config.
8. **Operational:** confirm Postgres bound to `127.0.0.1:5432` only (`ss -tlnp | grep 5432`), confirm `/root/.layman_pg_password` is mode 0600 root-only, confirm `.env` file is mode 0600.

### P1 — Must fix before scaling beyond a single trusted user

9. **H2** Strict `google_sub` matching in `upsert_google_user`; reject email collision.
10. **H4** Compound rate-limit key `(user_id, client_host)`; separate stricter unauthenticated bucket; move to Redis.
11. **H5** Cookie `SameSite=strict`; CSRF double-submit on all mutating endpoints; `Origin`/`Referer` allowlist.
12. **H6** Separate `WEBHOOK_HMAC_PEPPER`; migrate hashes.
13. **H7** Opaque OAuth error codes + server-side trace.
14. **M1** 24 h cookie, sliding refresh, idle-timeout, fingerprint binding, `kid`.
15. **M2** Timestamped HMAC + Postgres-persisted `signal_id` dedup.
16. **M4 + M7 + M8** `AuthSession` forensics columns; strict mode for `scoped_runtime_dir`; remove `__global__` in-memory bucket under auth-on.
17. **M10** Whitelist `OrderRouteAudit.metadata_json`.

### P2 — Hardening

18. **L1** Drop literal fallback in `_hash_secret`.
19. **L2** Token rotation primitive (`kid`, `aud`, `jti`, key map).
20. **L3** PKCE in OAuth.
21. **L4** Pin egress nodes by SSH host fingerprint.
22. **L5** Pin CI action versions, move VPS IP to repo variable.
23. **L6** Unicode/IDNA normalization of emails.
24. **M3** Confirm recursive redaction in webhook log fixtures.
25. **M5/M6** Drop `init_database()` from startup hook in production.

---

## Residual risk after P0 + P1

Even with all P0+P1 applied, the system carries:

- **Single-tenant database compromise.** Postgres is local-only; a VPS root compromise (SSH key theft, package supply-chain) exposes the entire user table including encrypted Dhan tokens. The Fernet key sits in `.env` on the same box, so disk capture defeats encryption-at-rest. **Mitigation:** keep `TOKEN_ENCRYPTION_KEY` in a separate KMS once budget allows; until then, monitor `/etc/ssh/sshd_config` for change, enforce key-only SSH, install `unattended-upgrades`, alert on any new IP logging in.
- **TradingView trust boundary.** Webhook secrets are static tokens; TradingView itself can be socially engineered or have its account stolen. Mitigation: short TTL on webhook secrets, rotation reminders, IP-allowlist TradingView's published egress list, and refuse any signal without HMAC.
- **OAuth provider compromise.** Google account takeover (lost phone, weak recovery) gives full app access. Mitigation: require `email_verified=True` (already in place), require `hd` (hosted-domain) claim for Workspace tenants, mandate hardware-key MFA for the operator account, and add an admin "block this user_id" panel.
- **Time-of-check / time-of-use on `ENABLE_LIVE_ORDERS`.** The kill switch is a config bool read at order-placement time. A race between flipping it off and an in-flight order is possible. Mitigation: re-read inside the broker call site and inside `route_signal` before any HTTP to Dhan.
- **No Postgres backup strategy yet.** Loss of `users` / `auth_sessions` / `order_route_audit` would force every user to re-onboard and would erase audit trail. Mitigation: nightly `pg_dump` to off-VPS object storage, encrypted, with 30-day retention.
- **Single VPS = single failure domain.** No HA. Acceptable for beta, not for paid live users. Mitigation: documented runbook for spin-up of a replacement VPS, IaC for the egress nodes, DB restore drill at least monthly.

## Monitoring / detection controls to add

- Audit-log alerts (PagerDuty / email): `WEBHOOK_AUTH_FAILED` > 10/min, `WEBHOOK_UNKNOWN_SECRET` > 5/min, `WEBHOOK_HMAC_AUTH_FAILED` > 5/min, any `OAUTH_INVALID_STATE`, any `EMAIL_NOT_ALLOWED`.
- Metric: rolling count of distinct `AuthSession.user_id`s with `last_used_at` in the past hour — alert on unexpected spikes (signals onboarding abuse).
- Metric: `dhan_credentials` write rate per user; alert on > 3 saves / 24h (signals token-rotation problems or attempts to overwrite).
- Daily report: every `OrderRouteAudit` with `status != "OK"` and `source_ip` that doesn't match the user's assigned `egress_node.public_ip` (catches routing leaks).
- Health probe: `vault_ready()` and `_database_url()` checked from `/healthz`; alert on either becoming false.
- Filesystem monitor on `/etc/sudoers`, `/root/.ssh/authorized_keys`, `/etc/ssh/sshd_config`, `/root/.layman_pg_password`, `backend/.env` (auditd or AIDE).
- Fail2ban on SSH and on `/api/auth/google` (5 failures from one IP in 5 min → 30 min ban).

## What I am not claiming

This review reflects the code paths I read. I did **not** verify: the live behavior of `services/execution_router.py` under the new per-user audit, the websocket auth gate in `api/ws.py`, the per-user file migration in `services/state_store.py`, or runtime behavior under concurrent users. Those should be follow-up reviews before the P1 scaling milestone. I am also explicitly **not** claiming the system is "fully secure" — the items above are the ones I can name; what I cannot name is what I have not yet seen.
