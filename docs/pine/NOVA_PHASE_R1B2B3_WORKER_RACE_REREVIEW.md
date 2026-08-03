# NOVA Phase R1B-2B3R — Independent Worker-Race Fix Re-Review

## 1. Executive verdict

**APPROVED_WITH_NON_BLOCKING_FINDINGS**

The correction at
`9976089a80418b049082cf665a4be18646052eb3` resolves the real worker-ahead
evidence gap. The old implementation was independently reproduced failing
through the real `_complete_job()` → `_refresh_signal_summary()` path.
The corrected helper no longer reads `StrategySignal.result_summary` as
authority, derives accepted action from a server-built committed job
envelope, cross-checks it against the independently committed normalized
WebhookEvent action, and owner-scopes the execution-job SQL predicate.

The full backend was completed in eight deterministic file shards:
1,114 tests across 81 files were accounted for exactly once. The result was
1,113 passed and one deterministic pre-existing Dhan payload-validation
failure. That test fails identically 5/5 at the review baseline `5091979`
and 5/5 at the correction SHA; no Dhan or security-ID file changed in the
correction. It is therefore recorded as non-blocking for this differential
review and was not repaired or ignored.

Approval authorizes only R1B-2C outcome **design**. It does not authorize flag
activation, outcome or rejection implementation, merge, deployment, live
enablement, or AI conversion.

## 2. Reviewed correction and scope

- Starting/reviewed correction SHA:
  `9976089a80418b049082cf665a4be18646052eb3`
- Correction base/review report SHA:
  `509197944d11127726a9a5af4e7f3aca4a0c337c`
- Original blocked implementation:
  `c45427c7f7ed48e85593518871c2746ac5aefb00`
- Correction branch: `phase-r1b2b3-worker-race-fix`
- Re-review branch: `phase-r1b2b3-worker-race-rereview`
- Starting worktree: clean
- Review mode: read-only; this report is the only repository change.

The blocked report
`docs/pine/NOVA_PHASE_R1B2B4_TRADING_DECISION_REVIEW.md` remains unchanged.

## 3. Correction diff classification

Diff: `5091979..9976089`.

| Classification | File |
| --- | --- |
| WORKER_RACE_RUNTIME_FIX | `backend/app/services/trading_canonical_decision_evidence.py` |
| NEW_REGRESSION_TEST | `backend/app/tests/test_r1b2b3_worker_race_fix.py` |
| UPDATED_REGRESSION_TEST | `backend/app/tests/test_r1b2b3_trading_decision_failure_isolation.py` |
| UPDATED_REGRESSION_TEST | `backend/app/tests/test_r1b2b3_trading_decision_integration.py` |
| POSTGRESQL_PROBE | `backend/scripts/r1b2b3_pg_verify.py` |
| DOCUMENTATION | `docs/pine/NOVA_PHASE_R1B2B3_TRADING_DECISION_INTEGRATION.md` |
| DOCUMENTATION | `docs/pine/NOVA_PHASE_R1B2B3_WORKER_RACE_FIX.md` |
| UNEXPECTED | none |

Direct diff checks found no functional change to the HOLD helper, decision
writer, private webhook ingress, replay identity, worker, router, risk
manager, PaperBroker, Dhan, position state/reconciliation, models, Alembic,
frontend, prompts, or transports.

## 4. Original defect reproduction

**ORIGINAL_WORKER_RACE_REPRODUCED**

At `c45427c`, the current real-worker regression file was executed against
the old application with import isolation:

```text
21 failed, 8 passed
```

The nine required real-worker cases all failed:

- BUY_CE, BUY_PE and EXIT;
- completed/traded, failed and blocked/no-op.

In each case, `strategy_job_worker._complete_job()` called
`_refresh_signal_summary()` (`backend/app/workers/strategy_job_worker.py`,
lines 104–158), the aggregate signal summary contained no ingress action,
the old helper emitted closed `FOREIGN_REFERENCE`, and the decision query
returned no row. The failure was not reproduced by status-only mutation.

## 5. Result-summary authority removal

**MUTABLE_RESULT_SUMMARY_AUTHORITY_REMOVED**

`persist_trading_decision_best_effort`
(`backend/app/services/trading_canonical_decision_evidence.py`, lines 97–214)
contains no `result_summary` attribute read. An AST proof confirms it has no
summary helper, `.get("action")`, `["action"]`, fallback, exception-recovery,
owner/instance/job-binding, fingerprint, or mapping dependency on the mutable
signal summary.

