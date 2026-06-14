# Stage 6C — Real VPS Deployment Validation

Operator guide for validating the real deployment before any live order. The
objective is **dry-run validation**. A real order pilot is allowed only after
every validation below passes and a manual final approval is given.

```
Hostinger main app + live-pilot worker
Neon PostgreSQL central database
Executor 001 = 64.225.87.19   (Dhan Account 1 whitelist = 64.225.87.19)
Executor 002 = 152.42.157.165 (Dhan Account 2 whitelist = 152.42.157.165)
```

All scripts live in `deploy/pilot/`. They never print secrets, print explicit
PASS/FAIL, and exit non-zero on any unsafe state.

## Validation scripts

| Script | Validates |
|---|---|
| `validate_hostinger_main.sh` | API service active; paper + live-pilot workers active; `/api/readiness` ready; `live_policy` disabled; migrations current; dry-run flags safe. |
| `validate_neon_database.sh` | Neon reachable; Alembic at head; `alembic check` clean. No connection string printed. |
| `validate_executors.sh` | Both executors `/health` (code match) and `/egress-ip` == expected Reserved IP. |
| `validate_dry_run_signal.sh` | Safe flags; posts one relay alert (token never printed); confirms two dry-run jobs and **no** real order. |
| `disable_live_everywhere.sh` | Emergency: forces live off on Hostinger + real orders off on both executors. |
| `pilot_go_no_go_check.sh` | Aggregates all checks into a single GO / NO-GO; fails if dry-run flag is off or any executor IP mismatches. |

## Exact commands

Checking the Hostinger service:

```bash
systemctl is-active layman-nova-signal-router.service
curl -fsS https://layman-api.manyacare.com/api/readiness
```

Checking the live-pilot worker:

```bash
systemctl is-active layman-live-worker.service
journalctl -u layman-live-worker.service --since "10 min ago" --no-pager
```

Checking the Neon migration:

```bash
cd /opt/layman-nova-signal-router/backend
.venv/bin/alembic upgrade head
.venv/bin/alembic check
bash deploy/pilot/validate_neon_database.sh
```

Checking executor health:

```bash
EXECUTOR_001_URL=https://exec-001.example.com \
EXECUTOR_002_URL=https://exec-002.example.com \
  bash deploy/pilot/validate_executors.sh
# Or on each droplet directly:
bash deploy/executor/check_executor_health.sh
```

Checking executor egress IP:

```bash
# On each droplet (independent of the service):
bash deploy/executor/check_reserved_ip_route.sh           # EXECUTOR_001 -> 64.225.87.19
EXECUTOR_RESERVED_IP=152.42.157.165 bash deploy/executor/check_reserved_ip_route.sh
```

Testing a dry-run signal:

```bash
RELAY_TOKEN=*** bash deploy/pilot/validate_dry_run_signal.sh
```

Run the full gate:

```bash
EXECUTOR_001_URL=https://exec-001.example.com \
EXECUTOR_002_URL=https://exec-002.example.com \
RELAY_TOKEN=*** \
  bash deploy/pilot/pilot_go_no_go_check.sh
```

Enabling the real pilot (only after GO + manual approval): see
`docs/FIRST_2_ACCOUNT_LIVE_PILOT_CHECKLIST.md`.

Disabling the real pilot (emergency or after the test):

```bash
sudo bash deploy/pilot/disable_live_everywhere.sh
```

Checking Dhan orders: open the Dhan web console for both accounts and confirm the
order book matches (during dry-run there must be zero new orders).

Checking Nova audit logs:

```bash
sudo journalctl -u layman-nova-signal-router.service --since "30 min ago" --no-pager
# live order jobs (per user) via the authenticated API:
curl -fsS https://layman-api.manyacare.com/api/me/live-order-jobs -b cookies.txt
```

## Pass criteria (dry-run gate)

All of these must be true before a real pilot is even considered:

```
Hostinger readiness OK              Executor 001 egress == 64.225.87.19
Neon DB migration head OK           Executor 002 egress == 152.42.157.165
paper worker healthy                Dhan Account 1 whitelisted 64.225.87.19
live pilot worker healthy           Dhan Account 2 whitelisted 152.42.157.165
executor 001 health OK              User 1 -> EXECUTOR_001, User 2 -> EXECUTOR_002
executor 002 health OK              Both approved for SUPERTREND_FLIP only
LIVE_ORDER_DRY_RUN_ONLY=true        Max trades/day = 1, tiny quantity only
ENABLE_LIVE_ORDERS=false            EXECUTOR_REAL_ORDERS_ENABLED=false on both
TradingView dry-run alert creates 2 dry-run jobs; no Dhan order placed
```
