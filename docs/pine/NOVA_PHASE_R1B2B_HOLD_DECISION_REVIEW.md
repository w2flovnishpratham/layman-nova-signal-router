# NOVA Phase R1B-2B — Independent HOLD Decision Integration Review

## 1. Executive verdict

**APPROVED_FOR_NEXT_R1B_DESIGN_GATE**

The HOLD-only canonical decision integration is correctly placed after the
true authoritative commit boundary, uses a completely separate evidence
session, takes its fingerprint exclusively from the committed WebhookEvent
metadata, is gated to exactly the fresh replay path, and is invisible to the
webhook caller under every forced failure. Duplicates never repair missing
evidence. Trading actions, outcomes and rejections remain disconnected.
HOLD still creates zero execution jobs. All required regressions pass,
including independent PostgreSQL 18 concurrency verification. Two
non-blocking observations are recorded (section 22). This approval closes
R1B-2B and permits only the selection of the next separately designed R1B
phase — it does not authorize BUY_CE/BUY_PE/EXIT decisions, outcomes,
rejections, backfill, merge, deployment, live enablement or AI conversion.

## 2. Reviewed commit and scope

- Implementation branch: `phase-r1b2b-hold-decision-integration`
- Reviewed HEAD: `ebc7b3e26def857221aa2f703be47e831429ae67` (clean worktree)
- Diff range: `c49127b..ebc7b3e` (single implementation commit)
- Review branch: `phase-r1b2b-hold-decision-review`
- Inputs reviewed: R1B-2 design, R1B-2A library doc, R1B-2A writer review,
  R1B-2B integration doc.

Read-only review; the only repository change is this report.

## 3. Changed-file classification

12 files; none unexpected once the three runtime-adjacent edits are
explained:

| Category | Files |
| --- | --- |
| HOLD_RUNTIME_INTEGRATION | `private_webhook_service.py` (1 import + 15-line post-commit fresh-HOLD block in `ingest`); `webhook_replay_store.py` (**additive only**: the fresh-claim return dict gains `webhook_event_id: str(row.id)` — provider/event-id/`raw_body_sha256` identity construction is byte-identical, duplicate/tampered branches untouched); `config.py` (comment-only rewording of the flag block); `canonical_signal_decision_persistence.py` (docstring-only R1B-2B boundary update) |
| EVIDENCE_HELPER | `hold_canonical_decision_evidence.py` (140 lines, new) |
| TEST | `test_r1b2b_hold_decision_integration.py`, `test_r1b2b_hold_decision_failure_isolation.py`, `test_r1b2b_runtime_call_sites.py` (new); `test_r1b2a_zero_call_sites.py`, `test_r1b_persistence_flags.py` (allowlists extended for the one authorized helper — a change caused by this branch, verified line-by-line) |
| POSTGRESQL_PROBE | `scripts/r1b2b_pg_verify.py` (new) |
| DOCUMENTATION | `NOVA_PHASE_R1B2B_HOLD_DECISION_INTEGRATION.md` |
| UNEXPECTED | none |

No change to request schemas, credential resolution, replay identity
construction, `private_webhook_execution.py`, worker, router, risk manager,
brokers, position code, contract resolution, verification readiness, DB
models, migrations, prompts, transports or frontend (diff-name sweep: only
the files above).

## 4. Authoritative commit boundary

**POST_COMMIT_ORDERING_CONFIRMED**

Verified against the real transaction structure:

1. Replay claim (`webhook_replay_store.claim_webhook_event`) commits and
   closes its own `session_scope` before returning `{"status": "fresh",
   ..., "webhook_event_id"}` (store lines 72-92).
2. `_persist_hold` (`private_webhook_service.py:505-542`) opens its own
   `session_scope`; `db.flush()` inside is only a flush — the commit happens
   at the context-manager exit, before `_persist_hold` builds and returns
   the authoritative response dict.
3. The evidence call sits in `ingest` **after** `_persist_hold(...)` has
   returned (svc:445-460), i.e., strictly after the StrategySignal commit,
   and is guarded by `if status == "fresh"` — it is not in a `finally`, not
   in an exception handler, and not inside any authoritative session.
4. Inverse ordering: `test_authoritative_hold_failure_prevents_evidence_call`
   forces `_persist_hold` to raise and counts zero helper invocations; the
   PG script repeats this against real PostgreSQL.
5. The helper receives only identifiers (strings/UUIDs), never an ORM
   entity, so it cannot touch — let alone commit — the authoritative
   session.

## 5. Separate evidence session

**EVIDENCE_TRANSACTION_ISOLATION_CONFIRMED**

