# NOVA C1+C2 — isolated staging bring-up & real smoke runbook

You run this on an **isolated** target (never the production VPS `187.127.153.128`
or its database). Deploys the approved C1+C2 runtime, then walks the real
admin-only smoke: Claude conversion → approve → TradingView compile → SELF
install → HOLD → Paper Ready.

- **Runtime source branch:** `phase-product-c1-c2-staging-integration`
  (= `origin/phase-product-c2-postgres-status-width-fix`)
- **Deploy SHA:** `1627d620d98fcf9986543bfb6386a3b43643ab5c` (Alembic head `0017`)
- **Never** deploy this to `main`/production; **never** enable Live.

Why not the existing workflow: `.github/workflows/deploy-staging.yml` is pinned
to the `phase-2b2a-read-shadow` lineage and `alembic heads == 0010`; it rejects
this C1+C2 SHA (head `0017`). This runbook is the manual isolated path instead.

## 0. Safety invariants (must stay true the whole time)

```
ENABLE_LIVE_ORDERS=false
PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED=false
DHAN_MODE=MOCK
CLAUDE_CONVERSION_ENABLED=true      # admin-only by design (ADMIN_EMAILS gate)
C2_TRADINGVIEW_INSTALLATION_ENABLED=true
```

The Anthropic key lives **only** in the staging backend env file — never in Git,
the frontend, the browser, the database, logs, or chat.

## 1. Isolated staging database (PostgreSQL 16)

Use a dedicated DB, distinct from production. Simplest isolated option:

```bash
docker run -d --name nova-staging-pg \
  -e POSTGRES_PASSWORD=<choose> -e POSTGRES_DB=nova_staging \
  -p 127.0.0.1:55433:5432 postgres:16
```

Staging `DATABASE_URL=postgresql://postgres:<choose>@127.0.0.1:55433/nova_staging`.
Confirm it is empty/new and is NOT the production database before migrating.

## 2. Backend release (isolated path + venv)

```bash
sudo install -d -o "$USER" /opt/nova-staging
git clone <repo> /opt/nova-staging/src && cd /opt/nova-staging/src
git checkout 1627d620d98fcf9986543bfb6386a3b43643ab5c
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
```

## 3. Staging env file (outside the repo)

Create `/etc/nova-staging/nova-staging.env` (chmod 600, root/service-owned):

```env
APP_ENV=isolated_staging
DATABASE_URL=postgresql://postgres:<choose>@127.0.0.1:55433/nova_staging

# Auth + crypto (generate fresh; do not reuse production)
AUTH_REQUIRED=true
APP_SECRET_KEY=<48+ random chars>
CREDENTIAL_ENCRYPTION_KEY=<Fernet key>
GOOGLE_CLIENT_ID=<staging OAuth client>
GOOGLE_CLIENT_SECRET=<staging OAuth secret>
ADMIN_EMAILS=w2f.lovnish@gmail.com

# C1 admin-only Claude conversion (key added in step 6, not now)
CLAUDE_CONVERSION_ENABLED=true
ANTHROPIC_API_KEY=
CLAUDE_CONVERSION_MODEL=claude-sonnet-5
CLAUDE_CONVERSION_DAILY_ADMIN_LIMIT=3
CLAUDE_CONVERSION_DAILY_GLOBAL_LIMIT=10
CLAUDE_CONVERSION_MAX_REPAIRS=1

# C2 installation + private HOLD (Paper only)
C2_TRADINGVIEW_INSTALLATION_ENABLED=true
PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED=true
PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED=false

# Execution authority OFF
ENABLE_LIVE_ORDERS=false
DHAN_MODE=MOCK
```

Generate secrets (values never printed to chat):

```bash
python -c "import secrets;print(secrets.token_urlsafe(48))"                 # APP_SECRET_KEY
python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"  # CREDENTIAL_ENCRYPTION_KEY
```

`CLAUDE_CONVERSION_MODEL`: use a current Claude model id (e.g. `claude-sonnet-5`
for cost/latency, `claude-opus-4-8` for maximum fidelity). Do not hardcode a
model in code — it is read from this env var.

## 4. Migrate to 0017 and verify

