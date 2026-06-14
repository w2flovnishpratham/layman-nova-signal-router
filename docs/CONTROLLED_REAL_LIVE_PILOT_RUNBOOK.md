# Controlled Real Live Pilot Runbook

Scope: a single, manually supervised, tiny-quantity live pilot for **two**
accounts, each on its own executor droplet. This is **not** a public live launch.
Default state is dry-run; real orders are blocked unless every flag and check
below is deliberately enabled, and are disabled again immediately after the test.

```
Hostinger main app + live-pilot worker
Neon PostgreSQL central database
EXECUTOR_001 = 64.225.87.19   (Dhan Account 1 whitelist = 64.225.87.19)
EXECUTOR_002 = 152.42.157.165 (Dhan Account 2 whitelist = 152.42.157.165)
```

Keep open during the whole pilot: this runbook, the Nova admin live-pilot queue,
the Dhan web console for **both** accounts, and a terminal on each executor.

---

## A. Pre-market checks (all must pass before enabling any real flag)

1. **Neon backup** — confirm last automated `pg_dump` succeeded (`/var/backups/layman/postgres`), or take a fresh one.
2. **Alembic** — on Hostinger: `python -m alembic upgrade head` then `python -m alembic check` (expect "No new upgrade operations detected.").
3. **Hostinger readiness** — `GET /api/readiness` returns ready; service active.
4. **Paper worker healthy** — `systemctl is-active layman-paper-worker.service`.
5. **Live-pilot worker healthy** — the dedicated live worker process is running (`python -m app.services.live_worker_runtime`).
6. **Executor 001 health** — `bash deploy/executor/check_executor_health.sh` on droplet 001 → PASS, code `EXECUTOR_001`.
7. **Executor 002 health** — same on droplet 002 → PASS, code `EXECUTOR_002`.
8. **Executor 001 egress** — `check_executor_egress.sh` and `check_reserved_ip_route.sh` → `64.225.87.19`.
9. **Executor 002 egress** — same → `152.42.157.165`.
10. **Dhan Account 1 whitelist** — `64.225.87.19` is whitelisted (and the 7-day lock is satisfied).
11. **Dhan Account 2 whitelist** — `152.42.157.165` is whitelisted.
12. **User 1 → EXECUTOR_001** — assignment exists and is `verified` in the admin UI.
13. **User 2 → EXECUTOR_002** — assignment exists and is `verified`.
14. **Both users approved only for `SUPERTREND_FLIP`** — admin approval active, not expired.
15. **Max trades per day = 1** — set on each user's live subscription and approval cap.
16. **Tiny quantity** — set the smallest tradable lot on each subscription.
17. **Run a dry-run alert** — post one `SUPERTREND_FLIP` signal through the relay.
18. **Confirm two dry-run jobs route correctly** — one job to EXECUTOR_001, one to EXECUTOR_002, each reporting its expected egress IP; status `dry_run_verified`.
19. **Test the global kill switch** — toggle `global_kill_switch` on, confirm a dry-run signal is blocked with `kill_switch_active`, then toggle off.
20. **Only now** proceed to enable real flags.

If any step fails, stop. Do not enable real orders.

---

## B. Real pilot flags

Hostinger `/etc/layman/layman.env`:

```env
RELAY_ENABLED=true
LIVE_PILOT_ENABLED=true
LIVE_ORDER_DRY_RUN_ONLY=false
ENABLE_LIVE_ORDERS=true
EXECUTION_NODE_ROUTING_ENABLED=true
ENABLE_LIVE_PILOT_WORKERS=true
```

Executor 001 `/etc/layman-executor/executor.env`:

```env
EXECUTOR_REAL_ORDERS_ENABLED=true
```

Executor 002 `/etc/layman-executor/executor.env`:

```env
EXECUTOR_REAL_ORDERS_ENABLED=true
```

Restart the affected services after editing env files:

```bash
# Hostinger
sudo systemctl restart layman-nova-signal-router.service
sudo systemctl restart layman-live-worker.service   # live-pilot worker
# Each executor droplet
sudo systemctl restart layman-executor.service
```

---

## C. Real pilot rules

- One Supertrend signal only.
- One order maximum per account (max trades per day = 1).
- Tiny quantity only.
- Manual monitoring open the entire time.
- Dhan console open for both accounts.
- Nova admin queue open.
- Emergency kill switch ready (one click).
- After the first order on each account, **disable real flags immediately** (Section D).

Place exactly one `SUPERTREND_FLIP` signal. Watch:
- the live-pilot queue: each user gets one `live_order_job`, routed to its own executor;
- each executor logs a single signed request from the Hostinger IP and an egress IP equal to its reserved IP;
- the Dhan console for each account shows the single order;
- `live_order_jobs.dhan_order_id` is populated; status `sent` or `confirmed`.

If anything looks wrong: hit the global kill switch, then proceed to shutdown.

---

## D. Shutdown / rollback after the test

Set back on Hostinger:

```env
ENABLE_LIVE_ORDERS=false
LIVE_ORDER_DRY_RUN_ONLY=true
```

Set back on **both** executors:

```env
EXECUTOR_REAL_ORDERS_ENABLED=false
```

Then restart and verify live is disabled:

```bash
# Hostinger
sudo systemctl restart layman-nova-signal-router.service
sudo systemctl restart layman-live-worker.service
# Verify readiness reports live disabled / dry-run only
curl -s https://layman-api.manyacare.com/api/readiness

# Each executor
sudo systemctl restart layman-executor.service
bash deploy/executor/check_executor_health.sh
```

Confirm: `ENABLE_LIVE_ORDERS=false`, `LIVE_ORDER_DRY_RUN_ONLY=true`, and both
executors report real orders disabled. A dry-run signal after rollback must route
as `dry_run_verified` (never a real order).

---

## E. Kill-switch runbook (any time)

1. In the admin UI (or runtime settings) set `global_kill_switch=true` (or `emergency_stop=true`).
2. All in-flight and new live jobs block with `kill_switch_active` before any executor/Dhan call.
3. If the kill switch is unavailable, set `ENABLE_LIVE_ORDERS=false` and restart the Hostinger service and live worker.
4. As a last resort, set `EXECUTOR_REAL_ORDERS_ENABLED=false` on both executors and restart them; the executor then refuses every real order with HTTP 403.
5. Manually verify/cancel any open order directly in the Dhan console for each account.

---

## F. Emergency rollback (deploy issue)

- Stop the live worker: `sudo systemctl stop layman-live-worker.service`.
- Set `ENABLE_LIVE_ORDERS=false`, `LIVE_ORDER_DRY_RUN_ONLY=true` on Hostinger; restart the API.
- Set `EXECUTOR_REAL_ORDERS_ENABLED=false` on both executors; restart them.
- Restore the database from the latest Neon `pg_dump` only if data integrity is in question.
- Reconcile open positions manually in the Dhan console for both accounts.

---

## G. Disable flags after the test — required

This pilot is complete only after Section D is done and verified. Do not leave
`ENABLE_LIVE_ORDERS=true` or `EXECUTOR_REAL_ORDERS_ENABLED=true` running
unattended. The default resting state of the system is dry-run with real orders
disabled.