`persist_hold_decision_best_effort` (helper lines 65-140): flag check is the
first statement (`is not True` — even truthy non-boolean values disable),
before any parsing or DB access (proven by a test that replaces
`session_scope` with an exploding callable under flag-false); a new
`session_scope()` opens only when enabled; WebhookEvent, StrategyInstance
and StrategySignal are reloaded from committed state by identifier; commit
and rollback affect only the evidence session (context-managed, always
closed); the helper returns `None` unconditionally. An injected evidence
commit failure (real session, forced rollback+raise) left the committed
StrategySignal and WebhookEvent durable and the HOLD response byte-equal
(`test_evidence_commit_failure_rolls_back_only_decision`, plus the PG leg).

## 6. HOLD-only call-site proof

**HOLD_ONLY_RUNTIME_SCOPE_CONFIRMED**

AST-based call-graph tests (not text grep) prove:
`persist_canonical_signal_decision` has exactly one runtime caller — the
helper; `persist_hold_decision_best_effort` has exactly one runtime caller —
`ingest`; and the call node's `If`-ancestors include both `action == 'HOLD'`
and `status == 'fresh'`. Outcome and rejection writers have zero runtime
call sites (AST). Trading-action paths, worker, router, broker and position
paths: 0. No dynamic imports, string resolution or registries reference the
writers (swept). Counts: decision integration paths 1 (fresh HOLD); BUY_CE/
BUY_PE/EXIT/worker/router/broker/position paths 0; outcome 0; rejection 0.

## 7. Fresh/no-backfill proof

**NO_DUPLICATE_OR_HISTORICAL_BACKFILL_CONFIRMED**

Evidence is attempted only when the replay claim returned exactly `fresh`.
Dynamically proven zero-decision outcomes for: identical duplicate HOLD,
conflicting duplicate (409), stale, future-skew, invalid action, malformed
request, unknown credential (uniform 401), revoked credential (uniform
401), lifecycle rejections. The exact required scenario passes: HOLD
accepted with flag false → no decision; the same request redelivered after
the flag becomes true → normal duplicate response and **no** backfilled
decision. The helper contains no repair query — it operates on one
identified committed event only, and no code path searches for
decision-less HOLDs (swept). Edge case noted: the crash-recovery path
(claim `duplicate` but signal missing) re-persists the signal yet does
*not* attempt evidence, because the gate requires `fresh` — conservative
and spec-exact.

## 8. Fingerprint provenance

**COMMITTED_FINGERPRINT_PROVENANCE_CONFIRMED**

Chain verified end-to-end: the canonical fingerprint is computed by trusted
server code (`payload_fingerprint`, svc:139-155) from the normalized
payload *before* the replay claim; it is committed as server-owned
`WebhookEvent.event_metadata["payload_fingerprint"]` (svc:408-417); it is
never read from user JSON, comments or Pine-visible metadata. The helper
reads it **only** from the committed WebhookEvent (helper line 132); its
signature has no fingerprint parameter, so no caller can substitute one;
the writer shape-validates and stores exactly that value; the integration
test asserts `decision.payload_fingerprint ==
event.event_metadata["payload_fingerprint"] == recomputed
payload_fingerprint(payload, "HOLD")` and that a 64-hex user comment cannot
replace it. Foreign event/fingerprint substitution is blocked by the
owner/provider/metadata bindings (section 15).

Relationship clarified: `payload_fingerprint` is the canonical alert
identity (hash of normalized action/signal_id/time/metadata);
`WebhookEvent.raw_body_sha256` is the replay layer's SHA-256 **of that
canonical fingerprint string** (the private path passes the fingerprint as
`raw_body`), not of the literal HTTP body — asserted in the integration
test; replay identity is (provider, event_id) with `raw_body_sha256` as the
tamper check; the decision conflict tuple includes the fingerprint, so a
different fingerprint for the same signal is a detected conflict, never a
silent replacement. No documentation in this branch misdescribes these
hashes.

## 9. Canonical HOLD mapping

**HOLD_CANONICAL_MAPPING_CONFIRMED**

The helper builds the event exclusively via `adapt_legacy_action("HOLD",…)`
(no hand-written mapping), and the persisted row is exactly
HOLD / CONNECTIVITY_TEST / NONE / CONNECTIVITY_TEST with all three
compatibility fields NULL, `provenance_kind=LIVE`,
`adapter_version=nova.legacy-signal-adapter.v1`, referencing the exact
committed StrategySignal, correct instance/owner and the same-ingress
WebhookEvent — asserted field-by-field on SQLite and PostgreSQL. The DB
cross-field CHECK (`ck_canonical_decision_hold_consistency`) is the final
structural guard. HOLD maps to no trading direction, no FLAT, no EXIT.

