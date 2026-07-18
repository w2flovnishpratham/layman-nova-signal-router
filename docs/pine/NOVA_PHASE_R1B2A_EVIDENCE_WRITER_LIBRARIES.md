# NOVA Phase R1B-2A — Insert-Only Evidence Writer Libraries

## Scope statement

R1B-2A builds the three insert-only evidence writer libraries and fixes the
two approved semantic-writer error-envelope findings. It changes nothing
else:

- R1B-2A introduces no production writer call sites.
- R1B-2A writes no evidence during webhook or worker execution.
- R1B-2A does not change signal acceptance.
- R1B-2A does not change replay.
- R1B-2A does not change job creation.
- R1B-2A does not change execution.
- R1B-2A does not change position state.

## Writer purposes and closed APIs

All three writers share the pattern proven by the R1B-1 semantic writer:
flag check before any session access → closed trusted-input validation (no
DB) → one closed database envelope (pre-select by natural key → SAVEPOINT
insert → unique-conflict recovery) → closed error codes. Shared utilities
live in [r1b_evidence_safety.py](../../backend/app/services/r1b_evidence_safety.py)
(canonical `|`-joined SHA-256 keys, hex-shape validation, closed-value and
identifier validation, credential-taint rejection, database-error
translation). No writer imports FastAPI, the router, brokers, Dhan, the
state store or any execution module (AST-verified by tests).

### Canonical decision writer

[canonical_signal_decision_persistence.py](../../backend/app/services/canonical_signal_decision_persistence.py)
— `persist_canonical_signal_decision(session, *, strategy_signal,
strategy_instance, owner_user_id, canonical_event, payload_fingerprint,
received_at, webhook_event=None, adapter_version, source, provenance_kind)`
(writer version `nova.canonical-decision-writer.v1`).

Every row field is derived from the persisted StrategySignal, the trusted
StrategyInstance/owner, the optional WebhookEvent and the immutable R1A
`CanonicalSignalEvent`; the wire action and all compatibility fields come
from the closed inverse of the adapter's action table, so the caller cannot
supply event type, desired state, reason or compatibility values — nor
quantity, lots, strike, expiry, security ID, execution mode, broker fields,
SL/TP, credentials, raw payloads or comments (no such parameters exist).
Cross-checks: owner↔instance, signal↔instance (`instance:{id}` strategy
name), signal↔canonical event, webhook event↔ingress context
(`instance-webhook:{id}` + event id), adapter version equals
`nova.legacy-signal-adapter.v1`. `safe_metadata` is always rebuilt through
the R1B-0 `build_safe_metadata` (comments and credential-shaped values can
never survive). Idempotency: natural key `strategy_signal_id`; identical
call returns the existing row; a conflicting reinterpretation raises
`CANONICAL_DECISION_CONFLICT` and never updates.

### Canonical outcome writer

[canonical_signal_outcome_persistence.py](../../backend/app/services/canonical_signal_outcome_persistence.py)
— `persist_canonical_signal_outcome(session, *, decision, phase,
routing_result, current_state=None, no_op_reason=None, exit_reason=None,
execution_job=None, attempt=None, safe_detail=None)` (writer version
`nova.canonical-outcome-writer.v1`).

Inputs are closed typed values drawn exactly from the 0015 CHECK vocabulary
plus the design-approved 28-value `safe_detail` vocabulary; arbitrary router
dictionaries, broker bodies, order payloads, raw exceptions and metadata are
unrepresentable. `attempt` must be a positive int; an execution job must
belong to the decision's StrategySignal (`FOREIGN_REFERENCE` otherwise);
`result_sha256` stays NULL in R1B-2A. Idempotency key (internal only):
`sha256("nova.r1b2.outcome.v1|decision|phase|job-or-NONE|attempt-or-NONE|`
`routing_result|detail-or-NONE")` — stable across retries, distinct per
phase/attempt/reversal step, always 64 lowercase hex.

### Signal rejection writer

[strategy_signal_rejection_persistence.py](../../backend/app/services/strategy_signal_rejection_persistence.py)
— `persist_strategy_signal_rejection(session, *, strategy_instance,
owner_user_id, stage, rejection_code, received_at, signal_id=None,
webhook_event=None, request_fingerprint=None, safe_detail=None,
adapter_version=None, contract_version=None, known_secrets=())` (writer
version `nova.signal-rejection-writer.v1`).

Requires trusted, owner-resolved context (owner↔instance verified); stages
and codes are the 0015 closed sets; `safe_detail` is a closed vocabulary;
adapter/contract versions must equal the known closed constants. Signal IDs
are stored only as the R1B-0 safe projection plus an internally computed
SHA-256; a credential-shaped or known-secret signal ID suppresses the whole
row with `TAINT_DETECTED` (the value never reaches the row, error text or
output). Dedupe key: `sha256("nova.r1b2.rejection.v1|instance|stage|code|`
`identity-or-NONE|utc-date")` with a UTC daily bucket; identical retries
collapse, conflicting duplicates dedupe per conflicting payload fingerprint,
and no-signal-ID cases stay bounded per instance/stage/code/day.

