# Manual V2 Paper Fanout Check

This check verifies one controlled v2 paper fanout cycle from a local or staging shell. It is not a public webhook, not a UI flow, and not a worker loop.

## Required Flags

Set these before a mutating run:

```powershell
$env:DEBUG_ENABLED = "true"
$env:MULTI_STRATEGY_FANOUT = "true"
$env:V2_PAPER_RUNNER_DEBUG = "true"
$env:V2_PAPER_FANOUT_CONFIRM = "paper_only"
$env:DATABASE_URL = "<test-or-staging-db-url>"
```

The script also checks runtime safety:

- `max_jobs` must be between `1` and `5`.
- `ENABLE_LIVE_ORDERS=true` is allowed only when the current runtime engine mode is paper.
- Any active `real_orders` strategy instance for `SUPERTREND_V1` blocks the run.
- More than one active `paper_live_data` instance blocks the run to avoid broad fanout.

## Local Dry-Run

From `backend/`:

```powershell
python -m scripts.manual_v2_paper_fanout_check --dry-run
```

Dry-run validates flags and payload shape, prints planned actions, and does not mutate the database or runtime state files.

## Local Or Staging Mutating Run

From `backend/`:

```powershell
python -m scripts.manual_v2_paper_fanout_check --confirm-paper-only --max-jobs 1
```

The script will:

1. Print feature flags and safety settings.
2. Create or locate the manual test user.
3. Create or locate exactly one `SUPERTREND_V1` `paper_live_data` instance for that user.
4. Build a schema-valid `nova.v1` ENTRY payload.
5. Call `run_v2_paper_fanout_once(...)` directly.
6. Verify the v2 instance-scoped position exists.
7. Build a schema-valid `nova.v1` EXIT payload.
8. Call `run_v2_paper_fanout_once(...)` again.
9. Verify the same instance position is cleared.
10. Verify the legacy global paper position is unchanged.
11. Print a sanitized summary.

The payload uses `source="backend"` with metadata identifying the script because `manual_debug` is not valid under the current `nova.v1` schema.

By default, the script installs an in-process simulated paper LTP shim. This keeps the check independent of real Dhan/shared market-data credentials and market hours while still exercising the runner, paper bridge, execution router, paper broker fill path, and instance-scoped state. To use real shared paper market data instead, add:

```powershell
python -m scripts.manual_v2_paper_fanout_check --confirm-paper-only --max-jobs 1 --use-real-paper-market-data
```

Only use that opt-out when staging shared market-data credentials are configured and market-data access is expected to work.

## Expected Success Output

You should see JSON lines similar to:

```json
{"event":"feature_flags","data":{"MULTI_STRATEGY_FANOUT":true,"V2_PAPER_RUNNER_DEBUG":true}}
{"event":"settings","data":{"debug_enabled":true,"enable_live_orders":false}}
{"event":"success_summary","data":{"entry_position_opened":true,"exit_position_cleared":true,"legacy_position_untouched":true}}
```

Secrets, raw payloads, Dhan headers, raw broker responses, proxy URLs, and access tokens are stripped from printed summaries.

## Cleanup And Rollback

The script clears only the test instance-scoped runtime position in its cleanup step. It does not clear the legacy global paper position.

Database rows created for the test user, catalog/version, signals, jobs, and the test paper instance are intentionally left for audit. To clean up manually, disable or delete only rows associated with:

- user email: `manual-v2-paper-fanout-check@example.invalid`
- instance label: `manual-v2-paper-fanout-check`

Do not delete unrelated strategy instances.

## What Not To Do

- Do not run against a live `real_orders` instance.
- Do not run with `ENABLE_LIVE_ORDERS=true` unless the script confirms paper mode.
- Do not expose `/api/debug/*` publicly.
- Do not use this as a TradingView webhook.
- Do not add this to startup, scheduler, cron, or worker loops.
- Do not use `--use-real-paper-market-data` unless you intentionally want the paper broker to fetch real shared-market LTP.

## Optional Debug Endpoint Smoke Example

If the local server is already running with auth/debug access configured, the existing internal endpoint can be smoke-tested manually:

```bash
curl -X POST http://localhost:8001/api/debug/v2/paper-fanout/run-once \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_code": "SUPERTREND_V1",
    "payload": {
      "version": "nova.v1",
      "secret": "test-only",
      "signal_id": "manual-test-001",
      "strategy_code": "SUPERTREND_V1",
      "action": "ENTRY",
      "intent": "BULLISH",
      "symbol": "NIFTY",
      "instrument_type": "OPTIDX",
      "option_side": "AUTO",
      "strike_mode": "MANUAL",
      "strike": 25000,
      "expiry_mode": "MANUAL",
      "expiry": "2026-07-09",
      "qty_mode": "LOTS",
      "lots": 1,
      "order_type": "MARKET",
      "product_type": "INTRADAY",
      "source": "backend",
      "timestamp": "2026-07-08T09:15:00+05:30",
      "metadata": {"source": "manual_debug"}
    },
    "max_jobs": 1,
    "confirm_paper_only": true
  }'
```

Prefer the CLI script for the full ENTRY/EXIT verification because it checks state before and after the run.
