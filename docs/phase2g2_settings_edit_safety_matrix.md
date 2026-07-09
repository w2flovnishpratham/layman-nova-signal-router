# Phase 2G-2 Settings Edit Safety Matrix

Phase 2G-2 verifies the Phase 2G-1 paused settings edit surface without adding
new app behavior. The matrix is an ops/test-only safety checkpoint for:

- `POST /api/debug/v2/instances/{instance_id}/settings`
- paused-only updates to `instance_label`, `lots`, and `side_preference`
- no execution, live, Dhan, webhook, worker, scheduler, or startup changes

## Command

From `backend/`:

```bash
python -m scripts.manual_v2_instance_settings_matrix
```

By default the script creates:

- a fresh temporary SQLite DB
- fresh temporary runtime state/log folders
- `ENABLE_LIVE_ORDERS=false`
- debug lifecycle/settings flags only

It does not use the configured staging DB unless `--allow-external-db` is
explicitly provided.

## Verified Scenarios

The matrix verifies:

- paused settings edit succeeds
- active settings edit returns `409`
- pause then edit succeeds
- planner skips while paused and observes the edited side/lots after resume
- `real_orders` settings edit returns `403`
- cross-user edit returns `404`
- smuggled fields are rejected
- no-op edit returns `changed=false`
- frontend settings controls stay behind existing flags
- Strategy Catalog panel does not reference runner endpoints
- V2 Paper panel remains read-only

Execution guards assert no calls to:

- `route_signal`
- v2 worker route adapter
- Dhan client placement methods
- PaperBroker placement methods

## Expected Summary

Successful output contains a sanitized JSON summary with:

```text
overall_ok: true
fresh_isolated_db: true
runtime_isolated: true
paused_settings_edit: passed
active_edit_blocked: passed
pause_then_edit: passed
planner_observes_after_resume: passed
real_orders_protected: passed
cross_user_isolation: passed
smuggled_fields_rejected: passed
noop_edit_safe: passed
frontend_static_safety: passed
execution_guard_calls: route_signal=0, dhan=0, paper_broker=0
sensitive_field_count: 0
enable_live_orders: false
```

## Remaining Risks

This phase does not prove a future archive/delete flow, public webhook setup,
paper job processing, live execution, or Dhan placement. Those remain out of
scope and require a separate design review before implementation.

## Recommendation

GO for the next design review only. The next safe feature candidate is
soft-disable/archive or read-only instance history/details, not execution.
