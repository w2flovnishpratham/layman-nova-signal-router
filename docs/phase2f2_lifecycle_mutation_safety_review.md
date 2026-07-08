# Phase 2F-2 Lifecycle Mutation Safety Review

Phase 2F-2 reviews the Phase 2F-1 paper strategy instance lifecycle surface
before any additional mutation is considered.

Scope reviewed:

- `POST /api/debug/v2/instances`
- `POST /api/debug/v2/instances/{id}/pause`
- `POST /api/debug/v2/instances/{id}/resume`
- frontend catalog lifecycle controls
- feature flags and internal documentation

Out of scope:

- webhook generation
- execution buttons
- public/custom webhook routing
- paper runner controls
- live execution
- `real_orders` processing
- worker, scheduler, or startup wiring
- Dhan live placement
- legacy Supertrend behavior changes

## Review Result

| Question | Result |
| --- | --- |
| Are all lifecycle endpoints debug-gated? | Yes. Router-level `DEBUG_ENABLED` plus lifecycle-specific gates are required. |
| Is `STRATEGY_INSTANCE_MUTATION_DEBUG` default off? | Yes. Unknown, missing, and empty feature flags fail closed to `False`. |
| Is `MULTI_STRATEGY_MODEL` required? | Yes. Lifecycle mutation requires `MULTI_STRATEGY_MODEL=true`. |
| Is `confirm_paper_only` required? | Yes. Create, pause, and resume reject missing or false confirmation. |
| Are created instances always born paused? | Yes. Server-side create hardcodes `status="paused"`. |
| Can client-supplied `execution_mode`, `status`, or `user_id` override server values? | No. Request models use `extra="forbid"` and server values are hardcoded. |
| Can `real_orders` be paused/resumed here? | No. Lifecycle transition rejects `real_orders` with 403. |
| Can lifecycle mutation create signals/jobs/credentials? | No. Create writes only `user_strategy_instances`; pause/resume only update that row. |
| Can lifecycle mutation call `route_signal`? | No. Lifecycle code does not call the runner, worker adapter, or execution router. |
| Can lifecycle mutation call Dhan/PaperBroker? | No. No Dhan or PaperBroker code is reachable from lifecycle endpoints. |
| Can a created paused instance be picked by fanout? | No. The v2 planner skips paused instances with `PAUSED_INSTANCE`. |
| Does resume make it eligible only for paper planning? | Yes. Resume only changes status to active; execution mode stays `paper_live_data`. |
| Does pause make it skipped again? | Yes. After pause, planner skips the instance again. |
| Are audit logs safe and secret-free? | Yes. Audit metadata excludes request bodies, secrets, credentials, and raw payloads. |
| Are frontend mutation controls hidden by default? | Yes. `VITE_ENABLE_STRATEGY_INSTANCE_MUTATION` is a default-off build flag and is combined with the catalog-status flag. |
| Are execution controls accidentally rendered? | No. The catalog panel renders only create, pause, and resume lifecycle controls. |
| Are legacy Supertrend paths untouched? | Yes. Legacy routing and Supertrend execution code are not changed by Phase 2F-1/2. |

## Scenario Matrix

Added:

```bash
cd backend
python -m scripts.manual_v2_instance_lifecycle_matrix
```

Default behavior:

- uses a fresh temporary SQLite database
- uses fresh temporary runtime state/log directories
- does not use the configured staging DB
- forces safe in-process debug flags needed for the matrix
- forces `ENABLE_LIVE_ORDERS=false`
- does not require real Dhan credentials
- prints a sanitized JSON summary
- exits non-zero on scenario failure

Required scenarios covered:

| Scenario | Covered Check |
| --- | --- |
| `create_born_paused` | Creates a paper instance born paused; confirms no signals, jobs, credentials, or runtime position files changed. |
| `paused_skipped_by_fanout` | Runs planner against a paused instance; confirms skip and no pending job. |
| `resume_planning_eligible` | Resumes the instance; confirms planner eligibility is `paper_live_data` only and no job is created by dry-run planning. |
| `pause_skips_again` | Pauses after resume; confirms planner skips again and no new job appears. |
| `real_orders_protected` | Seeds an active `real_orders` instance; confirms pause/resume reject it and planner skips real-orders execution. |
| `cross_user_isolation` | Confirms another user receives 404 and cannot mutate the owner instance. |
| `smuggled_fields_rejected` | Confirms extra `execution_mode`, `status`, `user_id`, live/real, and secret fields are rejected with no DB write. |
| `frontend_static_safety` | Confirms lifecycle UI is flag-gated and references only approved lifecycle helpers/endpoints. |

Expected successful summary:

```text
overall_ok: true
create_born_paused: passed
paused_skipped_by_fanout: passed
resume_planning_eligible: passed
pause_skips_again: passed
real_orders_protected: passed
cross_user_isolation: passed
smuggled_fields_rejected: passed
frontend_static_safety: passed
sensitive_field_count: 0
```

## Remaining Risks

- The lifecycle surface is still a debug/admin mutation surface; it must remain
  behind backend debug gates and internal admin/dev auth.
- Resume makes a paper instance eligible for v2 paper planning when fanout is
  separately enabled. It still does not execute by itself.
- Any future webhook, execution, activation, deletion, subscription, or live
  order mutation needs its own design review and verification gate.

## Recommendation

GO for accepting Phase 2F-2 if the matrix and full test/build verification pass.

NO-GO for adding execution, webhook, live, worker, scheduler, or Dhan mutation
until a separate design review defines explicit safety gates, rollback, audit,
idempotency, and staging verification.

## Verification Snapshot

Recorded on 2026-07-09:

```text
Focused backend tests: 20 passed
Full backend tests: 705 passed
Frontend flags off build: passed
Frontend catalog + lifecycle mutation build: passed
Manual lifecycle matrix: overall_ok true
Manual lifecycle matrix: all 8 scenarios passed
Manual lifecycle matrix: execution_guard_calls all zero
Manual lifecycle matrix: sensitive_field_count 0
```
