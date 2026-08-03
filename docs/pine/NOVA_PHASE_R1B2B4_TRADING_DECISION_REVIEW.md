# NOVA Phase R1B-2B4 — Independent Trading-Decision Integration Review

## 1. Executive verdict

**BLOCKED_WORKER_RACE**

The authoritative signal/job transaction ordering, fresh-request gate,
separate evidence session, canonical mappings, committed fingerprint chain,
response isolation, HOLD freeze, and two-helper static call graph are sound.
The updated pre-existing boundary tests remain narrowly scoped and do not
weaken the former HOLD/outcome/rejection safety properties.

Approval is blocked because the trading helper validates
`StrategySignal.result_summary["action"]` after the worker is woken. The real
worker completion path replaces the entire signal summary with aggregate job
results and removes that key. Independent SQLite and PostgreSQL probes using
the real `_complete_job()` → `_refresh_signal_summary()` path produced:

```text
authoritative response = 202 QUEUED
job status = completed
signal status = completed
signal summary = aggregate results without action
decision count after helper = 0
diagnostic = FOREIGN_REFERENCE
```

The committed SQLite and PostgreSQL “worker-ahead” tests change status fields
synthetically but preserve the old ingress summary, so they pass without
exercising the behavior they claim to prove. Two additional discrepancies
also require correction before review can pass: the execution-job lookup does
not use the full database unique key, and the decision writer re-reads the
shared flag after helper entry.

This review does not authorize R1B-2C outcome design.

## 2. Reviewed commit and scope

- Starting SHA: `c45427c7f7ed48e85593518871c2746ac5aefb00`
- Reviewed implementation SHA:
  `c45427c7f7ed48e85593518871c2746ac5aefb00`
- Implementation diff:
  `55deb9115cb8e9a735b59cccf24256d2b917d9b0..c45427c7f7ed48e85593518871c2746ac5aefb00`
- Implementation branch: `phase-r1b2b3-trading-decision-integration`
- Review branch: `phase-r1b2b4-trading-decision-review`
- Starting worktree: clean
- Review mode: read-only; this report is the only repository change.

Reviewed inputs:

- `NOVA_PHASE_R1B2B2_TRADING_DECISION_DESIGN.md`
- `NOVA_PHASE_R1B2B3_TRADING_DECISION_INTEGRATION.md`
- `NOVA_PHASE_R1B2B_HOLD_DECISION_INTEGRATION.md`
- `NOVA_PHASE_R1B2B_HOLD_DECISION_REVIEW.md`
- `NOVA_PHASE_R1B2A_EVIDENCE_WRITER_LIBRARIES.md`
- `NOVA_PHASE_R1B2A_WRITER_REVIEW.md`

## 3. Changed-file classification

| Classification | Files |
| --- | --- |
| TRADING_RUNTIME_INTEGRATION | `backend/app/services/private_webhook_service.py` |
| TRADING_EVIDENCE_HELPER | `backend/app/services/trading_canonical_decision_evidence.py` |
| NEW_TEST | `backend/app/tests/test_r1b2b3_runtime_call_sites.py`; `backend/app/tests/test_r1b2b3_trading_decision_concurrency.py`; `backend/app/tests/test_r1b2b3_trading_decision_failure_isolation.py`; `backend/app/tests/test_r1b2b3_trading_decision_integration.py` |
| UPDATED_BOUNDARY_TEST | `backend/app/tests/test_r1b2a_zero_call_sites.py`; `backend/app/tests/test_r1b2b_hold_decision_integration.py`; `backend/app/tests/test_r1b2b_runtime_call_sites.py`; `backend/app/tests/test_r1b_persistence_flags.py` |
| POSTGRESQL_PROBE | `backend/scripts/r1b2b3_pg_verify.py` |
| DOCUMENTATION | `docs/pine/NOVA_PHASE_R1B2B3_TRADING_DECISION_INTEGRATION.md` |
| UNEXPECTED | none |

The diff contains no functional change to the HOLD helper, replay identity,
credential resolution, private execution, worker, router, risk manager,
PaperBroker, Dhan, position state, reconciliation, contract resolution,
models, Alembic, frontend, prompts, or transports.

## 4. Updated-test diff assessment

**UPDATED_TEST_BOUNDARIES_REMAIN_STRONG**

### `test_r1b2a_zero_call_sites.py`