## Transaction ownership and concurrency

Writers never commit, never roll back and never open the caller's
transaction — proven by tests that roll back the caller session after a
successful write and observe the evidence disappear. Inserts run in a
SAVEPOINT; a unique-race loser re-selects the winner (verified with genuine
two-session, two-thread races on PostgreSQL 18 for all three writers). An
unrelated IntegrityError (e.g. FK violation) is never misclassified as
idempotent success — it surfaces as `PERSISTENCE_UNAVAILABLE`.

## Error translation

Closed exception families in `r1b_evidence_safety`:
`CanonicalDecisionPersistenceError`, `CanonicalOutcomePersistenceError`,
`SignalRejectionPersistenceError` — codes: `PERSISTENCE_DISABLED`,
`INVALID_INPUT`, `OWNER_INSTANCE_MISMATCH`, `FOREIGN_REFERENCE`,
`CANONICAL_DECISION_CONFLICT` (decision only), `TAINT_DETECTED` (rejection
only), `PERSISTENCE_UNAVAILABLE`. Raw SQLAlchemy exceptions are translated
with `from None` (never chained), so SQL text, parameters, constraint names
and paths cannot leak. Unique conflicts are distinguished from unrelated
database failures by winner re-selection.

## Feature flags

`R1B_CANONICAL_DECISION_PERSISTENCE`, `R1B_CANONICAL_OUTCOME_PERSISTENCE`,
`R1B_SIGNAL_REJECTION_PERSISTENCE` — unchanged defaults (false; missing/
empty/invalid resolve false; server-side only). Each writer refuses with
`PERSISTENCE_DISABLED` before any session attribute access when its flag is
false (tested with an exploding session stand-in). Because production call
sites are zero, forcing all flags true still writes nothing at runtime
(dynamic zero-write suite re-verified).

## Zero-call-site and insert-only proofs

[test_r1b2a_zero_call_sites.py](../../backend/app/tests/test_r1b2a_zero_call_sites.py):
no runtime file outside the writer modules references the writer functions,
writer module names or writer flags; the webhook/worker/router/broker/
state-store files are explicitly present and clean; writers import no
execution machinery (AST); no execution service references the evidence
models.
[test_r1b2a_insert_only.py](../../backend/app/tests/test_r1b2a_insert_only.py):
no Core `update()`/`delete()`/`Query.update`/`Query.delete` targets any of
the four evidence models anywhere in runtime code; writer modules expose no
update/delete/upsert/merge API; the ORM `before_update` guard still rejects
mutation. Database-trigger immutability is NOT claimed (per the R1B-1
review boundary).

## Semantic-writer envelope fixes (review findings 1 and 2)

In [pine_semantic_analysis_persistence.py](../../backend/app/services/pine_semantic_analysis_persistence.py):
the owner-chain query now lives inside the closed translation envelope (a
database failure there raises `PERSISTENCE_UNAVAILABLE`, end-to-end 503
`SEMANTIC_PROVENANCE_UNAVAILABLE`, nothing recorded, status unchanged), and
an unexpected analyzer exception is translated to the new closed code
`ANALYSIS_UNAVAILABLE` with no source, registry, stack or path content and
`__cause__` stripped. Successful analysis behavior and provenance identity
are untouched (all prior provenance tests pass unmodified).

## PostgreSQL verification

[scripts/r1b2a_pg_verify.py](../../backend/scripts/r1b2a_pg_verify.py) ran
against a disposable `postgres:18-alpine` container (created for the run,
removed after): flag-off refusal, valid insert + idempotent reuse per
writer, decision-conflict refusal, and genuine concurrent identical inserts
(two sessions, two threads, barrier) resolving to one winner row for each of
the three writers. Alembic head remains `0015_r1b_persistence`; no migration
was created and no schema changed.

## Deviations from the draft prompt (schema-driven)

- `provenance_kind="REALTIME"` from the draft does not exist in the 0015
  CHECK (`LIVE | BACKFILL`); the decision writer uses `LIVE` and rejects
  anything else.
- `canonical_signal_outcomes` and `strategy_signal_rejections` have no
  `provenance_kind` column, so those writers take no such parameter.

## Rollback

Revert the single R1B-2A commit. No schema, no flags-on state, no call
sites, no data: nothing else moves.

## R1B-2B/2C/2D integration boundaries

Unchanged from the approved design and still unauthorized: R1B-2B connects
the decision writer post-commit in `_persist_hold`/`_persist_execution_job`
(HOLD first); R1B-2C connects the outcome writer after the worker's
`_complete_job`; R1B-2D connects the rejection allowlist in `ingest`. Each
requires its own independent review; evidence failure must remain
metric-only at every call site.