## 10. Feature-flag behavior

Default/missing/empty/invalid all resolve false (validator unchanged;
re-tested). Flag false: no session, no query, no insert, no nested
transaction, no logging of request data (exploding-session proof). Flag
forced true: BUY_CE/BUY_PE/EXIT (202, zero decisions), duplicate HOLD,
conflicting duplicate, stale, future-skew, invalid credential — all zero
decisions; only fresh HOLD writes one. The flag is absent from API schemas,
webhook payloads, frontend source, strategy metadata and Pine paths
(sweeps + committed exposure tests).

## 11. Response equivalence

**HOLD_RESPONSE_EQUIVALENCE_CONFIRMED**

The HOLD response was compared across: flag false, flag true + success,
each closed writer error (INVALID_INPUT, OWNER_INSTANCE_MISMATCH,
FOREIGN_REFERENCE, CANONICAL_DECISION_CONFLICT, TAINT_DETECTED,
PERSISTENCE_UNAVAILABLE), evidence DB unavailable, evidence commit failure,
unexpected helper exception, and logging failure. In every case: status
202, exact JSON body `{"ok": true, "signal_id": …, "status": "NO_OP",
"reason": "HOLD"}`, same content-type, duplicate semantics unchanged, setup
evidence still validated by the real
`tradingview_setup_service._validate_signal_evidence`, and no
`canonical_decision_id`, evidence status, writer code or database state in
any response (`"decision" not in response.text` asserted). The framework
returns parsed-body + status + header comparison; the flag-off/flag-on pair
additionally compares normalized full bodies.

## 12. Failure isolation

**BEST_EFFORT_FAILURE_ISOLATION_CONFIRMED**

Every forced failure in the Part-10 list leaves: the committed HOLD
committed, the response unchanged, setup evidence valid, zero execution
jobs, zero broker calls, zero position mutations, no reprocessing, no HTTP
500, no duplicate signal or event, no READY regression. The double
suppression at the call site (evidence exception → sanitized recorder →
recorder exception → pass) was verified, including the pathological case
where both the helper and the failure recorder raise.

## 13. Logging and secret safety

**SECRET_SAFE_DIAGNOSTICS_CONFIRMED**

The only diagnostic is
`event=r1b_hold_decision_evidence_failed writer_version=… error_code=…`
with the code clamped to a closed set (unknown codes collapse to
`PERSISTENCE_UNAVAILABLE`; `PERSISTENCE_DISABLED` is not logged at all).
Log-capture tests assert the rendered messages contain no credential,
signal id, user identifier or free text; the injected `nwk_…` secret,
signal ids and email-prefix markers are absent. The independent PG run's
emitted diagnostics visibly match the closed format. Logging failure is
itself suppressed. The R1B-2A 14-value injection matrix remains green at
the writer layer beneath this integration.

## 14. HOLD execution invariants

**HOLD_ZERO_EXECUTION_INVARIANT_CONFIRMED**

