# NOVA Phase R1B-2A — Independent Evidence Writer Review

## 1. Executive verdict

**APPROVED_FOR_R1B2B_HOLD_ONLY_DESIGN**

The three writer libraries are insert-only, deterministically idempotent,
owner- and reference-safe, secret- and source-safe, transaction-safe,
concurrency-safe (proven with genuine PostgreSQL 18 races, including a
conflicting race this review added), closed-error-safe, and completely
disconnected from every production flow — statically, dynamically and with
all flags forced true. The caller-hash trust boundary singled out for
scrutiny was audited input-by-input: every writer-owned key is internally
generated with domain separation, no signature accepts any provenance hash
or final key, and the only two caller-provided hashes are shape-validated
reference fields that can never impersonate provenance or reuse evidence
(section 7). The semantic-writer envelope fixes close review findings 1 and
2 exactly. Three non-blocking observations are recorded (section 22).

This approval authorizes only R1B-2B HOLD-only integration design or
implementation under a new explicit prompt. It does not authorize
trading-action decision integration, outcome integration, rejection
integration, merge or deployment.

## 2. Reviewed commit and scope

- Implementation branch: `phase-r1b2a-evidence-writer-libraries`
- Reviewed HEAD: `e61c7abf340bdb412cd11a19a1b7586c85d61592` (clean worktree)
- Diff range: `5eb9275..e61c7ab` (single implementation commit)
- Review branch: `phase-r1b2a-writer-review`
- Inputs reviewed: the R1B-2 design, R1B-2A library doc, R1B-1 schema doc
  and R1B-1 persistence review.

Read-only review: probes ran from the session scratchpad against throwaway
databases; the only repository change is this report.

## 3. Changed-file classification

14 files; none unexpected; zero production-path changes (grep over the diff
for webhook/worker/router/broker/state/position/alembic/frontend/prompt/
transport paths: no matches).

| Category | Files |
| --- | --- |
| WRITER_LIBRARY | `canonical_signal_decision_persistence.py`, `canonical_signal_outcome_persistence.py`, `strategy_signal_rejection_persistence.py` |
| SAFETY_UTILITY | `r1b_evidence_safety.py` |
| SEMANTIC_ENVELOPE_FIX | `pine_semantic_analysis_persistence.py` (23-line envelope-only diff) |
| TEST | 5 new writer/static test files + `test_pine_semantic_analysis_persistence.py` (3 additive envelope tests) + `test_r1b_persistence_flags.py` (flag-confinement update caused by the new writers) |
| POSTGRESQL_PROBE | `scripts/r1b2a_pg_verify.py` |
| DOCUMENTATION | `NOVA_PHASE_R1B2A_EVIDENCE_WRITER_LIBRARIES.md` |
| UNEXPECTED | none |

## 4. Zero-call-site proof

**ZERO_PRODUCTION_CALL_SITES_CONFIRMED**

- Repository-wide search: the three `persist_*` functions appear only in
  their own modules, the five test files and the PostgreSQL script.
- The committed static suite (`test_r1b2a_zero_call_sites.py`) checks every
  runtime file for the function names, module names and flags, and spot-
  checks the sensitive files (webhook service/router/execution, worker,
  router, risk manager, PaperBroker, Dhan client, state store, reconciler,
  monitor, EOD, main/startup) by name.
- No dynamic imports: `importlib`/`__import__`/string-resolution of writer
  names does not occur anywhere in runtime code (swept).
- Feature flags appear only in `config.py` and each flag's own writer.
- No evidence table/model is referenced by any execution service.
- Dynamic: the committed zero-write suite runs HOLD/entries/duplicates/
  conflicts/invalid/stale/paused with **all flags forced true** and asserts
  all evidence tables remain empty — re-executed green in this review.

## 5. Feature-flag proof

Defaults false; missing/empty/invalid resolve false via the shared fail-safe
validator (re-verified by the committed parametrized tests); no API,
webhook or frontend exposure (swept). Each disabled writer raises
`PERSISTENCE_DISABLED` **before any session attribute access** — the
committed tests hand each writer an exploding object whose every attribute
access raises, so no SELECT, no INSERT and no nested transaction can occur,
and disabled paths log nothing.

