# Phase 2H-0 Next Safe Feature Design Review

Status: Accepted

This checkpoint documents the Phase 2H-0 review decision. The original review
artifact was not present in the repository when Phase 2H-1 was completed, so
this file records the accepted decision from the review conversation before any
Phase 2H-2 mutation work starts.

## Decision

Phase 2H-1 should be read-only instance details/history only.

This was accepted as the safest next step because it improves visibility before
adding another mutation such as archive or disable. It also keeps the work away
from execution-adjacent paths such as runner buttons, public webhooks, live
activation, or broker order placement.

## Approved Phase 2H-1 Scope

Add a read-only details/history view for a user's own strategy instance.

Allowed:

- one read-only endpoint under the debug router
- safe instance details
- current safe settings
- strategy/version metadata
- sanitized lifecycle/settings history
- safe status timeline
- frontend Details button and read-only drawer/modal
- Refresh and Close inside the details view

The endpoint:

```text
GET /api/debug/v2/instances/{instance_id}/details
```

Required gates:

- `DEBUG_ENABLED=true`
- authenticated internal admin/dev user
- current user owns the instance

Ownership behavior:

- owner instance: `200`
- non-owner instance: `404`
- unknown or malformed instance id: `404`

The endpoint must not require:

- `STRATEGY_INSTANCE_MUTATION_DEBUG`
- `MULTI_STRATEGY_FANOUT`
- `V2_PAPER_RUNNER_DEBUG`
- `confirm_paper_only`

## Forbidden In Phase 2H-1

Do not add:

- mutation endpoints
- archive, disable, or delete
- edit controls beyond existing approved controls
- execution buttons
- run, process, or retry controls
- webhook generation
- public or custom webhook routing
- live execution
- `real_orders` processing
- startup, worker, or scheduler wiring
- Dhan live placement changes
- legacy Supertrend behavior changes

Do not expose:

- `config_json` wholesale
- `risk_config` wholesale
- raw request or response bodies
- `raw_payload`
- `signal_payload`
- credential hashes
- webhook secrets
- access tokens
- headers
- proxy URLs
- Dhan credentials
- client IDs
- full audit metadata that may contain bodies or secrets

Do not write:

- audit logs for the read unless an existing policy requires it
- `strategy_signals`
- `normalized_option_signals`
- `strategy_execution_jobs`
- webhook credentials
- runtime state files
- order logs

## Safe History Projection

Allowed history fields:

- `event_type`
- `created_at`
- `summary`
- `changed_fields`
- `old_values` for safe fields only
- `new_values` for safe fields only
- `previous_status`
- `new_status`
- safe actor type

Safe fields:

- `instance_label`
- `lots`
- `side_preference`
- `status`
- `execution_mode`
- `strategy_code`

## Phase 2H-2 Guidance

The next mutation should not jump to execution or webhooks. The safe progression
after Phase 2H-1 remains:

1. Soft archive / disable with paused-only constraints.
2. Clone / duplicate.
3. Execution-oriented features only after dedicated design review.

Any future mutation must receive its own safety review before implementation.