```bash
cd /opt/nova-staging/src/backend
set -a; . /etc/nova-staging/nova-staging.env; set +a
.venv/bin/python -m alembic current      # inspect starting revision (fresh = none)
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic current      # expect: 0017_expand_admin_review_status (head)
```

Confirm the width fix on the real schema:

```sql
SELECT column_name, character_maximum_length FROM information_schema.columns
WHERE table_name='strategy_admin_reviews' AND column_name IN ('previous_status','new_status');
-- expect character varying 64 for both
```

Do not run `downgrade` on the shared staging DB after a successful upgrade.

## 5. Service, frontend, health

Backend service: adapt `deploy/nova-staging.service.example` (port `8012`,
`EnvironmentFile=/etc/nova-staging/nova-staging.env`, working dir
`/opt/nova-staging/current/backend`). Keep its `ExecStartPre` isolation guard.
Restart only the staging service; never touch production services.

Frontend: build with Node from the repo (`.github` workflows use Node 24) and
point it at the staging backend:

```bash
cd frontend && npm ci
VITE_BACKEND_URL=<staging backend origin> npm run build   # serve dist/ on the staging domain
```

Health:

```bash
curl -fsS http://127.0.0.1:8012/api/health
```

## 6. Pre-credential web acceptance (Claude key still absent)

Log in on the staging site. Verify:

- admin (`w2f.lovnish@gmail.com`) can open the Pine conversion page
- a normal user is 403 / cannot reach it
- source-submission + manual-fallback forms render
- C2 controls appear only for approved candidates; My Strategies loads
- Live eligibility is unavailable
- with `ANTHROPIC_API_KEY` empty, submitting falls back to the manual path — **no HTTP 500**

Only when all pass are you `C1_C2_STAGING_DEPLOYED_AWAITING_USER_SMOKE`.

## 7. Add the Anthropic key (your manual step)

Add `ANTHROPIC_API_KEY=...` to `/etc/nova-staging/nova-staging.env` directly on
the host. Do not paste it into chat or Git. Restart only the staging backend.
Confirm presence without printing it:

```bash
grep -q '^ANTHROPIC_API_KEY=.\+' /etc/nova-staging/nova-staging.env && echo "ANTHROPIC_API_KEY: configured" || echo "ANTHROPIC_API_KEY: missing"
```

## 8. Real smoke (web UI)

1. Admin submits one representative Pine on the web → analyzer runs → one Claude
   call → structured candidate → source SHA matches → NOVA appends Transport V2
   (server-owned) → deterministic validation → `READY_FOR_ADMIN_REVIEW`.
   Verify provider mode = CLAUDE_API, model from config, token counts + latency
   recorded, repair count ≤ 1, source/candidate diff visible.
2. Admin reviews the converted Pine (logic/timeframe/`request.security` preserved,
   no invented filter/transport) and **approves** → `APPROVED_FOR_TRADINGVIEW_COMPILE`.
   Approval must not start the engine, create orders, or grant Live.
3. Copy the exact approved candidate → paste unmodified into TradingView Pine
   Editor → compile → record compile success via the C2 admin UI.
4. Create a **SELF** installation → generate the private credential (shown once;
   only its hash is stored; keep it out of the URL, in the JSON body).
5. Create a TradingView alert to the staging private-webhook URL with the
   credential in the JSON body → send one **HOLD**.

## 9. Verify HOLD → Paper Ready, zero execution

- private webhook resolves the correct owner/instance; HOLD evidence stored;
  `paper_eligible=true`; `live_eligible=false`; `hold_status=VERIFIED`
- owner sees Paper Ready in their engine selector only; other users cannot;
  selecting it does not start the engine
- Prove zero execution on the staging DB:

```sql
SELECT count(*) FROM strategy_execution_jobs;      -- 0 for this instance
SELECT count(*) FROM live_order_intents;            -- 0
SELECT count(*) FROM strategy_instance_positions;   -- 0
```

Then you are `C1_C2_REAL_STAGING_SMOKE_COMPLETED`.

## 10. Post-smoke safety

Keep Live eligibility false, real-orders disabled, normal-user Claude disabled,
low Claude quotas, and the C2 kill switch. Do not deploy to production. Do not
rotate the working smoke credential unless necessary.

Rollback: stop the staging service and (if needed) restore the pre-deploy
release symlink; do not downgrade the staging database.