| Item | Assessment |
| --- | --- |
| Original assertion | Only `hold_canonical_decision_evidence.py` could reference/call the decision writer and safety utility. Outcome/rejection callers remained zero; forbidden execution imports and flag homes were closed. |
| New assertion | The allowlist is exactly `{hold helper, trading helper}`. All other runtime files remain forbidden; outcome/rejection paths remain zero; both helpers receive the same forbidden-import scrutiny. |
| Reason | A second explicitly authorized decision helper now exists. |
| Safety retained | Yes. Runtime scan remains exhaustive and the new R1B-2B3 AST suite pins the two exact helper paths. |
| Strength | Equivalent for the old property and stricter for the two-helper graph because both helpers are inspected. |
| Coverage moved | Exact path/count and trading guard ancestry are additionally checked by `test_r1b2b3_runtime_call_sites.py`. |
| Escape risk | No new production caller can escape without failing at least one static suite. |

### `test_r1b2b_hold_decision_integration.py`

| Item | Assessment |
| --- | --- |
| Original assertion | BUY_CE/BUY_PE/EXIT produced zero decisions. |
| New assertion | Each produces one decision through the trading helper while a spy proves the HOLD helper receives zero trading calls. |
| Reason | Trading decisions are the authorized implementation scope; the old zero-row expectation is no longer valid. |
| Safety retained | HOLD-helper isolation is now asserted directly; trading mappings/references are covered in the new dynamic suite. |
| Strength | Stronger separation assertion; the obsolete zero-trading-decision property is intentionally replaced. |
| Coverage moved | Exact trading decision contents moved to `test_r1b2b3_trading_decision_integration.py`. |
| Escape risk | HOLD-only ancestry and fresh gating remain independently asserted. |

### `test_r1b2b_runtime_call_sites.py`

| Item | Assessment |
| --- | --- |
| Original assertion | Decision writer caller set was exactly the HOLD helper. |
| New assertion | Caller set is exactly the HOLD and trading helpers. |
| Reason | The production graph now has two approved helper callers. |
| Safety retained | HOLD helper ingress count, HOLD action exclusion, HOLD guard ancestry, forbidden imports, and zero outcome/rejection callers are unchanged. |
| Strength | Equivalent boundary generalized to the exact two-helper set. |
| Coverage moved | Trading-specific ancestry/import checks are additive in the new static suite. |
| Escape risk | None found. |

### `test_r1b_persistence_flags.py`

| Item | Assessment |
| --- | --- |
| Original assertion | Decision flag homes were writer + HOLD helper; forced flags produced one HOLD decision. |
| New assertion | Trading helper is the only added flag home; forced flags produce exactly fresh HOLD, active BUY_CE, and paused BUY_CE decisions. |
| Reason | The shared flag now intentionally gates all accepted decision types. |
| Safety retained | API/frontend exposure checks, false/invalid parsing, zero outcome/rejection/Pine writes, and rejected/duplicate zero-write behavior remain. |
| Strength | Equivalent confinement with a stricter authorized-row count. |
| Coverage moved | Per-action flag behavior is covered by the new integration suite. |
| Escape risk | None found. |

Finding record:

- File path: the four updated tests above.
- Function/class: changed boundary tests described above.
- Line range: implementation diff hunks across each changed test.
- Observed behavior: allowlists grew only by the one authorized helper and
  obsolete zero-trading-row assertions were replaced with exact authorized
  counts and helper separation.
- Risk: a broad allowlist could hide a new execution caller.
- Verdict: no such broadening occurred; updated boundaries remain strong.

## 5. Authoritative commit boundary

**POST_COMMIT_SIGNAL_JOB_ORDERING_CONFIRMED**

Sequence verified from code:

```text
WebhookEvent replay session exits and commits
→ _persist_execution_job opens one authoritative session
→ StrategySignal inserted and flushed
→ StrategyExecutionJob inserted and flushed
→ authoritative session exits
→ session_scope commits signal and job atomically
→ wake_strategy_job_worker()
→ authoritative response dict returned
→ ingest evaluates fresh/non-duplicate trading gate
→ separate evidence helper may run
→ unchanged response returned
```

Evidence is not inside `_persist_execution_job`, is not triggered by either
flush, is not in `finally`, receives no ORM object, and cannot roll back the
signal/job session. Forced authoritative failure produced zero helper calls.

Finding record:

- File path: `backend/app/services/private_webhook_service.py`;
  `backend/app/db/engine.py`.
- Function/class: `_persist_execution_job`, `ingest`, `session_scope`.
- Line range: service 586-640 and 466-490; engine 123-135.
- Observed behavior: signal and job commit before worker wake and before
  evidence invocation.
- Risk: pre-commit evidence could roll back or orphan authoritative rows.
- Verdict: ordering is correct.

## 6. Fresh trading-only gate

**FRESH_TRADING_ONLY_SCOPE_CONFIRMED**

The single runtime gate is:

