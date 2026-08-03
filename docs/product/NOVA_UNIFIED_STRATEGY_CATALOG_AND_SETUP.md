# NOVA Unified Strategy Catalog and Conversational Setup

## Purpose

The Trading page uses one backend-authoritative catalog and one reusable,
schema-driven setup conversation for NOVA built-ins and owner-imported Pine
strategies. The selected-strategy card is the final review/runtime surface; it
is not the strategy picker.

## Architecture

`backend/app/services/built_in_strategy_registry.py` owns built-in product
metadata, stable keys, real availability, execution-adapter identity, and setup
schemas. `backend/app/services/strategy_catalog_service.py` combines those
definitions with the authenticated owner's `StrategyInstance` rows and their
existing C2 readiness projection.

The normalized catalog is available at:

```text
GET /api/strategies/catalog
```

Runtime hydration also embeds the same projection as `strategy_catalog` in:

```text
GET /api/runtime/status
```

No frontend strategy-name array is authoritative.

## Built-in registry

The registered product keys are deterministic:

| Key | Strategy | Availability | Execution adapter |
| --- | --- | --- | --- |
| `nova-supertrend` | Supertrend | Paper ready | `strategy_webhook:supertrend` |
| `nova-orb` | ORB | Coming soon | Missing |
| `nova-vwap` | VWAP | Coming soon | Missing |
| `nova-rsi` | RSI | Coming soon | Missing |
| `nova-scalper` | Scalper | Coming soon | Missing |

Only Supertrend has a real backend execution path. The other names remain
visible as disabled product entries with an explicit `Missing execution
adapter` reason. No strategy parameters or execution logic were fabricated.
Live eligibility is false for every catalog entry in isolated staging.

## Imported strategy inclusion

Imported entries are loaded only from the authenticated owner's non-archived
`StrategyInstance` rows. Selectability comes from the existing
`_engine_eligibility()` projection, which reuses C2 `readiness_for_setup()` for
C2 installations. It therefore retains owner binding, active installation and
credential, genuine HOLD, paper entry/exit evidence, and candidate/source/
strategy-layer integrity gates.

Stopped-but-ready imported strategies remain selectable. Unready owner-visible
entries are returned disabled with their existing blocker code so the UI can
explain a lost-readiness selection. No credential value, credential hash, Pine
source, Dhan token, TOTP, or provider-private data is included.

When immutable semantic-analysis evidence exists, the catalog projects only a
safe exit-behavior summary: explicit Pine exits present, no explicit Pine exit
detected, or unknown. The setup conversation explains that backend SL/TP is
independent safety. If no explicit exit was detected, it identifies
opposite-signal reversal, backend SL/TP, and manual square-off as the available
exit paths. Pine source and analysis internals are never returned.

## Stable identity and persistence

Imported strategy identity is its `strategy_instance_id`.

Built-in identity is its stable server key, such as `nova-supertrend`. When a
user confirms a runnable built-in selection, the backend resolves or creates a
real owner-bound `NOVA_SHARED` `StrategyInstance`. An inactive legacy
subscription is used during materialization, so selection cannot enqueue a
signal or begin execution.

Both types continue to persist through the existing
`UserEngineConfig.selected_strategy_instance_id`. No migration or competing
selection column was needed.

Selection uses:

```text
PUT /api/strategies/catalog/selection
```

It is owner-scoped and requires a stopped, flat runtime when the identity
changes. Selection never starts/stops the engine, creates an order/position/job,
rotates credentials, changes mode, or sends HOLD.

## Setup schema and validation

The backend returns the applicable setup schema with each catalog entry. The
current real runtime supports:

```text
direction: CE | PE | BOTH
lots: 1..20
stop_loss_percent: 0..100
take_profit_percent: 0..1000
```

Supertrend does not expose ATR period or multiplier because the current runtime
has no user-level override path. Imported Pine entries do not expose Pine input
overrides; Pine entry/exit logic remains unchanged. The four unsupported
built-ins return no setup fields.

The shared frontend renderer asks each returned field in order. The backend
revalidates required fields, choices, numeric types, bounds, and unknown keys.
Final setup is saved using:

