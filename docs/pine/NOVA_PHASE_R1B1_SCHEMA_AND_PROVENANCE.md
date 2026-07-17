# NOVA Phase R1B-1 — Additive Persistence Schema and Semantic Provenance

## Scope statement

R1B-1 adds the four dedicated canonical evidence tables and implements only
immutable Pine semantic-analysis provenance persistence.

- No canonical decisions are written.
- No canonical outcomes are written.
- No signal rejections are written.
- Only semantic-analysis provenance may be written.
- All persistence flags default false.
- No new table controls execution.

## Four-table evidence schema

Migration `0015_r1b_persistence`
([20260718_0015_r1b_persistence.py](../../backend/alembic/versions/20260718_0015_r1b_persistence.py))
creates, in order:

1. `canonical_signal_decisions` — at most one immutable canonical
   interpretation per accepted `StrategySignal`
   (`uq_canonical_decision_signal`). Closed CHECKs on `wire_action`,
   `event_type`, `desired_state`, `intent_reason`, the three compatibility
   fields and `provenance_kind`, plus four cross-field action-consistency
   CHECKs (HOLD/BUY_CE/BUY_PE/EXIT). FKs: `strategy_signals` (CASCADE),
   `webhook_events` (SET NULL), and the composite instance-owner key
   `(strategy_instance_id, user_id) → strategy_instances(id, user_id)`
   (CASCADE) so a decision can never claim another user's instance.
2. `canonical_signal_outcomes` — append-only routing/state observations.
   `uq_canonical_outcome_idempotency` on `idempotency_key`; closed CHECKs on
   phase, current state, routing result, no-op reason, exit reason;
   `attempt > 0` when non-null. FKs: decision (CASCADE), execution job
   (SET NULL).
3. `strategy_signal_rejections` — future safe post-authentication ingress
   rejection evidence. `uq_signal_rejection_dedupe`; closed stage and
   rejection-code CHECKs; composite instance-owner FK (CASCADE); webhook
   event (SET NULL).
4. `pine_semantic_analyses` — immutable semantic-analysis provenance; the
   only table with an R1B-1 writer. Unique provenance tuple
   `uq_pine_analysis_provenance` over `(source_artifact_id, source_sha256,
   analyzer_version, registry_id, registry_version, registry_sha256,
   analysis_schema_version)`. FKs: `strategy_source_artifacts` (CASCADE) and
   self-referential `supersedes_analysis_id` (SET NULL).

All enumerated columns are portable `VARCHAR` with named CHECK constraints —
no PostgreSQL ENUM. One drafted width was corrected during PostgreSQL
verification: `analysis_schema_version` is `VARCHAR(50)` (not 30) because the
mandated closed schema version string
`nova.pine-semantic-analysis-persistence.v1` is 42 characters — PostgreSQL
rejects the insert at the drafted width while SQLite silently accepts it. Hash columns are `CHAR(64)` with named CHECKs enforcing
length 64 and lowercase on both PostgreSQL and SQLite; the hexadecimal
character set itself is not portably expressible in one CHECK, so it is
guaranteed by the writers, which only ever store `hashlib.sha256(...).
hexdigest()` output (deliberate portable simplification, noted for review).

## Authority boundaries

The tables are EVIDENCE ONLY — NOT EXECUTION AUTHORITY. The public webhook
actions (BUY_CE/BUY_PE/EXIT/HOLD), WebhookEvent replay identity,
NormalizedSignal, StrategyExecutionJob, the execution router, PaperBroker/
Dhan adapters and per-user JSON position files remain authoritative and
untouched. No query in the execution router, risk manager, broker adapters or
position store references the new tables, and no failure involving them can
alter webhook responses, signal acceptance, replay results, job creation,
broker routing, position mutation or verification readiness.

## Migration and downgrade

- Revision `0015_r1b_persistence` (20 characters, fits the deployed
  `alembic_version.version_num VARCHAR(32)`), down revision
  `0014_verify_select`.
- Creates only new tables and indexes; adds no column to any existing table;
  performs no backfill; modifies no existing row.
- Table creation is inspector-guarded because the 0001 baseline `create_all`
  materializes current models on fresh databases (recorded tech debt), so a
  fresh chain reaches 0015 with the tables already present.
- Downgrade drops the four tables in exact reverse dependency order:
  `pine_semantic_analyses`, `strategy_signal_rejections`,
  `canonical_signal_outcomes`, `canonical_signal_decisions`.

## Feature flags

Four server-side flags in `app/config.py`, all `False` by default, all run
through the fail-safe boolean validator (missing, empty or invalid values
resolve to `False`), none controllable via API or webhook, none exposed to
the frontend:

| Flag | R1B-1 role |
| --- | --- |
| `R1B_CANONICAL_DECISION_PERSISTENCE` | declared only — zero call sites |
| `R1B_CANONICAL_OUTCOME_PERSISTENCE` | declared only — zero call sites |
| `R1B_SIGNAL_REJECTION_PERSISTENCE` | declared only — zero call sites |
| `R1B_PINE_ANALYSIS_PERSISTENCE` | gates semantic-analysis provenance only |

`CANONICAL_SIGNAL_SHADOW` is not reused as a persistence flag. Because the
three declared-only flags have zero runtime call sites, setting them true
cannot activate unfinished behavior — proven by static and dynamic tests.

## Semantic-analysis provenance

Writer: `app/services/pine_semantic_analysis_persistence.py`, schema version
`nova.pine-semantic-analysis-persistence.v1`.

`persist_semantic_analysis(session, source_artifact, source_text, *,
owner_user_id=None, supersedes_analysis_id=None)`:

