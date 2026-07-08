# Phase 2G-1 Paused Settings Edit

Phase 2G-1 adds a narrow debug-gated settings edit for existing strategy
instances. It is data-only and applies only to eligible `paper_live_data` or
`signal_only` instances that are already paused.

## Endpoint

`POST /api/debug/v2/instances/{instance_id}/settings`

Required gates:

- `DEBUG_ENABLED=true`
- `MULTI_STRATEGY_MODEL=true`
- `STRATEGY_INSTANCE_MUTATION_DEBUG=true`
- internal admin or dev user
- `confirm_paper_only=true`

This endpoint does not require `MULTI_STRATEGY_FANOUT` or
`V2_PAPER_RUNNER_DEBUG`.

## Editable Fields

- `instance_label`
- `lots`
- `side_preference`

At least one editable field must be present. Extra request fields are rejected.
`real_orders` instances are forbidden. Active eligible instances return:

```text
Pause the instance before editing.
```

## Safety Boundary

The endpoint writes only `user_strategy_instances` fields needed for the edit:

- `instance_label`
- `lots`
- `side_preference`
- `updated_at`
- `config_json.last_mutation`

It does not write signals, normalized signals, execution jobs, webhook
credentials, runtime state, order logs, live Dhan credentials, routes, workers,
schedulers, startup hooks, or frontend execution controls.

Actual changes emit `V2_INSTANCE_SETTINGS_EDITED` audit metadata with changed
field names, old values, new values, actor user id, instance id, strategy code,
execution mode, and feature flag state. No-op edits return `changed=false` and
do not emit an audit event.