Flag false vs true: same StrategySignal count and values, same WebhookEvent
replay state, same setup evidence, zero StrategyExecutionJob rows (asserted
in every R1B-2B test via `_jobs(instance) == []` and PG counts), zero
outcome rows, zero rejection rows, zero broker/risk/contract-resolution
calls (the helper's import graph cannot reach them — AST test), zero JSON
position mutations. Paused-instance and verification-mode HOLD behavior is
unchanged (parametrized test) — both still 202/NO_OP, with one decision
when enabled and still no job.

## 15. Owner/reference isolation

**POST_COMMIT_REFERENCE_ISOLATION_CONFIRMED**

The helper re-verifies, from committed state only: instance owner == caller
owner, event owner == caller owner, event provider ==
`instance-webhook:{instance}`, event metadata instance id == instance,
event metadata action == HOLD, and the signal (looked up by the committed
instance-scoped name + event id) is a completed HOLD. Foreign owner,
foreign instance, foreign/missing event, non-HOLD metadata, missing/deleted
signal: all suppress evidence only (zero rows, unchanged response) —
proven at helper level, API level and on PostgreSQL. The identifiers passed
at the call site originate from `authenticate_credential` (trusted server
state) and the replay claim's committed row id — none from user-replaceable
request fields.

## 16. PostgreSQL concurrency

`scripts/r1b2b_pg_verify.py` was inspected (real `ingest` calls through
threads with a `threading.Barrier`, separate sessions, disposable
container) and independently executed on a fresh `postgres:18-alpine`:
fresh HOLD flag-off/on, mapping + exact fingerprint, duplicate/no-backfill,
conflicting duplicate, concurrent identical HOLD (one 202, one duplicate,
one signal, one event, one decision, zero jobs), concurrent conflicting
HOLD (202+409, one signal, one decision), concurrent evidence unique race
(one row), evidence DB failure and evidence commit failure after
authoritative commit (isolated), authoritative failure inverse (zero
evidence calls), owner/reference suppression — ALL PASS. Container removed
after the run.

## 17. Authoritative failure inverse test

**NO_EVIDENCE_AFTER_AUTHORITATIVE_FAILURE_CONFIRMED** — forced
`_persist_hold` failure propagates out of `ingest` with zero helper
invocations (SQLite + PG); the evidence call is lexically inside the
success path only (AST-verified: no `finally`, no exception handler, no
rollback/duplicate handler contains it).

## 18. Test-quality assessment

| File | Tests | Character |
| --- | --- | --- |
| test_r1b2b_hold_decision_integration.py | 10 | dynamic, full HTTP stack via TestClient, rows + response signatures inspected, genuine thread races (SQLite level; PG authoritative), fingerprint recomputation cross-check |
| test_r1b2b_hold_decision_failure_isolation.py | 13 | dynamic failure injection at writer/session/logger/helper layers wrapping real code; exact-body assertions; log-capture secret search; inverse-ordering proof |
| test_r1b2b_runtime_call_sites.py | 5 | static — allowed categories only (AST call-site counts, forbidden imports, guard ancestry) |
| updated flag/zero-call-site suites | — | allowlists narrowed to exactly the one authorized helper path; dynamic flags-forced-true flow retained |

Mocks replace only failure surfaces (writer/session/logger), never the code
under test on success paths. No tautological or source-text-only behavioral
test found; the one source-level test (guard ancestry) is a legitimate
static category. Concurrency at HTTP level uses real threads; PostgreSQL is
treated as the authoritative race result via the script.

## 19. Regression results

At `ebc7b3e` on the review branch: R1B-2B focused 32 passed; R1B-2A
writers/statics/flags 103 passed; webhook + execution 81 passed;
canonical/analyzer 108 passed; Pine regressions 64 passed; full backend
1028 passed (run alone); `alembic heads` →
`0015_r1b_persistence`; frontend tsc clean / vitest 20 passed / build
success; `git diff --check` clean; changed-file py_compile clean; Ruff on
changed files: one pre-existing F401 (section 22).

## 20. Scope-creep result

**SCOPE_CLEAN.** Runtime scope is exactly the ingress call block, the new
helper, the additive replay-claim return key, and two comment/docstring
updates. Zero frontend files changed, so the repository ESLint/chunk
baseline is pre-existing by this review's own criteria.

## 21. Blocking findings

None.

## 22. Non-blocking findings

1. **Baseline Ruff F401 in `config.py`** (`DEFAULT_EXCHANGE_SEGMENT`
   imported but "unused").
   - `backend/app/config.py:7`, module scope.
   - Observed: this is the deliberate R1B-0 compatibility re-export that
     every legacy `from app.config import DEFAULT_EXCHANGE_SEGMENT` caller
     depends on; the import line is unchanged since R1B-0 — this branch
     touched only adjacent comments, and the finding count did not
     increase. Auto-"fixing" it would break existing callers.
   - Verdict: pre-existing baseline; suggest `__all__` or `# noqa: F401`
     in a future hygiene pass, not here.
2. **Helper freshness relies on the single call-site gate.**
   - `hold_canonical_decision_evidence.py`,
     `persist_hold_decision_best_effort`.
   - Observed: the helper verifies "committed genuine HOLD" but not
     "fresh"; freshness is enforced solely by the `status == "fresh"` guard
     at the one call site (AST-tested). A direct call for an old committed
     HOLD (as one idempotency test does deliberately) would write its
     decision. Risk: none in production (one guarded call site; static test
     fails on any new caller); worth restating in the R1B-2C/2D designs so
     future call sites keep the gate at ingress.
   - Verdict: non-blocking, by-design division of responsibility.

## 23. Next R1B design-gate recommendation

R1B-2B is closed. The next gate should be selected by a separate design
prompt — the natural candidates, in the approved slice order, are
trading-action decision integration (BUY_CE/BUY_PE/EXIT via the same
post-commit pattern in `_persist_execution_job`), then R1B-2C outcomes at
the worker's `_complete_job`, then R1B-2D rejections. Each requires its own
design and independent review; finding 2 above and the R1B-2A fingerprint
provenance requirement carry forward as standing inputs.
