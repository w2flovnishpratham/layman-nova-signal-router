# Hostinger Live-Pilot Deployment Notes (Stage 6B)

These notes cover the main Nova app on the Hostinger VPS for the controlled
two-account live pilot. They complement `deploy/executor/README.md` (the
executor droplets) and `docs/CONTROLLED_REAL_LIVE_PILOT_RUNBOOK.md` (the pilot
sequence). Live orders stay disabled by default.

## 1. Executor shared secrets

The main app authenticates to each executor with a per-executor HMAC secret.
Set them as a single JSON map keyed by executor code in `/etc/layman/layman.env`:

```env
EXECUTOR_SHARED_SECRETS_JSON={"EXECUTOR_001":"<secret-001-32+chars>","EXECUTOR_002":"<secret-002-32+chars>"}
```

Each value must exactly equal the `EXECUTOR_SHARED_SECRET` set on the matching
droplet. Generate independent secrets (`openssl rand -hex 32`) — one for the
relay, one per executor. Never reuse `SESSION_TOKEN_SECRET` or `RELAY_SHARED_SECRET`.

After editing, restart: `sudo systemctl restart layman-nova-signal-router.service`.

## 2. Register executor URLs

Use the admin live-pilot API/UI (admin login required) to register each executor:

```
POST /api/admin/executors
{
  "executor_code": "EXECUTOR_001",
  "provider": "digitalocean",
  "droplet_name": "nova-exec-user-001",
  "region": "blr1",
  "reserved_ip": "64.225.87.19",
  "health_url":   "https://exec-001.example.com/health",
  "execute_url":  "https://exec-001.example.com/execute-order",
  "egress_ip_url":"https://exec-001.example.com/egress-ip"
}
```

Repeat for `EXECUTOR_002` / `152.42.157.165`. Production executor URLs must be HTTPS.

## 3. Verify executor health and egress

From the admin UI/API, run health then egress verification for each node:

```
POST /api/admin/executors/{id}/verify-health
POST /api/admin/executors/{id}/verify-egress
```

A node only becomes `active` when `/health` returns its code and `/egress-ip`
exactly equals its reserved IP. Then assign and confirm:

```
POST /api/admin/executors/{id}/assign        { "user_id": "<user1>" }
# after the Dhan account whitelists the reserved IP:
POST /api/admin/executors/assignments/{id}/verify
```

One user maps to exactly one executor and vice versa.

## 4. Start the live-pilot worker

The live-pilot worker is separate from the paper worker and the web process:

```bash
ENABLE_LIVE_PILOT_WORKERS=true   # in /etc/layman/layman.env
python -m app.services.live_worker_runtime   # via its systemd unit
```

It reloads each job, re-checks every gate (relay verification, kill switch,
market hours, option side, quantity, daily trades, daily loss, executor code,
reserved IP) and signs the executor request. The web process never calls Dhan.

## 5. Run a dry-run

With `LIVE_ORDER_DRY_RUN_ONLY=true` (default), post one `SUPERTREND_FLIP` alert:

```
POST /relay/tradingview/SUPERTREND_FLIP
Authorization: Bearer <RELAY_SHARED_SECRET>
```

Confirm two `live_order_job`s are created, each routed to its own executor and
returning `dry_run_verified` with the expected egress IP. No Dhan call happens.

## 6. Enable / disable the real pilot

Enabling real orders is a deliberate, supervised step. Follow
`docs/CONTROLLED_REAL_LIVE_PILOT_RUNBOOK.md` exactly. In short:

Enable (Hostinger):

```env
RELAY_ENABLED=true
LIVE_PILOT_ENABLED=true
LIVE_ORDER_DRY_RUN_ONLY=false
ENABLE_LIVE_ORDERS=true
EXECUTION_NODE_ROUTING_ENABLED=true
ENABLE_LIVE_PILOT_WORKERS=true
```

Enable (each executor): `EXECUTOR_REAL_ORDERS_ENABLED=true`.

Disable after the test (Hostinger): `ENABLE_LIVE_ORDERS=false`,
`LIVE_ORDER_DRY_RUN_ONLY=true`. Each executor: `EXECUTOR_REAL_ORDERS_ENABLED=false`.
Restart services and confirm readiness reports live disabled.

The main app also refuses to **boot** in production with `ENABLE_LIVE_ORDERS=true`
unless the relay, executor secrets, verified assignments, and at least one current
`SUPERTREND_FLIP` approval all exist — so a half-configured live state cannot run.

## 7. Rollback

- Stop the live worker, set `ENABLE_LIVE_ORDERS=false` + `LIVE_ORDER_DRY_RUN_ONLY=true`, restart the API.
- Set `EXECUTOR_REAL_ORDERS_ENABLED=false` on both executors and restart them.
- Use the global kill switch for an immediate halt (`kill_switch_active` blocks before any executor/Dhan call).
- Restore Neon from the latest `pg_dump` only if data integrity is in doubt; reconcile open orders in the Dhan console.
