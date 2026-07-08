# Phase 2F-3 UI Lifecycle Smoke Verification

Phase 2F-3 verifies the actual Strategy Catalog lifecycle controls in a local
browser against a fresh isolated backend database and isolated runtime folders.
This phase adds no backend behavior and no new mutation surface.

## Environment

Backend flags:

```text
DEBUG_ENABLED=true
MULTI_STRATEGY_MODEL=true
STRATEGY_INSTANCE_MUTATION_DEBUG=true
MULTI_STRATEGY_FANOUT=true
V2_PAPER_RUNNER_DEBUG=true
ENABLE_LIVE_ORDERS=false
DHAN_MODE=MOCK
AUTH_REQUIRED=false
BACKGROUND_WORKER_RUNNER_ENABLED=false
STRATEGY_JOB_WORKER_ENABLED=false
```

Frontend flags:

```text
VITE_ENABLE_STRATEGY_CATALOG_STATUS=true
VITE_ENABLE_STRATEGY_INSTANCE_MUTATION=true
VITE_ENABLE_V2_PAPER_STATUS=true
```

Fresh isolated resources:

```text
DATABASE_URL=sqlite:///<temp>/phase2f3.sqlite3
RUNTIME_STATE_DIR=<temp>/runtime_state
RUNTIME_LOG_DIR=<temp>/runtime_logs
LAYMAN_ENV_FILE=<temp>/empty.env
```

## Commands

From `backend/`:

```bash
python -m scripts.init_db
python -m scripts.backfill_multistrategy
python -m uvicorn app.main:app --host 127.0.0.1 --port 8137
```

From `frontend/`:

```bash
VITE_BACKEND_PORT=8137 \
VITE_ENABLE_STRATEGY_CATALOG_STATUS=true \
VITE_ENABLE_STRATEGY_INSTANCE_MUTATION=true \
VITE_ENABLE_V2_PAPER_STATUS=true \
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5177
```

Browser:

```text
http://127.0.0.1:5177/#strategy-catalog-status
```

## Manual Browser Steps

1. Open Strategy Catalog.
2. Click `Create paper instance`.
3. Confirm the paper-only prompt.
4. Verify the new instance appears as `paper_live_data` and `paused`.
5. Run planner dry-run directly against the isolated DB and confirm the paused instance is skipped.
6. Click `Resume`.
7. Confirm the paper-only prompt.
8. Verify the instance appears `active`.
9. Run planner dry-run and confirm it is eligible for `paper_live_data` planning only.
10. Click `Pause`.
11. Confirm the paper-only prompt.
12. Verify the instance appears `paused` again.
13. Run planner dry-run and confirm it is skipped again.
14. Open V2 Paper Status and confirm it has only read-only controls.
15. Open Trading and confirm the legacy UI still renders normally.

## Expected Network Calls

Allowed lifecycle POST calls:

```text
POST /api/debug/v2/instances
POST /api/debug/v2/instances/{id}/resume
POST /api/debug/v2/instances/{id}/pause
```

Allowed read-only internal calls:

```text
GET /api/strategies/catalog
GET /api/strategies/instances
GET /api/debug/v2/paper-fanout/status
GET /api/debug/v2/paper-fanout/jobs
GET /api/debug/v2/paper-fanout/instances
```

Forbidden calls:

```text
POST /api/debug/v2/paper-fanout/run-once
POST /api/debug/v2/paper-fanout/process-ready
any /api/debug/dhan path
any /api/orders path
any /api/broker path
any /api/control path
any /api/engine path
any /api/webhook or /webhook path
any /api/strategies/subscribe path
```

## Observed Result

```text
UI create: paper_live_data, paused
UI resume: paper_live_data, active
UI pause: paper_live_data, paused
Paused planner check: planned_count=0, skip=PAUSED_INSTANCE
Resumed planner check: planned_count=1, execution_mode=paper_live_data, jobs_created=0
Paused-again planner check: planned_count=0, skip=PAUSED_INSTANCE
Signals created: 0
Normalized signals created: 0
Execution jobs created: 0
Webhook credentials created: 0
Forbidden execution buttons visible: none
V2 Paper panel buttons: Refresh only
Forbidden network calls: none
Legacy Trading view: opened
```

## Result

Phase 2F-3 UI lifecycle smoke is verified.

Do not proceed directly to execution, webhook, live, worker, scheduler, or Dhan
implementation. The next step should be a separate design review.