```python
status == "fresh"
and action in {"BUY_CE", "BUY_PE", "EXIT"}
and not result.get("duplicate")
```

and is reached only after `_persist_execution_job` returns. HOLD branches
earlier. Duplicate, conflicting, stale/future, invalid action, invalid/revoked
credential, and authoritative-failure paths do not reach it. AST and dynamic
tests agree.

Finding record:

- File path: `backend/app/services/private_webhook_service.py`.
- Function/class: `ingest`.
- Line range: 449-490.
- Observed behavior: one fresh/non-duplicate trading helper call site.
- Risk: duplicate repair or rejected-request evidence.
- Verdict: gate is correct.

## 7. HOLD path freeze

**REVIEWED_HOLD_PATH_UNCHANGED**

- The current HOLD helper is byte-identical to the reviewed base:
  SHA-256 `b741c12cb6014f07a0e026fca5c4b88b5726374f4ec43f4cae0f69885898b0b2`.
- `git diff 55deb91..c45427c` shows no HOLD helper change.
- The existing HOLD call block is semantically unchanged; only the import was
  reformatted to include the trading sibling.
- HOLD retains one fresh ingress call, one optional decision, zero jobs,
  unchanged response/fingerprint/no-backfill behavior.
- Trading helper imports do not include the HOLD helper.

Finding record:

- File path: `backend/app/services/hold_canonical_decision_evidence.py`;
  `backend/app/services/private_webhook_service.py`.
- Function/class: `persist_hold_decision_best_effort`, `ingest`.
- Line range: HOLD helper entire file; ingress 449-465.
- Observed behavior: exact reviewed bytes and call semantics retained.
- Risk: regression of independently approved HOLD behavior.
- Verdict: unchanged.

## 8. Helper trust boundary

The public trading helper accepts only:

```text
webhook_event_id
strategy_instance_id
owner_user_id
```

It accepts no action, fingerprint, signal ID, status, desired state,
compatibility field, quantity, lots, contract data, execution mode, worker/
router/broker result, position state, raw request, Pine source, or metadata.
Action and fingerprint come from the committed event; canonical mapping comes
from the reviewed adapter.

Finding record:

- File path:
  `backend/app/services/trading_canonical_decision_evidence.py`.
- Function/class: `persist_trading_decision_best_effort`.
- Line range: 97-102.
- Observed behavior: committed identifiers only.
- Risk: caller-controlled canonical meaning.
- Verdict: signature trust boundary is correct.

## 9. Committed reference reload

The signal reload is unambiguous: `(strategy_name, signal_id)` is protected
by `uq_strategy_signal`. The event and instance are primary-key reloads.

The job reload is not proven unambiguous. It queries only
`(strategy_name, signal_id)` while `uq_strategy_signal_user_job` protects
`(strategy_name, signal_id, user_id)`. `Session.scalar()` returns the first
matching row without an ordering or uniqueness assertion. Post-selection
owner/FK checks fail closed, but a database-permitted foreign-user duplicate
could be selected nondeterministically and suppress valid evidence.

Finding record:

- File path:
  `backend/app/services/trading_canonical_decision_evidence.py`;
  `backend/app/db/models.py`.
- Function/class: `persist_trading_decision_best_effort`;
  `StrategyExecutionJob`.
- Line range: helper 166-179; models 415-422.
- Observed behavior: helper claims a unique-key reload but omits `user_id`
  from the job query.
- Risk: ambiguous selection and avoidable missing evidence under a
  schema-valid foreign row.
- Verdict: committed job reload is not fully confirmed and must use the full
  unique identity or a uniquely constrained relationship.

## 10. Separate evidence session

**TRADING_EVIDENCE_SESSION_ISOLATION_CONFIRMED**

The flag gate precedes parsing/session access. An independent
`session_scope()` reloads committed event, instance, signal, and job. Only the
decision insert is in that session. PostgreSQL evidence rollback produced:

```text
WebhookEvent = 1
StrategySignal = 1
StrategyExecutionJob = 1
CanonicalSignalDecision = 0
response = unchanged QUEUED result
job = durable/claimable
```

The context rolls back on error and always closes. No helper value enters the
response or worker wake-up.

Finding record:

- File path:
  `backend/app/services/trading_canonical_decision_evidence.py`;
  `backend/app/db/engine.py`.
- Function/class: `persist_trading_decision_best_effort`, `session_scope`.
- Line range: helper 114-200; engine 123-135.
- Observed behavior: independent transaction with best-effort suppression.
- Risk: evidence rollback affecting execution.
- Verdict: isolated.

## 11. Worker-ahead race

**BLOCKING FINDING**

