# NOVA Phase R1B-1 — Independent Persistence and Provenance Review

## 1. Executive verdict

**APPROVED_WITH_NON_BLOCKING_FINDINGS**

Migration 0015 is additive and reversible with zero drift across every
existing table over three PostgreSQL 18 downgrade/upgrade cycles. All four new
tables are evidence-only with no execution-path reader. The decision, outcome
and rejection writers do not exist — statically and dynamically proven, even
with every flag forced true. The semantic-analysis writer is owner-scoped,
source-bound, deterministic, idempotent, immutable at the application layer,
concurrency-safe under a genuine two-session race, and secret-safe. Every
forced failure fails closed with no false qualification. Four non-blocking
findings are recorded (section 21); none changes any safety outcome. The
branch is approved as the design baseline for R1B-2. This approval does not
authorize merge, deployment or R1B-2 implementation.

## 2. Reviewed commit and scope

- Implementation branch: `phase-r1b1-persistence-provenance`
- Reviewed HEAD: `731177a673fb1a20c995c9a0c1a0e704070316d4` (clean worktree)
- Diff range: `c711213..731177a` (parent of the single R1B-1 commit)
- Review branch: `phase-r1b1-persistence-review`
- Inputs reviewed: `NOVA_PHASE_R0_CODEBASE_VERIFICATION.md`,
  `NOVA_PHASE_R1A_CANONICAL_SIGNAL_FOUNDATION.md`,
  `NOVA_PHASE_R1B1_SCHEMA_AND_PROVENANCE.md`

Read-only review: all probes ran from a session scratchpad against throwaway
databases; the only repository change is this report.

## 3. Changed-file classification

10 files changed; none unexpected.

| Category | Files |
| --- | --- |
| MIGRATION | `backend/alembic/versions/20260718_0015_r1b_persistence.py` |
| ORM_MODEL | `backend/app/db/models.py` (four appended models + immutability guard only; no existing model touched) |
| CONFIGURATION | `backend/app/config.py` (four flags + validator wiring only) |
| ANALYSIS_WRITER | `backend/app/services/pine_semantic_analysis_persistence.py` |
| QUALIFICATION_INTEGRATION | `backend/app/services/personal_pine_service.py` (one import, one helper, `validate_version` response assembly only) |
| TEST | `test_migration_metadata.py` (head assertion updated to 0015), `test_pine_semantic_analysis_persistence.py`, `test_r1b_persistence_flags.py`, `test_r1b_persistence_schema.py` |
| DOCUMENTATION | `docs/pine/NOVA_PHASE_R1B1_SCHEMA_AND_PROVENANCE.md` |
| UNEXPECTED | none |

## 4. Migration lineage

**ADDITIVE_MIGRATION_CONFIRMED**

- `revision = "0015_r1b_persistence"` — exactly 20 characters, fits the
  deployed `alembic_version.version_num VARCHAR(32)`.
- `down_revision = "0014_verify_select"`; exactly one Alembic head
  (`alembic heads` → `0015_r1b_persistence`).
- Upgrade creates only the four tables and their indexes, in the required
  order (decisions → outcomes → rejections → analyses); downgrade drops them
  in exact reverse dependency order and nothing else.
- Static source check: no `add_column`, `alter_column`, `drop_column`,
  `execute(`, `INSERT`, `UPDATE`, `DELETE FROM`, or `bulk_insert` anywhere in
  the migration (enforced by a committed test).
- All names (tables, uniques, FKs, CHECKs, indexes) are explicit and stable.
- Fresh-chain determinism: because the 0001 baseline `create_all`s current
  models (recorded tech debt), a fresh chain reaches 0015 with the tables
  present; the inspector guard makes the upgrade a deterministic no-op there,
  while a genuinely upgraded database gets identical explicit DDL — proven
  equal by full-schema snapshots.
