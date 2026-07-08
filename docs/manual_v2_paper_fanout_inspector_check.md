# Manual V2 Paper Fanout Inspector Check

This is a read-only smoke check for the Phase 2E-3 inspector. It verifies that the internal debug inspector can read v2 paper fanout status, jobs, and instances after a local or staging paper run without exposing sensitive fields or mutating state.

It does not create signals, does not process jobs, does not route orders, and does not call Dhan or PaperBroker.

## Required Flags

Set these in the shell that runs the check:

```powershell
$env:DEBUG_ENABLED = "true"
$env:MULTI_STRATEGY_FANOUT = "true"
$env:V2_PAPER_RUNNER_DEBUG = "true"
$env:DATABASE_URL = "<local-or-staging-paper-db-url>"
```

The script refuses to run if any required flag is off or `DATABASE_URL` is missing. The debug flag is required even in direct-service mode because the check is validating the same gated surface that backs the debug endpoints.

## When To Run

Run this after the Phase 2E-2R manual ENTRY to EXIT verification has succeeded:

```text
entry_position_opened: true
exit_position_cleared: true
legacy_position_untouched: true
analytics_pairing.paired_count: 1
entry_result.ok: true
exit_result.ok: true
```

That previous run should leave v2 paper jobs and strategy instance rows in the database for the inspector to report.

## Direct Service Mode

From `backend/`:

```powershell
python -m scripts.manual_v2_paper_fanout_inspector_check --direct-service
```

This opens a database session and calls only:

```text
get_v2_paper_fanout_status
list_v2_paper_fanout_jobs
list_v2_paper_strategy_instances
```

It does not call the runner, queue processors, `route_signal`, Dhan client, or PaperBroker.

## Server Mode

If the backend server is already running with the required debug flags:

```powershell
python -m scripts.manual_v2_paper_fanout_inspector_check --server-url http://localhost:8001
```

Equivalent curl checks:

```bash
curl "http://localhost:8001/api/debug/v2/paper-fanout/status?limit=20"
curl "http://localhost:8001/api/debug/v2/paper-fanout/jobs?limit=20"
curl "http://localhost:8001/api/debug/v2/paper-fanout/instances?limit=20"
```

Do not expose these debug endpoints publicly.

## What The Script Verifies

The script checks:

- Status, jobs, and instances are readable.
- Output contains no forbidden sensitive keys such as secrets, raw payloads, signal payloads, headers, proxy URLs, access tokens, or credential hashes.
- Jobs include only curated safe fields.
- Instances include only curated safe fields.
- `open_position_summary` includes only safe position fields.
- Database row counts and tracked row signatures are unchanged before and after the read.
- Runtime state and JSONL log file checksums are unchanged before and after the read.

## Expected Output

Success prints one sanitized JSON event:

```json
{"event":"success_summary","data":{"ok":true,"mode":"direct_service","safety":{"sensitive_field_count":0,"db_unchanged":true,"runtime_files_unchanged":true}}}
```

Useful success signals:

```text
readable.status: true
readable.jobs: true
readable.instances: true
safety.sensitive_field_count: 0
safety.jobs_safe_fields_only: true
safety.instances_safe_fields_only: true
safety.db_unchanged: true
safety.runtime_files_unchanged: true
```

If a previous Phase 2E-2R paper run exists, expect non-zero paper instance and v2 paper job counts. Empty-state output is also valid on a clean database.

## Confirming No Secrets

The printed summary should not contain keys or values for:

```text
secret
raw_payload
signal_payload
access_token
headers
raw_response
request
response
proxy_url
credential hashes
```

The script fails closed if the raw inspector output contains any forbidden key. Failure output is sanitized and reports only a generic failure message plus a count.

## Confirming No Mutation

The script snapshots before and after:

- Counts for strategy catalog/version, user strategy instances, strategy signals, normalized signals, v2 jobs, portfolio trades, and portfolio snapshots.
- Signatures for v2 job state, strategy instance state, and strategy signal state.
- Checksums for runtime state files.
- Checksums for JSONL log files.

The smoke check passes only when all snapshots match.

## What Not To Do

- Do not run this as a webhook.
- Do not add it to startup, scheduler, cron, or worker loops.
- Do not use it to process pending jobs.
- Do not call `run_v2_paper_fanout_once`.
- Do not call `process_ready_v2_paper_jobs_once`.
- Do not call `route_signal`.
- Do not call Dhan client or PaperBroker.
- Do not run against a public debug surface.
- Do not use this as a substitute for the Phase 2E-2R ENTRY to EXIT verification.