Nine adversarial cases in
`test_mutable_summary_cannot_select_or_suppress_action`
(`backend/app/tests/test_r1b2b3_worker_race_fix.py`, lines 121–147) replace
the summary with empty, HOLD, and opposite actions for BUY_CE, BUY_PE and
EXIT. All nine pass at `9976089` and all nine fail against `c45427c`.

## 6. Immutable accepted-action source

**IMMUTABLE_ACCEPTED_ACTION_SOURCE_CONFIRMED**

Trusted trace:

1. `PrivateWebhookPayload` forbids extra request fields
   (`private_webhook_service.py`, lines 55–70).
2. `normalize_action` trims, uppercases, and accepts only BUY_CE, BUY_PE,
   EXIT or HOLD (lines 41–43 and 91–93).
3. `ingest` computes this normalized value before replay claim or job creation
   (lines 392–404).
4. The WebhookEvent metadata action is built by server code from that value
   (lines 406–422).
5. `build_normalized_signal` constructs a new server-owned dictionary and
   writes `raw_payload["normalized_action"] = action`
   (lines 270–312).
6. `_persist_execution_job` serializes that normalized signal into the
   committed `StrategyExecutionJob.signal_payload` (lines 586–622).

The path name `raw_payload.normalized_action` is historical Pydantic envelope
terminology. It is not literal untrusted request JSON. The only request action
is normalized through the closed server vocabulary; unknown
`normalized_action`, `raw_payload`, and `signal_payload` request fields are
rejected by `extra="forbid"`.

An independent override probe confirmed:

```text
SERVER_NORMALIZATION_OVERRIDE_PROBE=PASS
normalized_action=BUY_CE
comment_preserved_only_as_comment=True
```

A Pine comment containing `normalized_action=EXIT` and a fake raw payload
remained inert comment text and could not change the stored BUY_CE action.

Worker claim, retry, execution and completion code
(`strategy_job_worker.py`, lines 33–190) reads or snapshots
`signal_payload` but never assigns it. The execution router receives the
validated signal value and has no job-payload update path. Repository-wide
runtime search found job-payload assignments only at job creation. Thus the
field is application-immutable after commit. The JSON column itself has no
database immutability trigger; that honest boundary is recorded as
non-blocking finding 2.

The replay store exposes a generic metadata-merge API for other webhook
providers (`webhook_replay_store.py`, lines 112–138), but repository call-site
inspection found no such update for the private `instance-webhook:{id}`
provider. Its accepted action is therefore also application-immutable on the
reviewed private ingress path.

## 7. Event/job action consistency

**COMMITTED_ACTION_CROSS_CHECK_CONFIRMED**

The helper requires:

- committed event action in BUY_CE/BUY_PE/EXIT;
- event metadata instance equal to the supplied instance;
- job normalized action in the same closed set;
- event action exactly equal to job action;
- server marker, strategy instance, strategy code and signal ID agreement.

Code: `trading_canonical_decision_evidence.py`, lines 124–194.

Independent dynamic probe:

```text
ACTION_CROSS_CHECK_PROBE=PASS
matching_actions=3 mismatch_cases=8 decisions_on_mismatch=0
```

The three matching actions persisted. Event HOLD/opposite/missing/malformed/
unsupported and job missing/malformed/unsupported cases all wrote zero
decisions, retained the mutated pre-evidence signal/job snapshots, preserved
the already-produced authoritative response, and emitted only closed
diagnostics. No heuristic winner exists.

## 8. Real worker-ahead path

**REAL_WORKER_AHEAD_RACE_RESOLVED**

`test_real_worker_completion_preserves_accepted_decision`
(`test_r1b2b3_worker_race_fix.py`, lines 73–118) calls the actual private
worker `_complete_job`, which calls `_refresh_signal_summary`; neither
function is mocked. It covers:

- BUY_CE, BUY_PE and EXIT;
- completed/traded;
- failed;
- blocked/no-op.

All nine pass. Each test confirms the replacement summary has no action,
then inspects the exact decision, signal status, job status, response action,
and zero outcome/rejection rows. Retryable completion is not a distinct
supported terminal result: stale retry recovery returns a job to queued and
does not execute the completion-summary replacement.

