# Phase 2H-1 Instance Details And History

Phase 2H-1 adds a read-only debug surface for inspecting a user's own strategy
instance details and sanitized lifecycle/settings history.

## Endpoint

```text
GET /api/debug/v2/instances/{instance_id}/details
```

Required gates:

- `DEBUG_ENABLED=true`
- authenticated internal admin/dev user
- current user must own the instance

The endpoint does not require:

- `STRATEGY_INSTANCE_MUTATION_DEBUG`
- `MULTI_STRATEGY_FANOUT`
- `V2_PAPER_RUNNER_DEBUG`
- `confirm_paper_only`

Ownership behavior:

- owner instance: `200`
- non-owner instance: `404`
- unknown or malformed instance id: `404`

## Response Fields

The response returns only curated instance fields:

- `id`
- `strategy_code`
- `strategy_name`
- `strategy_version`
- `status`
- `execution_mode`
- `instance_label`
- `lots`
- `side_preference`
- `created_at`
- `updated_at`

History rows are summarized to:

- `event_type`
- `created_at`
- `summary`
- `changed_fields`
- `old_values`
- `new_values`
- `previous_status`
- `new_status`
- `actor_type`

History values are limited to these safe fields only:

- `instance_label`
- `lots`
- `side_preference`
- `status`
- `execution_mode`
- `strategy_code`

## Forbidden Fields

The details endpoint and UI must not expose:

- `config_json` or `risk_config` wholesale
- raw request or response bodies
- `raw_payload` or `signal_payload`
- credential hashes
- webhook secrets
- access tokens
- headers
- proxy URLs
- Dhan credentials or client IDs
- full audit metadata

## Frontend Behavior

`StrategyCatalogStatusPanel` now shows a `Details` button for listed instances
when `VITE_ENABLE_STRATEGY_CATALOG_STATUS=true`.

The details drawer contains:

- current safe instance fields
- sanitized lifecycle/settings history
- Refresh
- Close

No new frontend flag is required. The mutation UI flag is not required for
details. The drawer does not add archive, delete, execution, webhook, live, Dhan,
or order controls.

## Data Access

The endpoint reads:

- `user_strategy_instances`
- `strategy_catalog`
- `strategy_versions`
- safe audit history rows/records when available

The endpoint writes nothing. It does not create strategy signals, normalized
signals, execution jobs, webhook credentials, runtime state, order logs, or audit
logs for the read.

## Manual Smoke

Use a fresh local or staging paper environment.

1. Build with catalog status enabled:

   ```bash
   VITE_ENABLE_STRATEGY_CATALOG_STATUS=true npm --prefix frontend run build
   ```

2. With mutation controls enabled in a safe environment, create a paper instance,
   edit settings while paused, resume once, and pause once.

3. Open Strategy Catalog Status and click `Details`.

4. Confirm:

   - instance details are visible
   - history rows are summarized
   - Refresh reloads the same read-only details endpoint
   - Close dismisses the drawer without any write request
   - no raw config/risk/audit body fields appear
   - browser network for details is GET only
   - no runner, webhook, live, Dhan, or order path is called
   - the V2 Paper panel remains read-only
   - legacy Trading opens normally after leaving the internal panel

## Remaining Risks

- History completeness depends on the available audit history source. The
  endpoint is intentionally conservative and drops any unknown or unsafe fields.
- `safe_runtime_summary` is not included unless a future phase can derive it
  without broker, Dhan, PaperBroker, or runtime-state mutation.
