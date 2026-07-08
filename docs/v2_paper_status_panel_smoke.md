# V2 Paper Status Panel Smoke

This is an internal/local verification checklist for the read-only V2 Paper Status panel.

The frontend visibility flag is not a security boundary. The backend debug gates remain the real protection:

```text
DEBUG_ENABLED=true
MULTI_STRATEGY_FANOUT=true
V2_PAPER_RUNNER_DEBUG=true
```

Keep live orders disabled for this smoke:

```text
ENABLE_LIVE_ORDERS=false
```

## Frontend Flag

The panel is hidden/disabled by default.

```text
VITE_ENABLE_V2_PAPER_STATUS=false
```

Enable it only for an internal dev/staging build:

```text
VITE_ENABLE_V2_PAPER_STATUS=true
```

When the flag is off:

- The V2 Paper header tab is hidden.
- The V2 Paper menu item is hidden.
- `/#v2-paper-status` shows an internal-disabled message.
- Inspector endpoints are not loaded by the panel.

When the flag is on for an admin/dev user:

- The V2 Paper header tab is visible.
- The V2 Paper menu item is visible.
- `/#v2-paper-status` loads the read-only inspector panel.

## Repeated Backend Smoke

From `backend/`, run:

```powershell
$env:DEBUG_ENABLED = "true"
$env:MULTI_STRATEGY_FANOUT = "true"
$env:V2_PAPER_RUNNER_DEBUG = "true"
$env:ENABLE_LIVE_ORDERS = "false"

python -m scripts.manual_v2_paper_repeated_smoke --iterations 3
```

Expected:

```text
event: repeated_v2_paper_smoke_summary
ok: true
iterations_requested: 3
successful_iterations: 3
fresh_isolated_db_all: true
runtime_isolated_all: true
```

The helper wraps `manual_v2_paper_scenario_matrix` and uses isolated temp SQLite/runtime state for each iteration.

## UI Smoke

1. Start the backend with an isolated DB/runtime and safe flags:

   ```powershell
   $env:DEBUG_ENABLED = "true"
   $env:MULTI_STRATEGY_FANOUT = "true"
   $env:V2_PAPER_RUNNER_DEBUG = "true"
   $env:ENABLE_LIVE_ORDERS = "false"
   $env:DATABASE_URL = "sqlite:///<temp path>/v2-paper-panel.sqlite3"
   ```

2. Run the scenario matrix or repeated smoke:

   ```powershell
   cd backend
   python -m scripts.manual_v2_paper_scenario_matrix
   python -m scripts.manual_v2_paper_repeated_smoke --iterations 3
   ```

3. Start the frontend with the panel enabled:

   ```powershell
   $env:VITE_ENABLE_V2_PAPER_STATUS = "true"
   npm --prefix frontend run dev
   ```

4. Open:

   ```text
   /#v2-paper-status
   ```

5. Confirm the panel shows:

   ```text
   flags/status
   safe counts
   recent V2 paper jobs
   paper strategy instances
   safe open position summaries
   recent failures
   ```

6. Confirm browser/network requests from this panel are only:

   ```text
   GET /api/debug/v2/paper-fanout/status?limit=20
   GET /api/debug/v2/paper-fanout/jobs?limit=20
   GET /api/debug/v2/paper-fanout/instances?limit=20
   ```

7. Confirm no forbidden fields appear:

   ```text
   secret
   raw_payload
   signal_payload
   access_token
   headers
   proxy_url
   client id
   credential hashes
   full Dhan response
   ```

8. Repeat the backend smoke plus UI load three times before treating the internal panel as verified.

## Prohibited

Do not add or expose controls for:

- run fanout
- process jobs
- retry jobs
- square off
- clear position
- enable live
- start worker
- create webhook
- create strategy

The only allowed panel action is Refresh.
