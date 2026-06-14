# First 2-Account Live Pilot Checklist

**This is not a public live launch.** It is a single, manually supervised pilot
with two accounts and **one tiny order per account**. Do not proceed unless the
Stage 6C dry-run GO/NO-GO gate returned **GO**.

```
Executor 001 = 64.225.87.19   -> Dhan Account 1 (User 1)
Executor 002 = 152.42.157.165 -> Dhan Account 2 (User 2)
```

## 1. Pre-flight (must all be checked)

- [ ] `bash deploy/pilot/pilot_go_no_go_check.sh` returned **GO** today.
- [ ] Neon backup taken within the last 24h.
- [ ] Hostinger API + paper worker + live-pilot worker active.
- [ ] Executor 001 egress == `64.225.87.19`; Executor 002 egress == `152.42.157.165`.
- [ ] Dhan Account 1 whitelists `64.225.87.19`; Dhan Account 2 whitelists `152.42.157.165` (7-day lock satisfied).
- [ ] User 1 verified to EXECUTOR_001; User 2 verified to EXECUTOR_002.
- [ ] Both users approved for `SUPERTREND_FLIP` only; approval not expired.
- [ ] Max trades/day = 1 and tiny quantity on both subscriptions.
- [ ] A dry-run alert produced exactly two `dry_run_verified` jobs and zero Dhan orders.
- [ ] Global kill switch tested (blocks a dry-run job), then turned off.
- [ ] Dhan console open for both accounts; Nova admin queue open; this checklist open.
- [ ] Manual final approval given by the operator. **One tiny order only.**

## 2. Enable the real pilot (temporary)

Hostinger `/etc/layman/layman.env`:

```env
LIVE_ORDER_DRY_RUN_ONLY=false
ENABLE_LIVE_ORDERS=true
```

Each executor `/etc/layman-executor/executor.env`:

```env
EXECUTOR_REAL_ORDERS_ENABLED=true
```

Restart:

```bash
# Hostinger
sudo systemctl restart layman-nova-signal-router.service
sudo systemctl restart layman-live-worker.service
# Each executor droplet
sudo systemctl restart layman-executor.service
```

## 3. Place exactly one signal

- [ ] Post one `SUPERTREND_FLIP` alert through the relay.
- [ ] Confirm one `live_order_job` per user, each routed to its own executor.
- [ ] Each executor logs a single signed request from the Hostinger IP, egress IP == its reserved IP.
- [ ] Each Dhan account shows exactly one order; `dhan_order_id` populated; status `sent`/`confirmed`.
- [ ] No second order is created (max trades/day = 1).

## 4. Disable immediately after the test

Hostinger:

```env
ENABLE_LIVE_ORDERS=false
LIVE_ORDER_DRY_RUN_ONLY=true
```

Each executor:

```env
EXECUTOR_REAL_ORDERS_ENABLED=false
```

Then:

```bash
sudo bash deploy/pilot/disable_live_everywhere.sh
bash deploy/pilot/validate_hostinger_main.sh   # must show live_policy disabled
```

- [ ] Readiness shows `live_policy: disabled`.
- [ ] Both executors report real orders disabled.
- [ ] A post-rollback dry-run alert routes as `dry_run_verified` (no real order).

## 5. Post-trade audit

- [ ] Dhan order book for both accounts matches the two intended orders only.
- [ ] `live_order_jobs` rows: status `sent`/`confirmed`, correct executor, correct reserved IP in the response.
- [ ] Nova audit/journal logs contain no access tokens or secrets.
- [ ] Realized P&L and positions reconciled against the Dhan console.
- [ ] Live flags confirmed disabled everywhere (Section 4).
- [ ] Write a short pilot note: what happened, any broker latency/partial fill, follow-ups.

## Rules

- One tiny order per account. Disable the real flags immediately after.
- If anything looks wrong: hit the global kill switch, then run
  `sudo bash deploy/pilot/disable_live_everywhere.sh`, then reconcile in the Dhan console.
- This pilot does not authorize public live launch or additional users.
