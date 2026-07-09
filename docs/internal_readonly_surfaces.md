# Internal Strategy Surfaces

This document records the current internal observability surfaces for v2 paper
fanout and multi-strategy catalog visibility. Phase 2F-1 adds one narrow
debug-gated mutation exception for paper strategy instance lifecycle only:
create born paused, pause, and resume. Phase 2G-1 adds paused-only settings
edit for `instance_label`, `lots`, and `side_preference`. These are not
execution surfaces. Phase 2H-1 adds read-only instance details/history
visibility for safe inspection before any further mutation. Phase 2H-2 adds
paused-only soft archive and restore-to-paused lifecycle mutation. Phase 2I-1
adds a configuration-only clone/duplicate that always creates a new,
born-paused instance.

## Panels And Flags

| Panel | Frontend flag | Default | Route |
| --- | --- | --- | --- |
| V2 Paper Status | `VITE_ENABLE_V2_PAPER_STATUS=true` | Off | `#v2-paper-status` |
| Strategy Catalog Status | `VITE_ENABLE_STRATEGY_CATALOG_STATUS=true` | Off | `#strategy-catalog-status` |
| Paper instance lifecycle/settings controls | `VITE_ENABLE_STRATEGY_INSTANCE_MUTATION=true` | Off | `#strategy-catalog-status` |

Both flags are frontend visibility guards only. Backend auth, debug flags, and
endpoint gates remain the real protection.

When a flag is off:

- The header/menu item is hidden.
- Direct hash route renders an internal-only disabled state.
- The panel does not call its internal APIs.

## Endpoints

V2 Paper Status calls:

- `GET /api/debug/v2/paper-fanout/status`
- `GET /api/debug/v2/paper-fanout/jobs`
- `GET /api/debug/v2/paper-fanout/instances`

Strategy Catalog Status calls:

- `GET /api/strategies/catalog`
- `GET /api/strategies/instances`

Paper instance lifecycle controls call:

- `POST /api/debug/v2/instances`
- `POST /api/debug/v2/instances/{id}/pause`
- `POST /api/debug/v2/instances/{id}/resume`
- `POST /api/debug/v2/instances/{id}/settings`
- `POST /api/debug/v2/instances/{id}/archive`
- `POST /api/debug/v2/instances/{id}/restore`
- `POST /api/debug/v2/instances/{id}/clone`

Instance details/history calls:

- `GET /api/debug/v2/instances/{id}/details`

The mutation endpoints require backend debug gates and `confirm_paper_only`.
Create always writes `execution_mode="paper_live_data"` and `status="paused"`.
Pause/resume only flip eligible paper or signal-only instance status between
`active` and `paused`. Settings edit is allowed only while an eligible
paper/signal-only instance is already paused, and only changes
`instance_label`, `lots`, `side_preference`, `updated_at`, and the safe
`config_json.last_mutation` breadcrumb.

Archive/restore requires the same debug mutation gates and confirmation. Archive
only changes a paused eligible paper/signal-only instance to `archived`; restore
only returns an archived eligible paper/signal-only instance to `paused`.
Idempotent repeats do not write or audit. Archived instances are hidden from the
catalog panel list by default and skipped by the v2 planner with
`ARCHIVED_INSTANCE`.