The helper has no explicit `signal.status` or `job.status` predicate, but it
does have a transient worker-dependent predicate:

```python
summary.get("action") == committed_event_action
```

The real worker `_refresh_signal_summary()` replaces `result_summary` after
all jobs finish:

```text
strategy_name
signal_id
subscriber_count
results
```

The accepted ingress `action` key is removed. Therefore:

- queued/running before final refresh: helper can write the decision;
- completed: helper suppresses with `FOREIGN_REFERENCE`;
- failed/completed_with_errors: helper suppresses with `FOREIGN_REFERENCE`;
- completed no-op/blocked result: helper suppresses with
  `FOREIGN_REFERENCE`;
- recovered queued/running jobs retain whichever summary preceded recovery,
  so behavior depends on timing/history rather than immutable identity.

Independent SQLite and PostgreSQL probes called the real `_complete_job()`,
which called the real `_refresh_signal_summary()`. Both left decision count
zero.

The committed worker-ahead unit test copies the ingress summary and adds
fields, preserving `action`. The PostgreSQL script changes only status and
attempt values and disables the worker globally. Neither executes the real
summary replacement.

Finding record:

- File path:
  `backend/app/services/trading_canonical_decision_evidence.py`;
  `backend/app/workers/strategy_job_worker.py`;
  `backend/app/tests/test_r1b2b3_trading_decision_integration.py`;
  `backend/scripts/r1b2b3_pg_verify.py`.
- Function/class: `persist_trading_decision_best_effort`,
  `_complete_job`, `_refresh_signal_summary`,
  `test_worker_ahead_states_still_record_decisions`.
- Line range: helper 153-164; worker 104-158; test 248-291; PG script
  236-256.
- Observed behavior: real worker completion removes `action`, while tests
  preserve it synthetically.
- Risk: timing-dependent permanent gaps in immutable trading-decision
  evidence for fast worker completions, no-ops, blocks, and failures.
- Verdict: worker-ahead safety is disproven; this blocks approval.

## 12. Feature-flag behavior

Default/missing/empty/invalid remain false. The flag is server-side and absent
from API, webhook, Pine, prompt, and frontend surfaces. Literal `True` is
required and false returns before parsing/session access.

The “single evaluation governs the attempt” claim is not true end-to-end.
The helper reads the flag once at line 114, but
`persist_canonical_signal_decision()` reads the same setting again at writer
line 103. An independent PostgreSQL probe started the helper with `True`,
flipped the flag to `False` when the evidence session opened, and observed
zero decisions. Thus a change during helper execution changes the current
attempt.

Operational coupling remains:

```text
one shared flag enables HOLD + BUY_CE + BUY_PE + EXIT decision attempts
```

The review does not authorize enabling it.

Finding record:

- File path:
  `backend/app/services/trading_canonical_decision_evidence.py`;
  `backend/app/services/canonical_signal_decision_persistence.py`;
  `backend/app/tests/test_r1b2b3_trading_decision_integration.py`;
  `backend/scripts/r1b2b3_pg_verify.py`.
- Function/class: both persistence functions; flag-once tests.
- Line range: helper 112-116 and 186; writer 103-104; unit test 76-96; PG
  script 346-355.
- Observed behavior: tests count only the helper read or test false at entry;
  writer performs a later second read.
- Risk: mid-attempt flag changes create timing-dependent evidence gaps,
  contrary to the documented rule.
- Verdict: false/default/exposure behavior passes; single-evaluation claim
  fails.

## 13. Canonical mappings

**TRADING_INTENT_MAPPING_CONFIRMED**

Dynamic SQLite and PostgreSQL rows match:

| Action | Event / state / reason | Compatibility |
| --- | --- | --- |
| BUY_CE | STRATEGY_SIGNAL / BULLISH / DIRECTIONAL_SIGNAL | ENTRY / BUY / CE |
| BUY_PE | STRATEGY_SIGNAL / BEARISH / DIRECTIONAL_SIGNAL | ENTRY / BUY / PE |
| EXIT | STRATEGY_SIGNAL / FLAT / EXPLICIT_EXIT | EXIT / SELL / null |

All use `LIVE` and `nova.legacy-signal-adapter.v1`. Mapping is produced by
`adapt_legacy_action` and the reviewed writer. No field claims submission,
fill, position existence, exit success, reversal success, broker acceptance,
or position mutation.

Finding record:

- File path:
  `backend/app/services/trading_canonical_decision_evidence.py`;
  `backend/app/services/canonical_signal_decision_persistence.py`.
- Function/class: helper canonical-event construction; decision writer.
- Line range: helper 181-195; writer mapping/row construction.
- Observed behavior: exact accepted-intent mapping only.
- Risk: decision being mistaken for outcome.
- Verdict: mapping semantics are correct when a row is written.