## 6. Shared safety utility

`r1b_evidence_safety.py`: SHA-256 via `hashlib.sha256(...).hexdigest()`
only; `evidence_key` uses deterministic `|`-joined UTF-8 canonical encoding
with a namespace domain separator; no JSON/NaN handling exists in the
module (no `allow_nan` anywhere); `translate_db_error` deletes the raw
exception and returns only a closed code (no text retained); closed-value
and identifier validation bound lengths; `reject_tainted` runs before any
persistence; taint errors never echo the value; the module has **no**
logging call at all; imports are stdlib + SQLAlchemy exceptions +
`app.domain.secret_taint` — no FastAPI/request/router/broker/Pine/Dhan/
state-store import (AST-verified). A utility failure raises a closed error
in a library with zero call sites: it cannot execute or mutate trading
state.

## 7. Caller-hash trust analysis

Exhaustive inventory of every function parameter that accepts a hash,
fingerprint or SHA-like string:

| Input | Writer | Classification | Fate |
| --- | --- | --- | --- |
| `payload_fingerprint` | decision | SHAPE_VALIDATED_UNTRUSTED → CALLER_REFERENCE_ONLY | `require_hex64` shape check; stored verbatim in the reference column `payload_fingerprint`; part of the conflict-comparison tuple; **never** used in any key. Probe: an attacker-selected 64-hex value on the same signal raises `CANONICAL_DECISION_CONFLICT` and leaves the original row intact — it cannot impersonate or reuse evidence. |
| `request_fingerprint` | rejection | SHAPE_VALIDATED_UNTRUSTED → CALLER_REFERENCE_ONLY | `require_hex64`; stored as reference; **rehashed** into the writer-owned dedupe key through `evidence_key` with the `nova.r1b2.rejection.v1` domain separator (probe: `row.dedupe_key != fingerprint` and equals the internal recomputation). |
| `known_secrets` | rejection | not a hash — request-local taint set | used only inside `is_credential_shaped`; never stored. |
| outcome `idempotency_key` | — | INTERNALLY_GENERATED_TRUSTED_HASH | no parameter exists; generated by `outcome_idempotency_key`. |
| rejection `dedupe_key` | — | INTERNALLY_GENERATED_TRUSTED_HASH | no parameter exists. |
| rejection `signal_id_sha256` | — | INTERNALLY_GENERATED_TRUSTED_HASH | computed by `sha256_text` from the (taint-screened) signal id. |
| semantic `source_sha256`/`registry_sha256`/`analysis_payload_sha256` | — | INTERNALLY_GENERATED_TRUSTED_HASH | computed and cross-verified inside the writer; unchanged in R1B-2A. |
| PROHIBITED_TRUST_INPUT | — | **none exist** | signature introspection (probe) proves no writer accepts `event_type`, `desired_state`, compatibility fields, `idempotency_key`, `dedupe_key`, `source_sha256`, `registry_sha256`, `analysis_payload_sha256`, `payload_sha256` or any metadata-authority parameter. |

Conclusion: caller-provided hashes never become trusted provenance and
never become final keys without internal domain-separated recomputation;
a shape-valid attacker string cannot impersonate any evidence hash.

## 8. Decision writer