## 9. Regression-test validity

**WORKER_RACE_REGRESSION_TESTS_VALID**

At `c45427c`, the full new regression file produced:

```text
21 failed, 8 passed
```

The 21 expected failures comprise:

- 9 real worker-completion cases;
- 9 adversarial mutable-summary cases;
- 3 immutable job-envelope mismatch cases.

At `9976089`, the same file produced:

```text
29 passed
```

The old failures occur because the old helper either suppresses evidence
after summary replacement or ignores the new immutable job-envelope checks.
Tests inspect decision rows and authoritative rows, invoke the real worker
mutation, do not restore the old action, and do not mock
`_refresh_signal_summary`.

## 10. Owner-scoped job reload

**OWNER_SCOPED_JOB_RELOAD_CONFIRMED**

The job query (`trading_canonical_decision_evidence.py`, lines 157–166)
contains SQL predicates for:

- `strategy_signal_id == strategy_signal.id`;
- instance-derived `strategy_name`;
- event-derived `signal_id`;
- `user_id == owner_uuid`.

The foreign-owner row therefore fails selection in SQL; there is no
after-query ownership fallback. Correct owner, foreign owner, foreign
instance/event and missing references are covered by committed tests.

Independent ambiguity probe:

```text
OWNER_AMBIGUITY_PROBE=PASS
same_looking_jobs=2 owner_scoped_matches=1 foreign_signal_decisions=0
```

## 11. Lookup uniqueness

**COMMITTED_JOB_LOOKUP_UNAMBIGUOUS**

Schema constraints:

- WebhookEvent: unique `(provider, event_id)` (`models.py`, lines 193–220);
- StrategySignal: unique `(strategy_name, signal_id)` (lines 393–409);
- StrategyExecutionJob: unique `(strategy_name, signal_id, user_id)`, with
  non-null signal FK and owner (`models.py`, lines 412–453).

The strategy name is structurally `instance:{strategy_instance_id}`. The
signal query therefore has at most one row. The full corrected job predicate
is at least as strong as its exact database unique key and also binds the
signal FK. The helper uses no `first()`, `limit(1)`, ordering, “latest” row,
or timestamp tie-breaker.

The schema deliberately allowed two same-looking jobs for different owners
in the independent probe; adding the owner predicate reduced two candidates
to exactly one. A job rebound to a foreign signal selected no row and wrote
no decision.

## 12. Dual feature-flag contract

**DUAL_FAIL_CLOSED_FLAG_CONFIRMED**

Gate 1 is the literal-true helper check
(`trading_canonical_decision_evidence.py`, lines 112–116). False returns
before identifier parsing, session access, query, or logging.

Gate 2 remains in the insert-only writer
(`canonical_signal_decision_persistence.py`, lines 88–117). False raises
closed `PERSISTENCE_DISABLED` before validation/query/insert behavior.

No action/fingerprint/authorization-token parameter or
`skip_flag_check`, `already_authorized`, `force_persist`, or equivalent
bypass exists. The configured default remains false
(`backend/app/config.py`, line 129).

## 13. Mid-request flag transitions

**MID_REQUEST_FLAG_BEHAVIOR_CONFIRMED**

- false at helper → true later: zero session, zero decision, no backfill;
- true/true: exact decision;
- true at helper → false at writer: `PERSISTENCE_DISABLED`, zero decision,
  unchanged authoritative rows and response;
- true → false → true: current attempt remains suppressed; no automatic
  retry or duplicate backfill.

Dynamic tests: `test_writer_gate_fails_closed_after_helper_gate`
(`test_r1b2b3_worker_race_fix.py`, lines 204–244) for all three actions;
duplicate/no-backfill test (`test_r1b2b3_trading_decision_integration.py`,
lines 170–190). PostgreSQL independently covers both transitions. Repository
search found no remaining complete-chain “single evaluation” claim.

## 14. Post-commit/session isolation

**POST_COMMIT_SESSION_ISOLATION_PRESERVED**

`_persist_execution_job` creates the signal and job within one
`session_scope`; context exit commits, then the worker is woken
(`private_webhook_service.py`, lines 586–640). `ingest` calls the evidence
helper only after `_persist_execution_job` returns
(`private_webhook_service.py`, lines 466–490).