```text
PUT /api/strategies/catalog/setup
```

The complete mode configuration is one JSON mutation under the selected
instance's existing `risk_config.unified_setup`. Invalid input changes nothing.
Paper and Live are separate keys. Current Live-ineligible entries reject Live
setup without touching Paper.

## Conversation state

The product sequence is:

```text
CHOOSE_MODE
→ CHOOSE_STRATEGY
→ CONFIGURE_STRATEGY (backend schema fields)
→ REVIEW
→ READY_TO_START
→ RUNNING
```

The existing session mode step remains the entry point. Isolated staging shows
Live as unavailable and disables its action. The strategy step renders `NOVA
Strategies` and `My Strategies`. A confirmed strategy selection enters the
shared question renderer. Successful atomic save enters review and renders the
existing `TradingStrategyCard`.

Finalized setup progress is backend-derived. A refresh or navigation back to
Trading restores review when the selected instance has a completed mode setup.
An incomplete setup resumes at catalog/configuration without silently choosing
a strategy.

## Explicit start and adapter binding

Only:

```text
POST /api/runtime/start-selected
```

starts the selected Paper engine. The endpoint requires:

- an owner-selected, Paper-ready strategy;
- a completed saved Paper setup;
- a stopped engine;
- a flat tracked position.

It applies the selected saved values to the Paper runtime immediately before
start. For `nova-supertrend`, explicit start activates the real
`supertrend` fan-out subscription in `paper_live_data` mode with the saved lots. Imported execution
remains bound to its private credential and selected `StrategyInstance`.
Stopping or reconfiguring deactivates a selected built-in subscription.

Opening Trading, loading the catalog, choosing a mode, choosing a strategy, and
saving setup do not start execution.

## Change Strategy safety

When stopped and flat, `Change Strategy` returns to the catalog and leaves the
current selection in place until a new card is confirmed. A running/changing
engine or tracked open position disables configuration and strategy change with
an explicit instruction to stop and become flat. The existing Stop &
Reconfigure path also deactivates a selected built-in subscription.

## Authorization and safety

Authentication supplies every user identity. User-provided owner IDs are never
accepted. A user cannot list, select, configure, or start another owner's
instance. Built-in definitions are globally visible, while their materialized
instance, setup, and engine pointer are owner-scoped.

Isolated-staging safety remains:

```text
APP_ENV=isolated_staging
DHAN_MODE=MOCK
ENABLE_LIVE_ORDERS=false
PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED=false
```

Live start remains unavailable. The catalog and conversation perform no broker
call.

## Verification

Focused Pytest coverage validates deterministic built-ins, honest disabled
states, owner isolation, secret/source omission, selection persistence,
unsupported selection rejection, no order/position/job side effects, atomic
setup validation, rehydration, Live isolation, and Supertrend adapter binding.

Vitest and React Testing Library cover both catalog groups, all five built-in
names, imported entries, disabled reasons, schema-driven questions, controlled
validation, review hydration, explicit start, safe Change Strategy, running
guards, Live unavailable, and DOM secret/source omission.

Existing C2 readiness, selection, Trading hydration, and runtime lifecycle
suites remain part of the regression gate.

## Deployment and rollback

Deploy only the named feature branch to isolated staging after recording the
current SHA and backing up PostgreSQL, `/etc/layman/layman.env`, and frontend
static files. No Alembic migration is required. Restart only
`layman-nova-signal-router.service` for backend code and replace only the
isolated-staging frontend static directory.

Rollback restores the recorded backend SHA and frontend backup, restores the
environment only if it changed, and restarts the same service. Database restore
is not normally needed because this feature adds no schema migration.

## Known limitations

- ORB, VWAP, RSI, and Scalper are catalog-visible but intentionally disabled
  until real execution adapters exist.
- Supertrend's alert/webhook signal logic is not parameterized by ATR settings
  at user level, so those questions are not exposed.
- Imported Pine inputs are not overridden by this setup flow.
- Live setup/start remains unavailable under the current safety gates.