## 14. Fingerprint provenance

**TRADING_COMMITTED_FINGERPRINT_CONFIRMED**

Chain verified:

```text
payload_fingerprint() canonicalizes server-side
→ replay claim commits metadata["payload_fingerprint"]
→ helper reloads WebhookEvent by committed ID
→ helper reads only committed metadata
→ writer shape-validates and stores exact value
```

The helper has no fingerprint parameter. For all three actions, tests and
PostgreSQL rows equal the committed event value and independent
recomputation; a 64-hex comment cannot substitute it. Missing/malformed
fingerprints suppress evidence only.

`payload_fingerprint`, replay `raw_body_sha256`, replay identity, and decision
conflict identity remain distinct.

Finding record:

- File path: `backend/app/services/private_webhook_service.py`;
  `backend/app/services/trading_canonical_decision_evidence.py`.
- Function/class: `payload_fingerprint`, `ingest`, trading helper.
- Line range: ingress fingerprint/claim metadata; helper 133-143 and 192.
- Observed behavior: exact committed event fingerprint is stored.
- Risk: user-selected provenance.
- Verdict: provenance is correct.

## 15. Paused/verification behavior

Ingress and worker code are unchanged. PAUSED and paper verification-mode
BUY_CE/BUY_PE/EXIT requests remain accepted at ingress, committing one signal
and job with the same 202 response. Decisions record accepted intent when the
helper wins the worker race; they do not imply execution.

The unconditional decision claim is not confirmed because a fast paused-entry
no-op or verification execution can complete and lose the `action` summary
before evidence reload. This is the same blocking race, not a new lifecycle
change.

Finding record:

- File path: `backend/app/services/private_webhook_service.py`;
  `backend/app/services/private_webhook_execution.py`;
  trading integration tests.
- Function/class: `validate_instance_lifecycle`, `_revalidate_instance`,
  paused/verification tests.
- Line range: lifecycle and test parameterizations.
- Observed behavior: authoritative acceptance unchanged; decision creation is
  race-dependent after worker completion.
- Risk: missing accepted-intent evidence on fast no-op paths.
- Verdict: ingress/worker behavior unchanged; decision guarantee blocked by
  section 11.

## 16. Response equivalence

**TRADING_RESPONSE_EQUIVALENCE_CONFIRMED**

The response dict is constructed before evidence and never receives an
evidence value. Dynamic tests cover flag off/on, all closed writer errors,
taint code, foreign references, DB/session outage, evidence rollback,
unexpected helper exception, and diagnostic failure across representative
ENTRY/EXIT paths. The shared call block is action-independent.

Observed response remains:

```json
{"ok": true, "signal_id": "...", "status": "QUEUED", "action": "..."}
```

with status 202 and `application/json`; duplicate behavior and job visibility
remain unchanged. No decision ID, evidence status, writer code, database
state, or retry state is exposed. The worker-race failure also preserves the
response, which is why it can create a silent evidence gap.

Finding record:

- File path: `backend/app/services/private_webhook_service.py`;
  `backend/app/tests/test_r1b2b3_trading_decision_failure_isolation.py`.
- Function/class: `ingest`, failure-isolation tests.
- Line range: service 466-491; failure tests 31-196.
- Observed behavior: evidence is response-invisible.
- Risk: evidence-dependent webhook acceptance.
- Verdict: response equivalence passes.

## 17. Failure isolation

**TRADING_DECISION_FAILURE_ISOLATION_CONFIRMED**

Closed writer errors, session creation failure, DB outage, rollback, unexpected
helper failure, and logger failure leave one durable signal/job, an unchanged
response, and no outcome/rejection. PostgreSQL confirms evidence rollback
cannot remove authoritative rows. Static imports exclude router, broker, and
position authority.

The implementation’s worker-race suppression is also isolated from execution,
but it is still a correctness failure because optional evidence becomes
timing-dependent.

Finding record:

- File path: trading helper, ingress, failure-isolation tests, PG script.
- Function/class: helper error envelope and ingress double suppression.
- Line range: helper 196-200; ingress 477-489; failure suite.
- Observed behavior: failures are metric-only and do not retry execution.
- Risk: authoritative rollback or duplicate execution.
- Verdict: isolation passes.

## 18. Secret-safe diagnostics

**TRADING_EVIDENCE_DIAGNOSTICS_SECRET_SAFE**

The logger receives only constant event text, closed writer version,
`ENTRY|EXIT`, and a clamped closed error code. Request, credential, IDs,
fingerprint, owner, database exception, SQL, and paths are never formatting
arguments. Logger failure is suppressed.