- Guard depth: the guard keys on table-name existence only; a same-named,
  structurally divergent table would be skipped. This is inherent to the
  pre-existing 0001 pattern (0014 behaves identically) and unreachable on the
  two supported paths; recorded as an accepted-pattern observation, not a
  defect.

## 5. Existing-schema preservation

Full before/after snapshots of **every** existing table (name, column set,
column types; not just a guarded subset) were taken on PostgreSQL 18 and
compared across three 0015→0014→0015 cycles: **zero drift**. Representative
pre-existing rows (StrategySignal, StrategyExecutionJob, WebhookEvent,
StrategySourceArtifact, StrategyInstance, StrategyInstancePosition,
PositionEvent) remained readable at every step. All four evidence tables held
zero rows after every upgrade — no backfill.

## 6. Four-table schema review

Constraint-by-constraint results (each row exercised on PostgreSQL 18; the
SQLite equivalents run in the committed schema tests):

| Table / constraint | Behavior | Result |
| --- | --- | --- |
| decisions `uq_canonical_decision_signal` | second decision for a signal rejected | PASS |
| decisions `fk_canonical_decision_instance_owner` | cross-owner (user B + user A's instance) rejected | PASS |
| decisions closed values (action/event/state/reason/compat/provenance) | out-of-set values rejected | PASS |
| decisions HOLD/BUY_CE/BUY_PE/EXIT cross-field CHECKs | each inconsistent shape rejected; all four valid shapes accepted | PASS |
| decisions `ck_…_payload_fp_hash` | uppercase and 63-char values rejected | PASS |
| decisions column audit | no credential, Pine source, broker token, TOTP, quantity, lots, strike, expiry, risk or raw-body column exists | PASS |
| outcomes `uq_canonical_outcome_idempotency` | duplicate key rejected | PASS |
| outcomes `ck_…_attempt_positive` | 0 and −1 rejected, NULL allowed | PASS |
| outcomes closed phase/state/routing/no-op/exit sets | out-of-set rejected | PASS |
| outcomes FKs | decision CASCADE, execution-job SET NULL | PASS |
| rejections `uq_signal_rejection_dedupe` | duplicate dedupe key rejected | PASS |
| rejections closed stage/code sets | out-of-set rejected | PASS |
| rejections hash CHECKs (3 columns) | wrong case/length rejected | PASS |
| rejections composite owner FK | cross-owner rejected | PASS |
| analyses `uq_pine_analysis_provenance` | duplicate 7-tuple rejected; any changed member accepted as a new row | PASS |
| analyses hash CHECKs (3 columns) | wrong case/length rejected; 65-char value rejected by PostgreSQL `CHAR(64)` (DataError, no silent truncation) | PASS |
| analyses closed level/confidence sets | out-of-set rejected | PASS |
| analyses artifact FK CASCADE / supersedes SET NULL | verified (section 16) | PASS |

Total on PostgreSQL: 5 valid rows accepted, 36 invalid rows rejected.

## 7. Authority-boundary proof

**EVIDENCE_ONLY_NOT_EXECUTION_AUTHORITY**

A repository-wide search for the four table names and four model names finds
exactly: `models.py`, the 0015 migration, `pine_semantic_analysis_persistence.py`
and the three R1B test files (plus a name-substring false positive: the
pre-existing `PineSemanticAnalysisResult` dataclass in
`pine_semantic_preanalyzer.py`, which is the in-memory analyzer result, not
the ORM model). None of: private webhook auth, replay/dedup, execution jobs,
execution router, risk manager, PaperBroker, Dhan adapter, contract
resolution, position reconciliation, position JSON storage, verification
readiness, engine selection, EOD square-off or the option monitor references
any evidence table, so no evidence result can influence acceptance, replay,
action, CE/PE, quantity, mode, orders, positions or READY status.

## 8. Feature-flag proof

All four `R1B_*` flags: pydantic default `False`, missing → `False`, empty →
`False`, invalid (`"banana"`, `None`, `3.5`, `[]`, `{}`) → `False` via the
shared fail-safe validator. Server-side only: no router, request schema or
frontend source references any flag (tested), and webhook metadata cannot
reach them. Static grep over all runtime sources: the three declared-only
flags appear nowhere outside `config.py`; the analysis flag appears only in
`personal_pine_service.py`, `pine_semantic_analysis_persistence.py` (and a
`models.py` docstring). Dynamic proof in section 15.

## 9. Semantic provenance proof

All 22 flow requirements verified in the writer
(`persist_semantic_analysis`): flag gate before any DB access; UTF-8 source
hash equality with `artifact.content_sha256`; optional owner-chain check;
deterministic pre-analyzer; post-analysis re-hash plus analyzer-reported hash
agreement (three-way); registry identity/hash validity (analyzer fail-closed
`UNAVAILABLE` provenance is refused and writes nothing); closed 5-list
payload, sorted, deduplicated, sorted keys, compact separators, UTF-8,
`allow_nan=False`, ≤ 64 KiB; payload SHA-256 recomputed; exact-tuple reuse;
new row per changed analyzer version, registry SHA, or schema version;
supersession inserts with `supersedes_analysis_id`; concurrent duplicates
resolve to one winner. Provenance tuple is exactly the seven required
components, and each was independently varied to produce a distinct row.

## 10. Transaction and concurrency proof

- The insert runs inside a SAVEPOINT within the caller's qualification
  transaction; the writer never commits the outer transaction.
- Genuine concurrency was tested on PostgreSQL 18 with two real sessions in
  two threads racing the same insert through a barrier: the loser's
  unique-violation resolved to the winner's row; final count 1; both callers
  received the same row ID.
- A losing insert does not abort the outer transaction (savepoint rollback
  only); the winner row is re-selected through the session.
- Non-unique database errors are translated to the closed
  `PERSISTENCE_UNAVAILABLE` (never re-raised raw) — with one gap outside the
  translation envelope recorded as finding 1.
- Repeated calls are idempotent (same row ID, count stays 1) on both engines.
- A failed analysis insert aborts validation before the report insert, and a
  later report failure rolls back the same transaction — no committed report
  without provenance and no orphan analysis from the same transaction (a
  reused analysis row from an earlier committed call is by-design durable
  evidence).
- Rollback behavior was exercised on PostgreSQL and SQLite with identical
  outcomes.

## 11. Owner/source binding

**SOURCE_AND_OWNER_BINDING_CONFIRMED**

Qualification resolves the version through `_owned_version` (owner + catalog
scoped, locked) and the artifact through `_artifact` (version-scoped); the
writer independently re-verifies the version → catalog owner chain and
refuses a foreign owner (`ARTIFACT_OWNERSHIP_MISMATCH`, proven). Hash
mismatch, empty and non-string sources fail closed. The source cannot be
substituted after the initial hash: the same immutable string is hashed
before analysis, after analysis, and compared to the analyzer-reported hash —
a simulated analyzer-side substitution was detected and refused. No fixture
fallback exists; no production runtime file imports test fixtures (swept).

## 12. Failure handling

Forced failure matrix (API-level via the validate endpoint, service-level via
session proxies):

| Forced failure | Result |
| --- | --- |
| database unavailable (provenance query) | closed 503 `SEMANTIC_PROVENANCE_UNAVAILABLE`; no DB internals in response |
| database unavailable (owner query) | **finding 1**: raw `OperationalError` escapes the writer → generic 500; transactionally still fails closed (no analysis, no report, version stays `draft`) |
| unique conflict (concurrent duplicate) | resolved to winner row, no error |
| non-unique integrity error | `PERSISTENCE_UNAVAILABLE` |
| registry unavailable / invalid registry hash | `REGISTRY_PROVENANCE_INVALID`, zero rows |
| payload too large | `PAYLOAD_TOO_LARGE` (bound enforced before insert) |
| source hash mismatch (both directions) | `SOURCE_HASH_MISMATCH`, zero rows |
| owner mismatch | `ARTIFACT_OWNERSHIP_MISMATCH`, zero rows |
| analyzer exception (injected) | **finding 2**: propagates unwrapped → generic 500; zero analysis rows, zero reports, version stays `draft`, injected secret text absent from the response |

In every case: no validation report committed, no false approval, no READY
advancement, and the webhook/execution paths cannot reach these errors (the
writer is unreachable from them — section 7).

## 13. Secret/source exclusion

**SECRET_AND_SOURCE_EXCLUSION_CONFIRMED**

The writer contains no `logger`, `print(` or traceback rendering; error
objects carry only closed codes. Pine source, registry content, filesystem
paths, credentials and tokens cannot appear in any writer error (probed with
a distinctive source token and an injected `nwk_` string under caplog —
absent from logs and responses). The database receives only: hashes,
closed-vocabulary strings, versions, timestamps and the bounded closed
payload — no credential (plain or hashed), broker token, TOTP, webhook URL,
raw body, raw hostile metadata, Pine source, feature vector, or owner email
column exists in any of the four tables.

## 14. Qualification integration

Flag off: no analysis query, no insert, response byte-identical (no
`semantic_analysis_id` key), validation behavior unchanged (64 Pine
regression tests green). Flag on: only the owner-scoped immutable artifact is
analyzed; the response references the row by ID only; the full
`analysis_payload` never appears in any API response; persistence failure
aborts validation with 503 and records nothing; status advances only through
the pre-existing validation logic (no admin approval implied). Manual package
generation, prompt selection, frozen transport, TradingView setup,
verification mode, private webhook and execution are untouched (diff +
regression proof).

## 15. Zero-write proof

Static: zero decision/outcome/rejection writer functions exist anywhere in
runtime code; their flags have zero call sites; exactly one analysis-writer
call site exists (`validate_version` → `_persist_semantic_provenance`). The
static proof is exhaustive over all flows (worker, paper execution,
verification mode included) because absent code cannot execute.

Dynamic (review probe + committed tests), with all flags false and all flags
forced true: HOLD, BUY_CE, BUY_PE, EXIT, exact duplicate, conflicting
duplicate (409), invalid action (422), stale signal (422), paused instance —
after all flows, `canonical_signal_decisions = 0`,
`canonical_signal_outcomes = 0`, `strategy_signal_rejections = 0`, and no
webhook test wrote `pine_semantic_analyses` (0 rows).

## 16. PostgreSQL verification

Disposable `postgres:18-alpine` container (created for this review, removed
after): fresh chain → head `0015_r1b_persistence`; representative
pre-existing rows inserted; three full downgrade/upgrade cycles with
full-schema snapshots and row readability at every step; 36 invalid /
5 valid constraint matrix; composite owner FKs; WebhookEvent SET NULL,
StrategySignal→decision→outcome CASCADE, artifact→analysis CASCADE,
supersedes SET NULL; semantic writer persist + idempotent reuse; genuine
two-session concurrent insert → one winner. All pass.

## 17. SQLite verification

The committed `test_r1b_persistence_schema.py` migration test performs the
supported SQLite cycle (upgrade → downgrade → upgrade) with column snapshots,
index/unique verification, row readability and empty-table assertions; the
constraint and deletion tests run under `PRAGMA foreign_keys=ON`. 62 tests
pass. SQLite's lax `VARCHAR`/`CHAR` length behavior is compensated by the
PostgreSQL leg (finding 3 context).

## 18. Scope-creep result

**SCOPE_CLEAN.** The diff touches no webhook schema, replay identity,
credential resolution, execution router, risk manager, PaperBroker, Dhan
adapter, contract resolution, lots/quantity logic, position JSON authority,
reconciliation, verification mode, engine selection, prompt (V2/V3/V3.1),
transport (V1/V2) or frontend runtime file. The only runtime files changed
are the four authorized ones (models, config, writer, qualification
integration).

## 19. Test results

At `731177a` on the review branch:

| Suite | Result |
| --- | --- |
| R1B schema/flags/writer + migration metadata | 62 passed |
| Canonical/analyzer (adapter, shadow, registry, preanalyzer) | 108 passed |
| Pine regressions (personal, conversion, prompt v3, transport v2, v3.1 binding, TradingView setup) | 64 passed |
| Webhook + execution | 81 passed |
| Full backend (`app/tests -q`) | 927 passed |
| Review probes (scratchpad, read-only) | 6 passed |
| PostgreSQL 18 review script | ALL PASS |
| `alembic heads` | `0015_r1b_persistence` |
| Frontend `tsc -b --noEmit` / vitest / build | clean / 20 passed / success |
| `git diff --check` | clean |

## 20. Blocking findings

None.

## 21. Non-blocking findings

1. **Owner-check query sits outside the writer's error-translation envelope.**
   - File: `backend/app/services/pine_semantic_analysis_persistence.py`
   - Function: `persist_semantic_analysis` → `_verify_owner` (called before
     the `try:` that translates `SQLAlchemyError`), lines ≈ 74–86 / 118–121
   - Observed: a database failure during the owner-chain SELECT escapes as a
     raw `OperationalError` instead of `PERSISTENCE_UNAVAILABLE`; the API
     yields a generic 500 rather than the closed 503. Transactionally it
     still fails closed (proven: zero analysis rows, zero reports, version
     stays `draft`; no DB detail in the HTTP response).
   - Risk: low — qualification-only path, flag-gated off by default, fails
     safe. Violates the "raw database exceptions never leave the writer"
     guarantee on one query.
   - Verdict: non-blocking. R1B-2 should move `_verify_owner` inside the
     translation envelope (one-line change) before wiring further writers.
2. **Analyzer exceptions are not wrapped.** Same file, step 4 of the flow: a
   hypothetical raise from `analyze_source` propagates unwrapped (analyzer is
   designed to fail closed and hostile-source probes confirm it does not
   raise today). Forced-injection probe confirms fail-closed transactional
   behavior and no leakage of injected secret text into the response.
   Non-blocking; recommend a generic exception envelope in R1B-2.
3. **Hex character set is not database-enforced** (carried from
   implementation, documented there): a 64-char lowercase non-hex value
   passes the portable CHECK (demonstrated on PostgreSQL). Every application
   writer is closed and stores only `hexdigest()` output; no alternate
   runtime writer exists. Classified NON_BLOCKING per the review rule. A
   PostgreSQL-specific supplemental regex CHECK is **recommended** for a
   later hardening migration; not implemented in this review.
4. **SQLAlchemy Core `update()` bypasses the ORM `before_update` guard**
   (inherent to mapper events). No repository service issues Core updates
   against evidence models (swept), and direct SQL is already documented as
   outside application guarantees. Immutability verdict is therefore
   **APPLICATION_IMMUTABILITY_CONFIRMED** — database-level immutability
   (triggers/permissions) is *not* claimed. ORM immutability was verified for
   scalar, JSON-payload, FK and relationship-driven updates on all four
   models; inserts and intentional cascades remain possible.

## 22. R1B-2 readiness decision

R1B-1 is approved as the R1B-2 design baseline. R1B-2 design may begin;
implementation remains unauthorized until its own review. R1B-2 must:
address findings 1–2 in the writer before or alongside new writers; consider
the finding-3 supplemental PostgreSQL CHECK in its hardening migration; and
design the decision/outcome/rejection writers so that evidence failures can
never alter signal acceptance, replay, job creation or execution — the same
isolation proven here for the semantic writer.
