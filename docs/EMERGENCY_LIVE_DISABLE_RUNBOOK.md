# Emergency Live Disable Runbook

Use this when live trading must be stopped **now** — a bad order, broker
anomaly, unexpected routing, or any doubt. The goal is to make real orders
impossible across the whole system as fast as possible, then reconcile.

```
Hostinger main app + live-pilot worker
Executor 001 = 64.225.87.19
Executor 002 = 152.42.157.165
```

## Fastest stop (one command, on Hostinger as root)

```bash
sudo bash deploy/pilot/disable_live_everywhere.sh
```

This sets `ENABLE_LIVE_ORDERS=false` and `LIVE_ORDER_DRY_RUN_ONLY=true` in
`/etc/layman/layman.env`, restarts the API and live-pilot worker, and disables
real orders on both executors (over SSH if `EXECUTOR_001_SSH` / `EXECUTOR_002_SSH`
are set, otherwise it prints the exact per-droplet commands).

To disable executors remotely in the same run:

```bash
EXECUTOR_001_SSH=root@64.225.87.19 \
EXECUTOR_002_SSH=root@152.42.157.165 \
  sudo bash deploy/pilot/disable_live_everywhere.sh
```

## Immediate kill switch (even faster, blocks before any broker call)

In the Nova admin UI (or runtime settings) set `global_kill_switch=true` (or
`emergency_stop=true`). Every in-flight and new live job blocks with
`kill_switch_active` before contacting any executor or Dhan.

## Manual fallback (if the script or admin UI is unavailable)

Hostinger `/etc/layman/layman.env`:

```env
ENABLE_LIVE_ORDERS=false
LIVE_ORDER_DRY_RUN_ONLY=true
```

```bash
sudo systemctl restart layman-nova-signal-router.service
sudo systemctl restart layman-live-worker.service
```

On EACH executor droplet `/etc/layman-executor/executor.env`:

```env
EXECUTOR_REAL_ORDERS_ENABLED=false
```

```bash
sudo systemctl restart layman-executor.service
```

With `EXECUTOR_REAL_ORDERS_ENABLED=false`, each executor refuses every real
order with HTTP 403, so even a stray signed request cannot place a trade.

## Verify the stop

```bash
bash deploy/pilot/validate_hostinger_main.sh        # live_policy must be disabled
curl -fsS https://layman-api.manyacare.com/api/readiness   # status ready, live_policy disabled
bash deploy/executor/check_executor_health.sh       # on each droplet
```

- [ ] Readiness shows `live_policy: disabled`.
- [ ] Both executors restarted with real orders disabled.
- [ ] A dry-run alert now routes as `dry_run_verified` (no real order).

## Reconcile

- Open the Dhan web console for **both** accounts.
- Cancel or square off any unintended open order or position manually.
- Check `live_order_jobs` for any `sent`/`confirmed` rows around the incident and
  match them to the Dhan order book.
- Inspect logs: `sudo journalctl -u layman-nova-signal-router.service --since "30 min ago" --no-pager`
  (logs never contain access tokens).
- If data integrity is in doubt, restore Neon from the latest `pg_dump`.

## After the incident

- Leave the system in the safe default (live disabled, dry-run only).
- Do not re-enable real orders until the cause is understood and the Stage 6C
  GO/NO-GO gate is GO again.
- Write a short incident note: trigger, actions taken, broker outcome, follow-ups.