The committed test injects a credential-shaped comment and separately checks
signal ID and fingerprint absence. Static inspection generalizes this to the
remaining prohibited values because there is no data-bearing log parameter.

Finding record:

- File path:
  `backend/app/services/trading_canonical_decision_evidence.py`.
- Function/class: `record_trading_decision_failure`.
- Line range: 52-78.
- Observed behavior: closed diagnostic vocabulary only.
- Risk: secret/tenant/SQL leakage.
- Verdict: safe.

## 19. Duplicate/no-backfill

**TRADING_NO_BACKFILL_CONFIRMED**

For BUY_CE, BUY_PE, and EXIT, a fresh flag-off request creates signal/job but
no decision; later identical delivery under flag-on returns duplicate and
does not repair the decision. Conflicting duplicates leave one winner and no
rejected-request decision. Repository search found no repair scan, startup
hook, worker repair, scheduled repair, or historical backfill.

Finding record:

- File path: ingress; integration/concurrency tests; PG script.
- Function/class: duplicate branch and duplicate tests.
- Line range: ingress 432-443 and 472-476; relevant tests.
- Observed behavior: freshness remains call-site-owned.
- Risk: unauthorized historical repair.
- Verdict: no backfill.

## 20. Owner/reference isolation

Owner, instance, event provider, event metadata instance/action,
signal↔instance/event, and selected job↔signal/owner checks fail closed.
Foreign/missing event, owner, instance, HOLD event, and missing job probes
produce no cross-owner decisions.

Coverage is incomplete for the prompt’s full reference matrix, and the job
query omits the full unique key as described in section 9. Signal/job rows
remain intact because the helper performs no updates.

Finding record:

- File path: trading helper and failure-isolation tests.
- Function/class: helper reload/check block;
  `test_owner_and_reference_mismatch_never_create_evidence`.
- Line range: helper 120-179; tests 199-286.
- Observed behavior: checks fail closed, but job selection can be ambiguous.
- Risk: valid evidence suppression or selecting an unintended candidate before
  post-check rejection.
- Verdict: cross-owner writes are prevented; reload identity needs correction.

## 21. Signal/job immutability

**AUTHORITATIVE_SIGNAL_JOB_UNCHANGED**

The helper issues ordinary SELECTs without `FOR UPDATE`, modifies no signal/job
attribute, and passes them read-only to a writer that inserts only the
decision. Successful/failure tests and PostgreSQL row counts show no signal/job
mutation caused by evidence. Worker changes are independently authoritative,
not evidence changes.

Finding record:

- File path: trading helper; canonical decision writer.
- Function/class: both persistence functions.
- Line range: helper 123-195; writer insert envelope.
- Observed behavior: no signal/job writes or update locks.
- Risk: evidence changing execution authority.
- Verdict: unchanged.

## 22. Static call graph

**CANONICAL_DECISION_CALL_GRAPH_CONFIRMED**

AST/repository sweep counts:

```text
decision writer helper callers = 2
HOLD helper ingress callers = 1
trading helper ingress callers = 1
outcome writer production callers = 0
rejection writer production callers = 0
```

No aliases, dynamic imports, registries, callbacks, scheduler/startup hooks,
worker/router/risk/broker/position callers were found.

Finding record:

- File path: runtime services and static call-site tests.
- Function/class: persistence call graph.
- Line range: repository-wide AST sweep.
- Observed behavior: exact two-helper graph.
- Risk: evidence gaining execution authority.
- Verdict: graph is correct.

## 23. PostgreSQL verification

The committed `r1b2b3_pg_verify.py` genuinely uses PostgreSQL 18, migrations,
separate sessions, separate threads, barriers for identical ingress and direct
evidence races, real transaction commits, and rollback isolation. It passes
fresh mappings/fingerprints, duplicates, identical concurrency, direct unique
race, DB outage, rollback, authoritative inverse, and basic owner mismatch.
The disposable review container was removed.

However:

- `STRATEGY_JOB_WORKER_ENABLED=False`;
- “worker-ahead” mutates only status/attempt fields;
- no `_complete_job()` or `_refresh_signal_summary()` executes;
- conflicting duplicate is sequential, not a PostgreSQL concurrent race;
- direct-race caller outcomes are not collected;
- “flag once” tests only false at helper entry and later enablement;
- commit failure is a controlled rollback/raise rather than a database commit
  exception.

The independent PostgreSQL probe added during review used the real worker
completion functions and reproduced decision count zero. A second probe flipped
the flag after helper entry and also produced decision count zero.

Finding record:

