# Manual V2 Paper Scenario Matrix

This script runs a local, isolated v2 paper fanout scenario matrix. It is a backend-only verification tool, not a webhook, not a worker, and not UI wiring.

By default it creates:

- A fresh temporary SQLite database.
- Fresh temporary runtime state and log directories.
- Seeded multi-strategy catalog/version rows.
- Only the scenario data needed for the run.

It does not use the configured staging database unless explicitly run with `--allow-external-db`.

## Required Flags

Set these before running:

```powershell
$env:DEBUG_ENABLED = "true"
$env:MULTI_STRATEGY_FANOUT = "true"
$env:V2_PAPER_RUNNER_DEBUG = "true"
$env:ENABLE_LIVE_ORDERS = "false"
```

The script refuses to run if these safety flags are not set.

## How To Run

From `backend/`:

```powershell
python -m scripts.manual_v2_paper_scenario_matrix
```

To keep the temporary DB/runtime folder for inspection:

```powershell
python -m scripts.manual_v2_paper_scenario_matrix --keep-temp
```

Avoid `--allow-external-db` unless you are intentionally using a disposable staging DB. Never use a production DB.

## Covered Scenarios

The matrix verifies:

- `bullish_entry_exit`: BULLISH ENTRY opens CE, EXIT clears the same instance.
- `bearish_entry_exit`: BEARISH ENTRY opens PE, EXIT clears the same instance.
- `duplicate_signal`: same `signal_id` does not create duplicate executable work or duplicate paper orders.
- `two_instance_isolation`: two paper instances receive separate jobs and positions; clearing one does not clear the other.
- `real_orders_blocked`: `real_orders` instances are skipped by the paper runner and no real-order v2 job is created.
- `signal_only_not_routed`: `signal_only` instances do not call route execution and do not create paper positions.
- `max_jobs_respected`: when more paper jobs exist than `max_jobs`, only `max_jobs` are processed and the rest stay pending.
- `inspector_safe`: inspector status/jobs/instances remain readable and expose no sensitive keys.

## Expected Output

The script prints a single sanitized JSON event:

```json
{"event":"scenario_matrix_summary","data":{"overall_ok":true,"scenarios":{"bullish_entry_exit":"passed","bearish_entry_exit":"passed","duplicate_signal":"passed","two_instance_isolation":"passed","real_orders_blocked":"passed","signal_only_not_routed":"passed","max_jobs_respected":"passed","inspector_safe":"passed"}}}
```

Useful proof fields:

```text
fresh_isolated_db: true
runtime_isolated: true
sensitive_field_count: 0
inspector_safe.details.db_unchanged: true
inspector_safe.details.runtime_files_unchanged: true
```

## Interpreting Failure

The process exits non-zero if any scenario fails. The summary includes the first failed scenario and a sanitized error message. It should not print secrets, access tokens, raw payloads, signal payloads, Dhan headers, raw broker responses, proxy URLs, or credential hashes.

## Live Dhan Safety

The matrix uses the same simulated paper market-data/security-id shim as the manual v2 paper fanout check. It does not require real Dhan credentials or shared market-data credentials.

It refuses `ENABLE_LIVE_ORDERS=true`.

## Database Safety

Default mode does not touch the configured `DATABASE_URL`; it overrides the process to a fresh temp SQLite DB. This is the preferred mode.

Do not run this against production. If `--allow-external-db` is used, use only a disposable local/staging DB.

## What Not To Do

- Do not expose this as an endpoint.
- Do not add it to startup, scheduler, cron, or worker loops.
- Do not use it with live orders enabled.
- Do not use it as a TradingView webhook.
- Do not run it against production data.