All Part-5 requirements verified (code + 19 dynamic tests): trusted-entity
signature only; the four-action mapping is computed from the closed inverse
of the adapter table and re-validated by the DB cross-field CHECKs; signal↔
instance (`instance:{id}`), owner↔instance, event↔ingress context
(`instance-webhook:{id}` + event id), signal-id↔canonical-event and
adapter-version checks all fail closed with the right codes;
`provenance_kind` accepts only the real CHECK vocabulary (`LIVE`/`BACKFILL`
— the draft's `REALTIME` is rejected, tested); no decision can precede its
signal (FK + persisted-entity requirement); one decision per signal;
identical call returns the existing row; conflicts raise
`CANONICAL_DECISION_CONFLICT` without update (verified same-session,
race-simulated, and in a genuine concurrent conflicting race on PostgreSQL
added by this review: exactly one row, loser gets the conflict code);
unrelated IntegrityError → `PERSISTENCE_UNAVAILABLE`; the writer never
commits the caller's transaction (rollback-erases-evidence test).

## 9. Outcome writer

All Part-6 requirements verified (20 dynamic tests): inputs are exactly the
0015 phase/state/result/no-op/exit sets plus the closed 28-value
`safe_detail` vocabulary; arbitrary dicts, broker bodies, exception text,
order/risk fields are unrepresentable (no such parameters; free text in
`safe_detail` → `INVALID_INPUT`); decision must be persisted; a job must
belong to the decision's signal (`FOREIGN_REFERENCE`); attempt must be a
positive non-bool int; no `provenance_kind` parameter exists (column
absent). Idempotency key matches the required construction exactly with
unambiguous `|` separation and `NONE` placeholders; six-way distinctness
(phase/attempt/result/detail/job-absent variants) and reversal-step
distinctness proven; concurrent identical inserts → one winner (PG);
FK-violation → `PERSISTENCE_UNAVAILABLE`, not idempotent reuse (SQLite with
FK pragma and PG natively, this review); the writer imports no router,
broker, state store or worker module (AST), so it cannot call execution,
touch JSON position state, retry anything or change a job; caller
transaction ownership proven by rollback test.

## 10. Rejection writer

All Part-7 requirements verified (20 dynamic tests): trusted instance +
matching owner are mandatory (`OWNER_INSTANCE_MISMATCH`); there is no
parameter through which credential plaintext, raw bodies, headers, emails,
Pine source, exception text or comments could arrive; stages/codes are the
0015 closed sets; timestamps are normalized to UTC with a
timezone-deterministic daily bucket (IST/UTC probe); no `provenance_kind`
parameter. Dedupe key follows the required construction; the caller cannot
supply it; same-request retries collapse; conflicting duplicates dedupe per
conflicting fingerprint; no-signal-ID cases stay bounded per
instance/stage/code/day; credential-shaped and known-secret signal IDs
suppress the whole row (`TAINT_DETECTED`) without echoing the value
anywhere; suppression is an exception in a zero-call-site library — it
cannot affect any authoritative response today, and the R1B-2D call site
contract (error constructed first, re-raised unchanged) is already fixed in
the design.

## 11. Insert-only proof

**INSERT_ONLY_APPLICATION_GUARANTEE_CONFIRMED**

No `update(`/`delete(`/`merge(`/`upsert`/`Query.update`/`Query.delete`/
`session.execute(update|delete` exists in any writer library (static test +
review grep); writers expose no update/delete/upsert/merge API (public-name
scan); existing rows are only selected and returned; conflicts never mutate
prior evidence (probed); the ORM `before_update` guard remains active
(scalar/JSON/FK/relationship mutations all raise — R1B-1 suite re-ran
green, plus the R1B-2A guard test); Core-update bypass remains outside
application guarantees, unused by runtime services, and the static test
fails if Core update/delete is later added against any evidence model.
Database-trigger immutability is NOT claimed.

## 12. Transaction ownership

No writer contains `session.commit()` (grep) or commits the outer
transaction. SAVEPOINTs (`begin_nested`) recover unique races; a losing
insert does not poison the outer session (probe: the same session performs
a subsequent valid write); outer rollback removes fresh evidence; outer
commit makes it durable (probe); non-unique failures surface as
`PERSISTENCE_UNAVAILABLE` (never swallowed); post-conflict lookups re-select
by the writer-owned natural key only; the writers take no locks on
execution tables (FK checks only) and cannot deadlock with execution code
because no production call site exists. All eight required scenarios were
exercised: insert+rollback, insert+commit, concurrent identical, concurrent
conflicting, foreign FK, unrelated integrity failure, session already in a
transaction (every test runs inside `session_scope`'s open transaction),
nested-SAVEPOINT failure with continued session use.

## 13. Semantic envelope fixes

Finding 1: the owner-chain query now sits inside a
`SQLAlchemyError → PERSISTENCE_UNAVAILABLE` envelope; a forced
OperationalError yields the closed code with `__cause__` stripped, no DB
text, and end-to-end the API returns 503 `SEMANTIC_PROVENANCE_UNAVAILABLE`
with zero analysis rows, zero reports and the version still `draft`.
Finding 2: an injected analyzer raise becomes `ANALYSIS_UNAVAILABLE`; the
injected path and credential text are absent from the error. The diff is
envelope-only: successful-path hashing, payload canonicalization and
provenance identity are untouched (all 27 pre-existing provenance tests
pass unmodified).

## 14. Error contract

Closed families `CanonicalDecisionPersistenceError` /
`CanonicalOutcomePersistenceError` / `SignalRejectionPersistenceError` +
`SemanticAnalysisPersistenceError`; observed codes: `PERSISTENCE_DISABLED`,
`INVALID_INPUT`, `OWNER_INSTANCE_MISMATCH`, `FOREIGN_REFERENCE`,
`CANONICAL_DECISION_CONFLICT`, `TAINT_DETECTED`, `PERSISTENCE_UNAVAILABLE`,
`ANALYSIS_UNAVAILABLE` (plus the pre-existing semantic codes;
`PAYLOAD_TOO_LARGE` remains semantic-writer-only since the three new
writers accept no unbounded payloads). Error messages are the code string
itself — no interpolation of input, SQL, constraint names, paths or
secrets; chaining is suppressed (`from None`, `__cause__ is None`
asserted); unique conflicts are distinguished from unrelated failures by
winner re-selection.

## 15. Secret/source exclusion

**SECRET_AND_SOURCE_EXCLUSION_CONFIRMED**

A 14-value injection matrix (nwk_ credential, hash-like, OAuth token, TOTP,
Authorization header, webhook URL, raw JSON, Pine source, Windows/Linux
paths, database URL, email, broker client ID, SQL error text) was pushed
through every free-text channel the writers expose (rejection `signal_id`
with request-local `known_secrets`; decision metadata via the canonical
event). Credential-shaped and known-secret values suppressed their rows
(`TAINT_DETECTED`, zero rows); the decision metadata channel kept only the
closed keys (the injected raw-JSON comment and credential-shaped
strategy_version vanished); no injected value appeared in rows, exception
strings, logs (caplog) or captured output (capsys). Tainted evidence
produced zero rows.

## 16. PostgreSQL concurrency

`scripts/r1b2a_pg_verify.py` was inspected line-by-line and independently
re-run on a fresh disposable `postgres:18-alpine` container: it genuinely
uses separate sessions from the session factory, separate threads, a
`threading.Barrier`, simultaneous unique-key races per writer, committed
winners, loser recovery to the same row ID, and a final count of one; the
container was removed afterwards. This review added two PG probes beyond
the script: a genuine **conflicting** concurrent decision race (exactly one
row persisted; the loser receives `CANONICAL_DECISION_CONFLICT`, not a
silent reuse) and a native-FK unrelated IntegrityError (classified
`PERSISTENCE_UNAVAILABLE`). All pass.

## 17. SQLite/PostgreSQL parity

Valid insert, disabled zero-access, reuse, conflict, owner mismatch,
foreign reference, closed-value rejection, taint suppression, outer
rollback and ORM immutability were verified on both dialects (SQLite via
the committed suites, PostgreSQL via the scripts). Honest limitations:
SQLite "concurrency" in the unit tests is a simulated race
(`_RaceSession` forcing the insert path); genuine multi-session racing is
proven only on PostgreSQL, which is the authoritative result. SQLite does
not enforce VARCHAR/CHAR widths or FKs without the pragma; width behavior
is covered by the PostgreSQL leg and the R1B-1 findings.

## 18. Test quality

103 R1B-2A-group tests, all executing real writer code against real
databases (mu_db SQLite; PG via scripts); no mock replaces writer logic —
the only stand-ins are failure injectors (exploding/dead/race session
proxies) that wrap the real session. Assertions inspect rows, row counts,
exception codes, `__cause__`, transaction visibility and captured output.

| File | Tests | Character |
| --- | --- | --- |
| test_canonical_signal_decision_persistence.py | 19 | dynamic; rows/exceptions/transactions; race simulated (PG authoritative) |
| test_canonical_signal_outcome_persistence.py | 20 | dynamic; includes FK-pragma unrelated-integrity case + AST import test |
| test_strategy_signal_rejection_persistence.py | 20 | dynamic; dedupe/taint matrices; capsys leak check |
| test_r1b2a_zero_call_sites.py | 4 | static — allowed categories only (call sites, imports, flags, table reads) |
| test_r1b2a_insert_only.py | 3 | 2 static (allowed: Core update/delete, API absence) + 1 dynamic ORM-guard |
| test_pine_semantic_analysis_persistence.py | 30 (3 new) | dynamic incl. API-level envelope proofs |
| test_r1b_persistence_flags.py | 26 | mixed: static confinement (allowed) + dynamic zero-write with flags forced true |

No tautological tests found. One borderline source-text assertion
(`test_migration_source_never_touches_existing_tables`, pre-existing from
R1B-1) is static-by-design and acceptable. Runtime behavior is everywhere
proven dynamically.

## 19. Regression results

At `e61c7ab` on the review branch: R1B-2A group 103 passed; R1B
schema/metadata 28 passed; webhook + execution 81 passed;
canonical/analyzer 108 passed; Pine regressions 64 passed; full backend
996 passed; review probes 7 passed; PG script + review PG
probes ALL PASS; `alembic heads` → `0015_r1b_persistence`; frontend tsc
clean / vitest 20 passed / build success; `git diff --check` clean. (One
full-suite run aborted with a collection MemoryError while three pytest
processes ran concurrently; the isolated rerun passed cleanly — environment
contention, not a code issue.)

## 20. Scope-creep result

**SCOPE_CLEAN.** The diff contains no change to any webhook, worker,
router, risk, broker, Dhan, position, contract-resolution, verification,
engine-selection, frontend, prompt, transport or Alembic file. The only
runtime-adjacent edits are the four new zero-call-site libraries and the
envelope-only semantic-writer fix.

## 21. Blocking findings

None.

## 22. Non-blocking findings

1. **Decision `payload_fingerprint` is stored verbatim from the caller.**
   - `canonical_signal_decision_persistence.py`,
     `persist_canonical_signal_decision`, fingerprint validation ≈ line 118.
   - Observed: the ingress fingerprint cannot be recomputed by the writer
     (that would require the forbidden raw payload), so it is shape-validated
     and stored as a reference; it participates in conflict detection but in
     no key. Risk: negligible today (zero call sites; the only designed
     caller computes it server-side at `private_webhook_service.py:139-155`).
   - Verdict: acceptable; the R1B-2B review must confirm the call site
     passes exactly the ingress-computed fingerprint.
2. **`signal_id_safe` may store arbitrary printable user text (≤128).**
   - `strategy_signal_rejection_persistence.py`, signal-id handling.
   - Observed: after credential/known-secret/control screening, a
     user-chosen signal id containing e.g. an email-shaped or URL-shaped
     string is stored as-is — this is the schema's design for the column,
     but worth an explicit R1B-2D policy decision (store hash-only vs
     projected text) before the rejection writer is wired.
   - Verdict: non-blocking design note for R1B-2D.
3. **Full-suite MemoryError under parallel pytest runs.** Environment
   contention on this workstation when three suites run concurrently;
   isolated reruns pass. Operational note only.

## 23. R1B-2B readiness

Approved for **HOLD-only** decision-evidence integration design/
implementation under a new explicit prompt: call site after the
authoritative HOLD transaction commits in `_persist_hold`, behind
`R1B_CANONICAL_DECISION_PERSISTENCE` (default false), best-effort with
metric-only failure, byte-identical webhook responses, no execution job,
and the finding-1 fingerprint provenance check above. BUY_CE/BUY_PE/EXIT
decisions, worker outcomes and rejection evidence remain disconnected
pending their own reviews.