The helper accepts committed identifiers only and opens its own
`session_scope`; it receives no ORM entity. Evidence commit failure rolls
back only the decision. Authoritative transaction failure invokes no helper.
These properties pass on SQLite and PostgreSQL.

## 15. Response equivalence

**CORRECTION_RESPONSE_EQUIVALENCE_CONFIRMED**

The integration and failure-isolation suites compare 202 status, serialized
body, content type, `ok`, signal ID, status and action across flag off/on,
all closed writer failures including `PERSISTENCE_DISABLED`, session outage,
commit failure, unexpected helper failure and diagnostic failure
(`test_r1b2b3_trading_decision_integration.py`, lines 153–190;
`test_r1b2b3_trading_decision_failure_isolation.py`, lines 38–201).

Real-worker, empty/opposite summary and foreign-reference probes retain the
authoritative response captured before direct evidence invocation. Duplicate
and conflicting replay behavior remains unchanged. No response contains
decision ID, evidence result, flag state, writer error, or database state.

## 16. Authoritative row invariants

**AUTHORITATIVE_SIGNAL_JOB_UNCHANGED**

The helper performs SELECTs and passes read entities to the insert-only
decision writer; it assigns no StrategySignal or StrategyExecutionJob
attribute (`trading_canonical_decision_evidence.py`, lines 123–211).
Dynamic snapshots cover success, immutable mismatch, foreign owner, writer
failure and evidence rollback. Worker-ahead tests distinguish worker-owned
status/summary changes from subsequent evidence behavior. Evidence changes
neither signal/job state nor router, broker, risk, position, lots, execution
mode, result, attempts, timestamps, owner, instance, payload or linkage.

## 17. Fingerprint provenance

**COMMITTED_FINGERPRINT_PROVENANCE_PRESERVED**

`payload_fingerprint` is computed server-side from the normalized action and
the closed request model (`private_webhook_service.py`, lines 143–159), put
into committed WebhookEvent metadata (lines 404–422), reloaded by the helper
(`trading_canonical_decision_evidence.py`, lines 133–143 and 195–209), and
shape-validated/stored by the writer
(`canonical_signal_decision_persistence.py`, lines 88–117 and 147–175).

The helper accepts no fingerprint argument. Job payload, mutable summary,
worker result, comment and arbitrary metadata cannot substitute it. Tests
compare the stored value with an independent recomputation and prove a
64-hex Pine comment cannot replace it.

## 18. Secret-safe diagnostics

**CORRECTION_DIAGNOSTICS_SECRET_SAFE**

`record_trading_decision_failure`
(`trading_canonical_decision_evidence.py`, lines 49–76) clamps both inputs
to closed error codes and ENTRY/EXIT before logging. Logging failure is
suppressed.

An independent 14-marker probe injected credential, access token, TOTP,
Authorization header, signal/job markers, fingerprint, raw request, Pine
source, owner email, database URL, SQL text and Windows/Linux paths:

```text
SECRET_DIAGNOSTIC_PROBE=PASS
markers_rejected=14 messages=15
```

Only event, closed writer version, optional safe action class and closed
error code remained. `PERSISTENCE_DISABLED` revealed no environment or
settings value. Existing end-to-end tests also inspect response, logs,
fingerprint and injected comment.

## 19. Static runtime scope

**CORRECTION_RUNTIME_SCOPE_CONFIRMED**

Static suites confirm:

- no helper result-summary authority;
- no action or fingerprint helper parameter;
- owner predicate in the job SQL;
- independent writer flag;
- no bypass parameter;
- exactly two decision-writer callers: HOLD and trading helpers;
- exactly one ingress caller for each helper;
- zero production outcome-writer callers;
- zero production rejection-writer callers;
- no worker/router/broker/position import in the trading helper.

The normalized HOLD-helper SHA-256 remains
`b741c12cb6014f07a0e026fca5c4b88b5726374f4ec43f4cae0f69885898b0b2`.

## 20. PostgreSQL verification

**POSTGRESQL_WORKER_RACE_FIX_CONFIRMED**

The updated script was inspected and independently run against disposable
PostgreSQL 18. It executes real `_complete_job` summary replacement for all
three actions, adversarial summaries, owner-scoped selection, evidence
rollback, dual flag transitions, simultaneous worker/helper threads with a
barrier and separate sessions, and uniqueness races.

Observed:

```text
R1B2B3 PG VERIFY: ALL PASS
```