- File path: `backend/scripts/r1b2b3_pg_verify.py`.
- Function/class: script worker-ahead and flag sections.
- Line range: 37; 236-256; 346-355.
- Observed behavior: script’s success message overstates worker/flag proof.
- Risk: false confidence in the highest-risk behavior.
- Verdict: ordinary PG transaction/concurrency evidence passes; worker-ahead
  and flag-once evidence is insufficient and contradicted independently.

## 24. Concurrency

Committed SQLite and PostgreSQL tests show, for each action, one fresh result,
one duplicate result, one replay identity, one signal, one job, and one
decision when the worker is disabled. SQLite concurrent conflict yields one
202, one 409, and a winner-matching decision. Direct helper racing leaves one
immutable decision.

PostgreSQL does not exercise a concurrent conflicting request, and its direct
race does not prove both callers resolved successfully. More importantly, an
actual concurrent worker completion can suppress the only evidence attempt.

Finding record:

- File path: concurrency tests; PG script; trading helper.
- Function/class: concurrent ingress/direct helper tests.
- Line range: concurrency suite entire file; PG 180-256.
- Observed behavior: ingress idempotency passes; worker concurrency is not
  safely handled.
- Risk: permanent decision gaps dependent on scheduler timing.
- Verdict: request idempotency passes; overall trading-decision concurrency is
  blocked by the worker race.

## 25. Test quality

| File | Count | Dynamic/static and runtime depth |
| --- | ---: | --- |
| `test_r1b2b3_trading_decision_integration.py` | 22 | Dynamic SQLite; real HTTP/TestClient and real helper on success; row/mapping/fingerprint/response assertions. Worker cases are synthetic and preserve `action`. |
| `test_r1b2b3_trading_decision_failure_isolation.py` | 22 | Dynamic SQLite; real HTTP and authoritative persistence; writer/session/helper/logger mocks inject failures; row/response/log assertions. Reference matrix and per-action failure matrix are incomplete. |
| `test_r1b2b3_trading_decision_concurrency.py` | 5 | Dynamic SQLite threads through real HTTP/helper; genuine thread scheduling, SQLite not authoritative for DB race semantics. |
| `test_r1b2b3_runtime_call_sites.py` | 6 | Static AST/hash checks in appropriate categories. |
| `test_r1b2a_zero_call_sites.py` | 4 | Updated static exhaustive boundary scan; equivalent strength. |
| `test_r1b2b_hold_decision_integration.py` | 11 | Updated dynamic HTTP HOLD/trading separation; HOLD behavior retained. |
| `test_r1b2b_runtime_call_sites.py` | 5 | Updated static exact two-helper set; HOLD guard unchanged. |
| `test_r1b_persistence_flags.py` | 26 | Mixed static exposure/confinement and dynamic forced-flag row counts. |
| `r1b2b3_pg_verify.py` | script | Real PostgreSQL for ordinary commits/races/rollback; synthetic worker and incomplete flag/concurrent-conflict probes. |

Source-only assertions are appropriately limited to call graph, imports,
guards, flag exposure, and HOLD hash. Success behavior runs the real helper.
Failure mocks generally replace only the failure surface.

The worker tests are behaviorally tautological with respect to the disputed
predicate: they preserve `result_summary["action"]`, which is exactly what the
helper requires, instead of invoking the function that removes it. The flag
test likewise counts only the helper’s setting object and cannot observe the
writer’s second read.

Finding record:

- File path: new integration tests and PG script.
- Function/class: worker-ahead and flag-once tests.
- Line range: integration 76-96 and 248-291; PG 236-256 and 346-355.
- Observed behavior: green tests can pass without executing the claimed race
  or mid-helper flag behavior.
- Risk: false-positive review evidence.
- Verdict: `R1B2B3_TEST_EVIDENCE_SUFFICIENT` is not established.

## 26. Regression results

At reviewed SHA `c45427c7f7ed48e85593518871c2746ac5aefb00`:

| Gate | Result |
| --- | --- |
| R1B-2B3 focused + complete HOLD suite | 87 passed |
| R1B-2A writer suite | 103 passed |
| Webhook/execution | 81 passed |
| Canonical/analyzer | 108 passed |
| Pine regression | 64 passed |
| Full backend, alone | 1083 passed |
| PostgreSQL 18 committed script | reports ALL PASS |
| Independent real-worker SQLite probe | reproduced missing decision |
| Independent real-worker PostgreSQL probe | reproduced missing decision |
| Independent mid-helper flag PostgreSQL probe | reproduced second-read gap |
| Alembic head | `0015_r1b_persistence` |
| TypeScript | pass |
| Vitest | 20 passed |
| Frontend build | pass |
| Ruff on all changed runtime/test/script files | pass |
| Changed-file Python compilation | pass |
| `git diff --check` before report | pass |