Clone requires the same debug mutation gates and confirmation. It is allowed
from any eligible paper/signal-only source instance whose status is `active`,
`paused`, or `archived` (`real_orders` sources → 403; `disabled`/`error`
sources → 409). The new instance always belongs to the calling user, is
always `execution_mode="paper_live_data"` and `status="paused"` regardless of
the source's mode or status, and copies only `strategy_id`,
`strategy_version_id` (pinned to the source's version), `lots`,
`side_preference`, `source_type`, and a renumbered `instance_label` (`"{name}
(Copy)"`, then `"(Copy 2)"`, `"(Copy 3)"`, ...). It never copies the source's
`config_json`/`risk_config`, runtime state, history, audit rows, or webhook
credentials — the clone starts with a fresh `config_json`, empty
`risk_config`, and zero history rows of its own. A per-user, per-strategy cap
of 10 instances blocks further clones once reached (409); this is a narrow
row-creation guard, not a general rate limiter, since clone is the only
mutation on this surface that creates a new row per call instead of editing
an existing one.

The details endpoint requires only backend debug access and an internal
admin/dev user. It does not require mutation, fanout, runner, or confirmation
flags. It returns only safe instance fields plus sanitized lifecycle/settings
history. It does not write audit logs for the read, strategy signals, jobs,
credentials, runtime state, or order logs.

## Never Allowed In These Surfaces

Do not add controls or API calls for:

- subscribe or unsubscribe
- activate or deactivate
- run, process, or retry jobs
- generate webhooks or rotate secrets
- go live or place paper/live orders
- square off or exit positions
- worker, scheduler, or startup wiring
- Dhan live placement

Phase 2F-1 allows only:

- create paper instance born paused
- pause eligible paper/signal-only instance
- resume eligible paper/signal-only instance

Phase 2G-1 additionally allows only:

- edit paused eligible paper/signal-only instance label
- edit paused eligible paper/signal-only instance lots
- edit paused eligible paper/signal-only instance side preference

Phase 2H-1 additionally allows only:

- view safe instance details for the current user's own instance
- view sanitized lifecycle/settings history
- refresh or close the details drawer

Phase 2H-2 additionally allows only:

- archive a paused eligible paper/signal-only instance
- restore an archived eligible paper/signal-only instance to paused
- show/hide archived rows client-side in the internal catalog panel

Phase 2I-1 additionally allows only:

- clone an active, paused, or archived eligible paper/signal-only instance
  into a new, always-paused, always-`paper_live_data`, always own-user
  instance
- copy only `strategy_id`/`strategy_version_id`/`lots`/`side_preference`/
  `source_type`/a renumbered label; never copy status, runtime state,
  history, audit rows, or webhook credentials
- enforce a per-user, per-strategy cap of 10 instances

It does not add execution controls, webhook generation, live mode, Dhan
placement, workers, schedulers, startup wiring, signal writes, job writes,
credential writes, or runtime state writes.

## Sensitive Fields

These surfaces must not show or return credential material, secret hashes, raw
payloads, request/response bodies, headers, or tokens. Examples:

- `secret`
- `access_token`
- `token`
- `client_id`
- `webhook_key_hash`
- `message_secret_hash`
- `credential_hash`
- `proxy_url`
- `raw_payload`
- `signal_payload`
- `headers`
- `response_json`
- `config_json`
- `risk_config`
- `metadata_json`

Backend endpoints should curate response fields. Frontend reads also sanitize
internal status payloads before rendering.

## Manual Smoke

Use a safe local or staging environment only.

Default-off smoke:

```bash
npm --prefix frontend run build
```

Expected:

- No internal nav items are visible.
- Direct internal hash routes show disabled state.
- No internal API calls are made by disabled panels.

Single-panel smoke:

```bash
VITE_ENABLE_V2_PAPER_STATUS=true npm --prefix frontend run build
VITE_ENABLE_STRATEGY_CATALOG_STATUS=true npm --prefix frontend run build
```

Expected:

- Enabled panel calls only its GET endpoints.
- The other internal panel remains hidden/disabled if its flag is off.
- No sensitive fields are visible.

Both-panel smoke:

```bash
VITE_ENABLE_V2_PAPER_STATUS=true VITE_ENABLE_STRATEGY_CATALOG_STATUS=true npm --prefix frontend run build
```

Expected:

- Both internal panels build successfully.
- Observed network traffic remains GET-only for internal endpoints.

Lifecycle smoke:

```bash
VITE_ENABLE_STRATEGY_CATALOG_STATUS=true VITE_ENABLE_STRATEGY_INSTANCE_MUTATION=true npm --prefix frontend run build
```

Expected:

- Lifecycle controls appear only in the catalog status panel.
- New instances are created paused and paper-only.
- Pause/resume require confirmation.
- Settings edit appears only for paused eligible paper/signal-only instances.
- Active eligible instances show `Pause to edit` and do not show settings save.
- Settings edit requires confirmation copy: `Paper only. No orders will be placed.`
- No execution, webhook, live, worker, scheduler, or Dhan calls occur.

Details smoke:

```bash
VITE_ENABLE_STRATEGY_CATALOG_STATUS=true npm --prefix frontend run build
```

Expected:

- Instance rows show a Details button without requiring the mutation UI flag.
- Details opens a read-only drawer with safe fields and sanitized history.
- Details refresh uses only `GET /api/debug/v2/instances/{id}/details`.
- No raw config/risk/audit body fields, secrets, tokens, headers, or proxy URLs
  appear.
- No execution, webhook, live, worker, scheduler, or Dhan calls occur.

Archive smoke:

```bash
VITE_ENABLE_STRATEGY_CATALOG_STATUS=true VITE_ENABLE_STRATEGY_INSTANCE_MUTATION=true npm --prefix frontend run build
```

Expected:

- Paused eligible rows show Archive.
- Archived rows are hidden until Show archived is checked.
- Archived eligible rows show Restore and not Resume.
- Archive/restore use only their debug POST endpoints with paper confirmation.
- Planner skips archived rows with `ARCHIVED_INSTANCE`.
- No signals, jobs, webhook credentials, runtime state, order logs, live paths,
  workers, schedulers, or Dhan calls occur.

Clone smoke:

```bash
VITE_ENABLE_STRATEGY_CATALOG_STATUS=true VITE_ENABLE_STRATEGY_INSTANCE_MUTATION=true npm --prefix frontend run build
```

Expected:

- Active, paused, and archived eligible rows all show Clone.
- Cloning always creates a new row with `status=paused`,
  `execution_mode=paper_live_data`, regardless of the source row's status or
  mode.
- Cloned label reads `"{source label} (Copy)"`, then `"(Copy 2)"`, etc. on
  repeated clones of the same source.
- The source instance is never mutated by cloning it.
- Clone uses only its debug POST endpoint with paper confirmation.
- Planner skips a freshly cloned instance with `PAUSED_INSTANCE` until it is
  explicitly resumed.
- A tenth clone of the same strategy for the same user is rejected (409); no
  row is written.
- No signals, jobs, webhook credentials, runtime state, order logs, live paths,
  workers, schedulers, or Dhan calls occur.

## Required Review Before Further Mutation

Any future phase beyond the Phase 2F-1 lifecycle triplet that adds subscription,
activation, webhook setup, paper execution, live execution, or job processing
must be reviewed separately before implementation. That review should define
auth, entitlements, idempotency, audit logging, staging verification, rollback,
and Dhan/live safety gates first.
