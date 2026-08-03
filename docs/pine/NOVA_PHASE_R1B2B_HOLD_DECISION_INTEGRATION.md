# NOVA Phase R1B-2B — HOLD Decision Integration

## Scope and authority

R1B-2B connects the reviewed insert-only canonical decision writer to one
runtime event only: a freshly accepted and committed `HOLD`. The existing
webhook path remains authoritative for authentication, lifecycle and freshness
validation, replay identity, signal acceptance, setup evidence, HTTP status and
response construction.

Canonical decision evidence is optional. Missing evidence does not invalidate
an accepted HOLD. Canonical decisions do not control verification or execution.
Duplicate HOLD requests do not repair missing decisions. Trading-action
decisions remain disconnected. Outcomes remain disconnected. Rejections remain
disconnected.

## Exact authoritative commit boundary

The replay claim opens `session_scope()` in
`backend/app/services/webhook_replay_store.py:73`, flushes the `WebhookEvent`,
and exits the context before returning its committed identifier at lines
88–92. `session_scope()` commits on normal context exit, rolls back on error,
and always closes the session (`backend/app/db/engine.py:124-135`).

`_persist_hold()` opens a second authoritative `session_scope()` at
`backend/app/services/private_webhook_service.py:535`. Its line 544 `flush()`
is not treated as commit. The commit occurs only when the context exits. The
function then returns the already-determined HOLD result at lines 552–557.

The call sequence is therefore:

```text
claim WebhookEvent
→ replay session exits and commits
→ call _persist_hold()
→ HOLD session exits and commits StrategySignal
→ _persist_hold() returns authoritative result
→ if and only if replay status was fresh, attempt optional evidence
→ audit and return the unchanged authoritative result
```

The evidence call is at
`backend/app/services/private_webhook_service.py:445-461`, after
`_persist_hold()` has returned. An authoritative HOLD commit exception prevents
control from reaching the evidence call.

## Separate evidence session

`backend/app/services/hold_canonical_decision_evidence.py` provides
`persist_hold_decision_best_effort()`. It checks
`R1B_CANONICAL_DECISION_PERSISTENCE` before parsing identifiers or opening a
session (lines 72–73). When enabled it opens its own `session_scope()` at line
79 and re-reads the committed `WebhookEvent`, `StrategyInstance`, and
`StrategySignal`.

The helper verifies:

- the event and instance exist;
- event and instance have the supplied committed owner;
- the event provider identifies the same instance;
- committed event metadata identifies `HOLD` and the same instance;
- the committed signal belongs to the instance and event;
- the signal is completed with authoritative `HOLD`/`HOLD` summary values.

Only then does it construct the canonical event through the approved legacy
adapter and invoke `persist_canonical_signal_decision()` at lines 121–135. The
evidence context alone commits, rolls back, and closes.

## Mapping and fingerprint provenance

The resulting decision is:

```text
wire_action = HOLD
event_type = CONNECTIVITY_TEST
desired_state = NONE
intent_reason = CONNECTIVITY_TEST
compatibility_action = null
compatibility_side = null
compatibility_option_side = null
provenance_kind = LIVE
adapter_version = nova.legacy-signal-adapter.v1
```

The trusted canonical ingress fingerprint is created before replay claim and
committed in `WebhookEvent.event_metadata["payload_fingerprint"]`. The schema
does not have a separate `WebhookEvent.payload_fingerprint` column;
`raw_body_sha256` is the replay store's SHA-256 of that canonical fingerprint
string and remains unchanged. The evidence helper reads the canonical
fingerprint only from the committed event metadata at line 132. Its signature
does not accept a fingerprint, so request metadata, comments, frontend input,
or another caller cannot substitute one.

## Fresh-only and no-backfill policy

The runtime call is nested under both `action == "HOLD"` and replay
`status == "fresh"`. Exact duplicates return from the existing duplicate path
before the helper. Conflicting duplicates, rejected, stale, future-skew,
malformed, and unauthenticated requests likewise never reach it.

If a HOLD was accepted while the flag was off, enabling the flag later and
redelivering that request does not repair or backfill its missing decision.
Historical backfill is outside R1B-2B.

## Response and failure isolation

The helper returns `None`; no evidence value enters the response. Closed writer
errors, invalid or foreign references, conflicts, taint failures, database
failures, and unexpected helper exceptions are suppressed. Diagnostics contain
only:

```text
event=r1b_hold_decision_evidence_failed
writer_version=<closed writer version>
error_code=<closed safe code>
```

Credentials, signal IDs, fingerprints, payloads, owner details, SQL,
exceptions, and paths are not logged. Failure of the logger is also
suppressed. The pre-existing HOLD status, body, duplicate behavior, setup
evidence, and zero-job invariant are unchanged.

## Owner, reference, execution, and call-site safety

Owner, instance, event-provider, event-action, and committed signal checks all
fail closed for evidence only. The helper imports no worker, router, risk,
broker, Dhan, position, reconciliation, scheduling, credential, or replay-store
machinery.

AST tests prove:

```text
persist_canonical_signal_decision production call sites = 1
allowed chain = fresh committed HOLD → HOLD helper → decision writer
BUY_CE/BUY_PE/EXIT decision integration sites = 0
persist_canonical_signal_outcome production call sites = 0
persist_strategy_signal_rejection production call sites = 0
```

HOLD still creates no `StrategyExecutionJob`, broker call, position mutation,
or canonical outcome/rejection.

## Concurrency and PostgreSQL verification

`backend/scripts/r1b2b_pg_verify.py` migrates a disposable PostgreSQL 18
database to the current head and verifies:

- flag-off fresh HOLD and no evidence session;
- flag-on mapping and exact committed fingerprint provenance;
- duplicate no-backfill and conflicting duplicates;
- genuine concurrent identical and conflicting HOLD ingress;
- a genuine concurrent evidence unique race;
- evidence database outage after authoritative commit;
- evidence transaction rollback/commit failure isolation;
- authoritative commit failure preventing the evidence call;
- owner, instance, event, and missing-reference suppression.

The disposable container is removed after the run. PostgreSQL is authoritative
for transaction and concurrency behavior; SQLite remains the fast regression
layer.

## Rollback

Disable `R1B_CANONICAL_DECISION_PERSISTENCE` (default `false`) to make the
helper return before all database work. Code rollback consists of removing the
fresh-HOLD helper invocation and its internal replay-claim identifier; no schema
rollback is required. No migration was added or changed.

## Remaining phases

R1B-2C and R1B-2D remain separately blocked on explicit design, implementation,
and independent review authorization. This phase grants no approval for
trading-action decisions, outcomes, rejections, backfill, APIs, workers,
execution authority, live orders, prompt/transport changes, or AI conversion.