## 27. Scope-creep result

**SCOPE_CLEAN**

The implementation changes only the trading ingress call block, new trading
helper, tests, PG probe, and documentation. No runtime code outside the two
expected service files changed. No migration, model, HOLD helper, replay,
credential, worker, execution, router, risk, broker, Dhan, position,
frontend, prompt, or transport file changed.

Finding record:

- File path: complete `55deb91..c45427c` diff.
- Function/class: repository scope.
- Line range: all 12 changed files classified in section 3.
- Observed behavior: no unexplained production path.
- Risk: unauthorized execution or schema change.
- Verdict: no scope creep.

## 28. Blocking findings

### 1. Real worker completion removes the helper’s required action

- File path:
  `backend/app/services/trading_canonical_decision_evidence.py`;
  `backend/app/workers/strategy_job_worker.py`.
- Function/class: `persist_trading_decision_best_effort`,
  `_refresh_signal_summary`.
- Line range: helper 153-164; worker 123-158.
- Observed behavior: helper requires `summary["action"]`; worker replaces the
  summary without that key; independent SQLite/PostgreSQL probes yield zero
  decisions.
- Risk: scheduler-speed-dependent permanent evidence gaps.
- Verdict: blocking.

### 2. Execution-job reload omits part of the unique key

- File path:
  `backend/app/services/trading_canonical_decision_evidence.py`;
  `backend/app/db/models.py`.
- Function/class: helper job query; `StrategyExecutionJob.__table_args__`.
- Line range: helper 166-179; model 415-422.
- Observed behavior: query uses two columns while uniqueness is three-column
  `(strategy_name, signal_id, user_id)`.
- Risk: nondeterministic candidate selection and valid-evidence suppression.
- Verdict: blocking reference-reload discrepancy.

### 3. Shared flag is evaluated again inside the writer

- File path: trading helper; canonical decision writer.
- Function/class: both persistence functions.
- Line range: helper 112-116/186; writer 103-104.
- Observed behavior: helper entry reads once, writer later re-reads; PG probe
  flipped between reads and produced no decision.
- Risk: documented single-evaluation contract is false.
- Verdict: blocking contract/test discrepancy.

### 4. Highest-risk tests do not execute the claimed behavior

- File path: trading integration tests; PG script.
- Function/class: worker-ahead and flag-once probes.
- Line range: test 248-291 and 76-96; PG 236-256 and 346-355.
- Observed behavior: synthetic mutations preserve the predicate; worker is
  disabled; flag test cannot observe writer re-read.
- Risk: false green review evidence.
- Verdict: blocking test-evidence gap.

## 29. Non-blocking findings

1. Existing FastAPI/Starlette TestClient deprecation warning.

   - File path: installed dependency, not repository runtime.
   - Function/class: test client compatibility layer.
   - Line range: dependency import.
   - Observed behavior: one warning in pytest runs.
   - Risk: future test dependency maintenance.
   - Verdict: pre-existing, non-blocking.

2. Existing Vite chunk-size warning.

   - File path: generated frontend build output.
   - Function/class: Vite production bundling.
   - Line range: generated bundle.
   - Observed behavior: main chunk exceeds 500 kB warning threshold.
   - Risk: frontend performance/maintainability only.
   - Verdict: pre-existing and unrelated, non-blocking.

## 30. R1B-2C readiness

Not ready. Before a new independent review:

1. remove every worker-mutable signal/job field from decision eligibility;
2. bind accepted action to immutable committed ingress/job identity;
3. make the job reload match a real unique constraint;
4. resolve or accurately document the shared writer/helper flag re-read;
5. replace synthetic worker tests with real `_complete_job()` /
   `_refresh_signal_summary()` execution on SQLite and PostgreSQL;
6. add genuine PostgreSQL concurrent-conflict and both-caller unique-race
   assertions;
7. complete the foreign signal/job/reference and response/failure matrices.

No runtime code changed during review. No test logic changed during review. No
migration changed. The reviewed HOLD path remains unchanged. Only fresh
committed BUY_CE, BUY_PE and EXIT reach the trading evidence call site.
Trading evidence executes after signal and job commit and uses a separate
database session. Evidence failure cannot alter the webhook response.
Duplicate requests do not repair missing decisions. Decisions record intent
and do not imply execution success. Outcome and rejection writers remain
disconnected. No router, broker, or position authority changed. The
decision-persistence flag remains disabled. No prompt or transport changed. No
AI conversion was enabled. No live order occurred. No merge or deployment
occurred.