Rollback assertions were signal count 1, job count 1 and decision count 0.
Worker races produced one exact decision and no evidence-owned authoritative
mutation. The container `nova-pg-r1b2b3-rereview` was removed.

## 21. Full backend regression

Collection/accounting:

```text
81 test files
1,114 collected tests
8 deterministic file shards
shard sizes: 140, 140, 140, 140, 140, 138, 138, 138
each test file included exactly once
```

Completed result:

```text
1,113 passed
1 pre-existing deterministic failure
```

The failure is
`test_security_id_resolver.py::test_dhan_payload_validation_passes_after_security_id_resolution`
(lines 87–97): expected `validation["ok"] is True`, observed `False`, while
resolution was true and payload security ID was `999999`.

Differential repetition:

```text
5091979 baseline: failed identically 5/5
9976089 correction: failed identically 5/5
```

The correction diff contains no Dhan/security-ID code or test. This is not a
worker-race correction regression and was not modified in this read-only
review.

## 22. Other regression results

| Suite | Result |
| --- | --- |
| Focused correction | 29 passed |
| R1B-2B3 integration/static/concurrency/failure | 57 passed |
| R1B-2B HOLD | 32 passed |
| R1B-2A writers | 103 passed |
| Webhook/execution | 81 passed |
| Canonical/analyzer | 108 passed |
| Pine regressions | 64 passed |
| Alembic | `0015_r1b_persistence (head)` |
| TypeScript | pass |
| Vitest | 20 passed |
| Frontend build | pass |
| Ruff changed runtime/tests/script | pass |
| Python compilation | pass |
| `git diff --check` before report | pass |

The recurring Starlette deprecation warning and Vite chunk-size warning are
unrelated, non-failing toolchain observations.

## 23. Scope-creep result

**SCOPE_CLEAN**

The sole runtime diff is the trading evidence helper. No execution-authority
code changed. HOLD, writer, private ingress, replay, worker, router, broker,
risk, Dhan, position, model, migration, frontend, prompt and transport files
are byte-identical across the correction range.

No runtime or test logic changed during this re-review.

## 24. Blocking findings

None.

## 25. Non-blocking findings

| File/function/lines | Observed behavior | Risk | Verdict |
| --- | --- | --- | --- |
| `backend/app/tests/test_security_id_resolver.py`, `test_dhan_payload_validation_passes_after_security_id_resolution`, lines 87–97 | Deterministically fails 5/5 at both `5091979` and `9976089` with identical expected/actual values. | The repository's aggregate suite is not globally green, but the failure predates and is untouched by this correction. | NON_BLOCKING_BASELINE_FAILURE; track separately, do not alter Dhan in this phase. |
| `backend/app/db/models.py`, `StrategyExecutionJob.signal_payload`, lines 412–453; `backend/app/workers/strategy_job_worker.py`, claim/retry/execute/complete, lines 33–190 | The JSON ingress snapshot is application-immutable by creation/update call graph, but no database trigger or ORM immutability guard prevents future arbitrary code from assigning the column. The helper cross-checks an independent event action and fails closed on mismatch. | A future unauthorized write path could weaken the provenance assumption; current runtime has no such path. | NON_BLOCKING_APPLICATION_IMMUTABILITY_BOUNDARY; retain static/update-path review in future phases. |

## 26. R1B-2C readiness

The correction closes R1B-2B3 trading-action decision integration and is
approved only for **R1B-2C outcome design**.

Explicit confirmations:

- No runtime code changed during re-review.
- No test logic changed during re-review.
- No migration changed.
- The old worker race was independently reproduced.
- `StrategySignal.result_summary` is not accepted-action authority.
- The selected job action is trusted, server-normalized committed ingress
  intent and is application-immutable after creation.
- User metadata and Pine comments cannot override accepted action.
- Event and job actions must agree.
- The real worker path no longer creates an evidence gap.
- The execution-job query is owner-scoped and unambiguous.
- The writer retains its independent flag gate; no bypass exists.
- Evidence remains post-commit and separate-session.
- Evidence failure cannot alter the webhook response.
- Decisions remain accepted-intent evidence only.
- HOLD remains unchanged.
- Outcome and rejection production-path counts remain zero.
- The shared feature flag remains disabled.
- No prompt or transport changed.
- No AI conversion was enabled.
- No live order occurred.
- No merge or deployment occurred.