1. refuses when `R1B_PINE_ANALYSIS_PERSISTENCE` is false (no query, no
   insert);
2. recomputes SHA-256 of the exact UTF-8 source bytes and requires equality
   with `source_artifact.content_sha256`;
3. optionally proves artifact ownership through the version → catalog owner
   chain (`ARTIFACT_OWNERSHIP_MISMATCH` otherwise);
4. runs the existing deterministic semantic pre-analyzer (no AI, no network);
5. recomputes the source hash after analysis and requires all three hashes
   (pre, post, analyzer-reported) to agree;
6. requires valid registry provenance (identity not `UNAVAILABLE`, 64-hex
   registry hash, non-empty analyzer version);
7. serializes the closed payload and canonicalizes it (sorted deduplicated
   arrays, sorted keys, compact separators, UTF-8, `allow_nan=False`, max
   64 KiB), then hashes it;
8. reuses the existing row when the exact provenance tuple already exists,
   otherwise inserts one immutable row inside a SAVEPOINT — a concurrent
   identical insert loses to the unique tuple and the winner row is
   returned;
9. never updates an existing row and exposes no update operation.

### Payload schema

The closed `analysis_payload` contains exactly:

```json
{
  "matched_capabilities": [],
  "temporal_classes": [],
  "blocker_codes": [],
  "disclosure_codes": [],
  "admin_review_points": []
}
```

Each array is sorted and deduplicated. `analysis_payload_sha256` is the
SHA-256 of the canonical compact JSON encoding. The analyzer feature vector
and the Pine source are never persisted.

## Integration point

The writer is called from exactly one place: the owner-scoped personal Pine
qualification flow (`personal_pine_service.validate_version` →
`_persist_semantic_provenance`), where the immutable
`StrategySourceArtifact` is already resolved and its hash already trusted.
It is not called from the private webhook, webhook router, execution worker,
execution router, PaperBroker, Dhan adapter, startup, scheduler or any
background loop.

- Flag off: no new query, no insert, API response byte-identical.
- Flag on: the provenance row is reused or inserted and the qualification
  response references only its ID (`semantic_analysis_id`); the full
  analysis payload is never exposed to a normal-user response.

## Failure handling

Writer failures raise `SemanticAnalysisPersistenceError` with one closed code
(`PERSISTENCE_DISABLED`, `INVALID_SOURCE`, `SOURCE_HASH_MISMATCH`,
`ARTIFACT_OWNERSHIP_MISMATCH`, `REGISTRY_PROVENANCE_INVALID`,
`PAYLOAD_TOO_LARGE`, `PERSISTENCE_UNAVAILABLE`). Raw database exceptions
never propagate and Pine source is never logged or echoed. In the
qualification flow a persistence failure aborts the whole validation
transaction with HTTP 503 `SEMANTIC_PROVENANCE_UNAVAILABLE`, so no
validation report is recorded and qualification is never falsely approved.
Webhook and execution behavior are unaffected in every failure mode.

## Immutability

All four models reject every ORM UPDATE via a `before_update` guard in
`app/db/models.py` (`immutable R1B evidence`). Future outcome/rejection
functionality inserts new rows; semantic reanalysis inserts a new row and may
point `supersedes_analysis_id` at the superseded one. Direct database updates
remain outside application guarantees, but no repository service exposes one.

## Secret exclusions

The decision table stores no credential plaintext, raw signal ID, comment,
raw request body, broker token, TOTP, quantity/lots, strike, expiry, risk
values or Pine source. The rejection table stores only hashed or
closed-vocabulary fields. The semantic writer accepts no credential, signal
ID, comment, webhook payload, user-provided safe_metadata, broker or
execution field. The R1B-0 secret-taint helpers remain unwired because the
three webhook-adjacent writers are not implemented in this phase; the
known-secret substring finding stays deferred to the phase that wires them.
The expectation updater exit code is unchanged in this phase.

## Tests

- `test_migration_metadata.py` — head `0015_r1b_persistence`, 20-char
  revision, lineage intact.
- `test_r1b_persistence_schema.py` — SQLite alembic upgrade/downgrade/upgrade
  cycle with existing-row readability, no-new-column and no-backfill proofs,
  index/unique presence, full CHECK/FK/cascade behavior for all four tables,
  ORM immutability.
- `test_r1b_persistence_flags.py` — defaults false, invalid values false,
  zero call sites for the three declared-only flags, analysis flag confined
  to the qualification flow, no router/frontend exposure, and dynamic
  zero-write proof across webhook flows with the flags both off and forced
  on.
- `test_pine_semantic_analysis_persistence.py` — the thirty writer proofs:
  exact provenance binding, double source-hash verification, canonical
  payload hashing and size bound, idempotent reuse, new rows per provenance
  change, supersedes behavior, concurrency, invalid registry, indeterminate/
  partial truthfulness, cross-owner refusal, safe error translation, no
  update API, no network imports, no source logging, and
  no-false-qualification on failure.
- PostgreSQL verification: 0014 → 0015 → 0014 → 0015 plus a fresh chain
  (results recorded in the completion report).

## Rollback

Revert the single R1B-1 commit and run `alembic downgrade 0014_verify_select`
(drops only the four empty evidence tables; `pine_semantic_analyses` rows,
if any were written under the flag, are evidence-only and safe to drop). No
data repair, no backfill, no feature-flag cleanup (all default false), no
frontend action.

## Remaining R1B-2 scope

Unimplemented and unauthorized until separate review: the canonical decision
writer, outcome writer and rejection writer with their webhook/worker
integration points, routing-result translation, historical backfill
(`provenance_kind = 'BACKFILL'`), retention for rejections, any signal-ledger
API, and wiring of the secret-taint helpers into ingress evidence.
