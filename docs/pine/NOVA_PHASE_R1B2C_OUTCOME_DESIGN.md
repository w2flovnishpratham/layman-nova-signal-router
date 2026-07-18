# NOVA Phase R1B-2C — Canonical Execution Outcome Design

## 1. Executive recommendation

**Verdict: `BLOCKED_SCHEMA`.**

The execution outcome natural key is identified as
`StrategyExecutionJob.id`, and the safe evidence point is identified as the
return from `_complete_job()` after both its job commit and its subsequent
signal-summary commit. Stable, closed result combinations can be normalized
from the committed job row without consulting broker text.

Implementation is not authorized, however, because the current database and
writer contradict the required optional, one-final-outcome-per-job model:

1. `canonical_signal_outcomes.canonical_decision_id` is non-null and the writer
   requires a `CanonicalSignalDecision`. Decision evidence is optional and its
   flag must remain disabled, so an outcome cannot be independently written.
2. `execution_job_id` is nullable and non-unique. The writer hashes the
   decision, phase, job, attempt, routing result and detail, so two conflicting
   final interpretations for one job deliberately create two rows.
3. The worker can persist an uncertain broker/position conclusion as a
   `completed` job, while reconciliation can later change JSON position state
   without a job link. Such jobs must be outcome-ineligible, but there is no
   later authorized finalization/backfill path.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `backend/app/db/models.py`, `CanonicalSignalOutcome`, lines 1348-1416; `backend/app/services/canonical_signal_outcome_persistence.py`, `persist_canonical_signal_outcome`, lines 123-208 | Non-null decision FK; nullable/non-unique job FK; full-observation idempotency; append-many semantics. | Migration 0016 must make decision linkage optional and enforce at most one row for each non-null authoritative job. The writer must return the identical winner and reject a conflicting winner. | Evidence remains a separate post-authority transaction. | Missing decisions suppress all outcomes; conflicting final rows are legal. | PostgreSQL decision-absent insert, identical race, and conflicting race. |
| `backend/app/workers/strategy_job_worker.py`, `_complete_job` / `_refresh_signal_summary`, lines 104-158 | Job and signal commit in two successive sessions; terminal result can be overwritten by another direct completion callback. | Invoke evidence only after `_complete_job` returns, and first make terminal completion a single-winner conditional transition in the later implementation. | Job commit → signal-summary commit → independent evidence session. | Premature evidence or a later result overwrite makes immutable evidence false. | Barriers around two terminal completions; only the winning committed result may reach evidence. |
| `backend/app/services/execution_router.py`, `_place_order`, lines 1551-1748; `backend/app/services/position_reconciler.py`, `get_reconciled_open_position`, lines 155-286 | Pending/unknown orders and local position corrections are possible after the worker result. | Closed adapter suppresses final outcome for unresolved broker status, missing position-transition proof, and reconciliation-dependent results. | JSON/broker effects precede job commit; reconciliation is a later independent path. | An immutable “achieved” outcome can be invalidated later. | Pending, unknown, position-write failure, and later reconciliation tests assert zero outcome. |

Required design findings:

```text
OUTCOME_NATURAL_KEY_IDENTIFIED = StrategyExecutionJob.id
POST_COMMIT_OUTCOME_POINT_IDENTIFIED = after _complete_job() returns
COMMITTED_OUTCOME_SOURCE_IDENTIFIED = committed StrategyExecutionJob, stable closed combinations only
REVERSAL_OUTCOME_MODEL_VALID = stable final reversal states only
MIGRATION_0016_REQUIRED = yes
IMPLEMENTATION_AUTHORIZED = no
```

## 2. Current execution lifecycle map

Common accepted path:

```text
private_webhook_service.ingest
→ committed WebhookEvent replay claim
→ committed StrategySignal + StrategyExecutionJob
→ worker claim (queued → running, attempts + 1)
→ private_webhook_execution.execute_private_job
→ strategy_fanout.dispatch_signal_job
→ strategy risk reservation
→ execution_router.route_signal
→ router risk + broker + atomic JSON position write
→ committed UserRun result
→ committed StrategyExecutionJob result
→ committed StrategySignal summary
```

The ingress transaction is at
`backend/app/services/private_webhook_service.py`,
`_persist_execution_job`, lines 586-634. The worker is at
`backend/app/workers/strategy_job_worker.py`, `_claim_jobs`, `_execute_job`,
`_complete_job`, and `_refresh_signal_summary`, lines 78-215. The private
router bridge is at `backend/app/services/private_webhook_execution.py`,
`execute_private_job`, lines 167-214. `session_scope()` commits only on context
exit (`backend/app/db/engine.py`, lines 124-134).

Outcome notation below is:
`phase / routing_result / current_state / no_op_reason / exit_reason / safe_detail`.
`—` means null. “Suppress” means no canonical outcome row, not a new outcome
enum.

| # / flow | Entry, router, risk, broker and position path | Committed authority, statuses and terminality | Proposed outcome, call point and isolation | Risk and required test |
|---|---|---|---|---|
| 1. Fresh BUY_CE, paper | `execute_private_job` 167-214 → `dispatch_signal_job` 752-908 → `route_entry_signal` 2156-2243 → `PaperBroker._place` 143-204 → `set_open_position` 356-400. Fanout risk is 810-833; router risk is 2202-2208. | Job `completed`; signal `completed`; outer result `completed`; nested `ORDER_PLACED`, `broker_status=TRADED`; JSON CE position is durably replaced before job commit. Stable terminal. | `ROUTING_COMPLETED / ENTRY_ROUTED / BULLISH / — / — / ENTRY_TRADED`. Evidence after `_complete_job` returns; evidence failure is swallowed. | Risk: order submission must not equal fill. Test exact nested `broker_status=TRADED`, CE JSON, one row, and injected evidence failure. |
| 2. Fresh BUY_PE, paper | Same functions as flow 1 with server-resolved PE contract (`private_webhook_execution.py`, `_resolve_entry_contract`, 97-138). | Same statuses; JSON PE position. Stable terminal. | `ROUTING_COMPLETED / ENTRY_ROUTED / BEARISH / — / — / ENTRY_TRADED`. | Test PE contract, fill, achieved state and no broker replay. |
| 3. Fresh BUY_CE, live | `dispatch_signal_job` 785-808 applies entitlement/arming/egress/credential gates; `_place_order` 1264-1748 claims live intent, calls Dhan, polls, then `route_entry_signal` writes JSON. | Only `broker_status=TRADED` plus successful JSON write is stable. Job `completed`, signal `completed`. | Same semantic mapping as flow 1; provenance remains on committed `job.execution_mode=real_orders`, not copied from Dhan text. | Test mocked closed Dhan TRADED result, durable JSON, no raw Dhan material in outcome. No live order. |
| 4. Fresh BUY_PE, live | Same as flow 3, PE. | Same status model, achieved PE position. | Same as flow 2. | Test closed PE live result and secret-taint exclusion. |
| 5. EXIT with open matching position | Pre-checks at `private_webhook_execution.py` 193-205; `route_exit_signal` 2246-2328; broker SELL; `clear_open_position` 375-400. | Job `completed`; signal `completed`; nested `ORDER_PLACED` + `broker_status=TRADED`; JSON flat. Stable terminal. | `ROUTING_COMPLETED / EXIT_ROUTED / FLAT / — / EXPLICIT_EXIT / EXIT_TRADED`. | Test open→flat, no outcome before JSON write and no execution change on evidence failure. |
| 6. EXIT while flat | `execute_private_job` 196-203 or enricher 146-152 returns `NO_OP/ALREADY_FLAT`; no risk, run or broker. | Job `completed`; signal `completed`; direct result `NO_OP`, `success=true`, `no_op=true`. Stable terminal. | `STATE_EVALUATED / STATE_NO_OP / FLAT / ALREADY_FLAT / EXPLICIT_EXIT / ALREADY_FLAT`. | Test no broker/run, exact no-op and one final row. |
| 7. BUY_CE already bullish | Pre-check 196-205 or enricher 153-155. | Job/signal `completed`; direct `NO_OP/ALREADY_IN_CE`. Stable terminal. | `STATE_EVALUATED / STATE_NO_OP / BULLISH / ALREADY_BULLISH / — / ALREADY_IN_CE`. | Test no scale-in/risk/broker and exact mapping. |
| 8. BUY_PE already bearish | Same as flow 7 for PE. | Direct `NO_OP/ALREADY_IN_PE`; stable terminal. | `STATE_EVALUATED / STATE_NO_OP / BEARISH / ALREADY_BEARISH / — / ALREADY_IN_PE`. | Test no scale-in and exact mapping. |
| 9. BUY_CE while bearish | `route_entry_signal` 2189-2200 → `route_reversal_signal` 1929-2116; exit PE then enter CE. | Final status depends on the two broker legs and JSON writes. | Use flows 11-13; never emit an intermediate outcome. | Test exit-before-entry call order and one row maximum. |
| 10. BUY_PE while bullish | Same reversal path, CE→PE. | Same two-leg model. | Use flows 11-13. | Test symmetric direction mapping. |
| 11. Reversal exit and entry succeed | `route_reversal_signal` 1963-2098: exit TRADED, clear JSON 2029-2033, entry success, set target JSON 2059-2067. | Job/signal `completed`; nested `REVERSAL_ORDER_PLACED` (or stable partial entry); target position is authoritative. | Full fill: `ROUTING_COMPLETED / REVERSAL_ROUTED / target state / — / REVERSAL_EXIT / REVERSAL_COMPLETED`. | Test one outcome for complete two-leg reversal; never rows for “exit started” and “entry started.” |
| 12. Reversal exit succeeds, entry fails | Exit clears JSON at 2029-2033; entry terminal failure returns `REVERSAL_ENTRY_FAILED` at 2100-2116. | Outer result `error`; job `completed`; signal `completed_with_errors`; JSON is flat. Stable only when entry failure is closed and non-uncertain. | `ROUTING_FAILED / PARTIAL / FLAT / — / REVERSAL_EXIT / REVERSAL_ENTRY_FAILED`. Entry-blocked after the exit uses `PARTIAL/FLAT/REVERSAL_EXIT` with only a reviewed closed detail or null. | Test flattened-but-entry-failed, blocked-vs-failed distinction, one row. |
| 13. Reversal exit fails | `route_reversal_signal` 1968-1994; entry is never sent and original JSON remains. | Outer `error`; job `completed`; signal `completed_with_errors`; nested `REVERSAL_EXIT_FAILED`. Stable only for closed rejection/failure, not timeout unknown. | `ROUTING_FAILED / FAILED / original state / — / REVERSAL_EXIT / REVERSAL_EXIT_FAILED`. | Test original position retained and zero entry calls. Unknown exit status must suppress. |
| 14. Entry blocked because instance paused | `_revalidate_instance` 59-94 returns `NO_OP/INSTANCE_PAUSED_ENTRIES_BLOCKED` before fanout. | Job/signal `completed`; no broker or position mutation. Stable terminal no-op. | `STATE_EVALUATED / STATE_NO_OP / UNKNOWN / INSTANCE_PAUSED / — / PAUSED_ENTRY_BLOCKED`. `UNKNOWN` is required because the committed result carries no position snapshot. | Test paused CE/PE, no reversal, no state guessed from a later JSON read. |
| 15. EXIT while paused | `_revalidate_instance` blocks only `action=="ENTRY"` at 76-77; EXIT follows flow 5 or 6. | Same terminal statuses as the applicable EXIT flow. | Same mapping as flow 5/6; do not invent a pause result. | Test paused EXIT remains allowed and outcome semantics unchanged. |
| 16. Verification-mode entry | `_revalidate_instance` 79-89 allows verification only with `paper_live_data`; normal paper routing follows. | Job/signal and result match flow 1/2. The job retains paper mode but not an immutable verification-mode snapshot. | Map achieved paper result as flow 1/2. Do not use `VERIFICATION_PAPER_ONLY` unless a committed immutable source is later added. | Test READY+verification paper entry and no live path; do not read mutable current instance mode as history. |
| 17. Verification-mode EXIT | Same verification gate and normal EXIT route. | Same as flow 5/6. | Same as flow 5/6; no special verification classification. | Test genuine paper close and flat no-op. |
| 18. Risk-manager rejection | Fanout risk `strategy_fanout.py` 810-833 or router risk `execution_router.py` 1950-1957, 2202-2204, 2268-2270. No broker/position write. | Wrapper `blocked`; job `completed`; signal usually `completed` because summary treats only job `failed` or outer `error` as errors (`strategy_job_worker.py` 146-151). Stable terminal block. | `STATE_EVALUATED / BLOCKED / UNKNOWN / — / optional RISK_EXIT for an exit / RISK_BLOCKED`. | Test both risk layers and prove no order/position call. |
| 19. Contract invalid/unavailable | `_resolve_entry_contract` 97-138 → enricher failure 156-161; fanout wraps it as outer `error`. | Job `completed`; signal `completed_with_errors`; nested `FAILED` + `block_code=CONTRACT_RESOLUTION_FAILED`. No broker. Stable terminal. | `ROUTING_FAILED / FAILED / UNKNOWN / — / — / CONTRACT_RESOLUTION_FAILED`. | Test no guessed contract, no broker and exact closed code. |
| 20. Broker submission failure | `_place_order` 1484-1520 and result normalization 1666-1748; route failure at 2240-2243 or 2325-2328. | Closed API failure produces outer `error`, job `completed`, signal `completed_with_errors`. Position remains prior state if no JSON write. | `ROUTING_FAILED / FAILED / UNKNOWN / — / action-appropriate exit / ENTRY_REJECTED or EXIT_REJECTED` only for closed non-uncertain status. | Test API error/rejection without free-form message classification. |
| 21. Broker rejection | Dhan non-2xx returns `REJECTED` (`dhan_client.py`, `place_order`, 540-576); PaperBroker returns `REJECTED` at 143-180. | Same as flow 20; terminal when broker rejection is closed. | Same as flow 20. | Test paper and live normalized rejection share canonical semantics. |
| 22. Broker timeout or uncertain response | Dhan timeout recovery can return `ORDER_STATE_UNKNOWN` (`dhan_client.py` 318-399); polling can return non-terminal (`dhan_client.py` 930-1016); router marks `PENDING_CONFIRMATION`/`PARTIAL_FILL_PENDING` at 1595-1598. | Worker may still store `completed`; route may also create tracking JSON because accepted pending is `success=true`. Conclusion can change. | **Suppress outcome.** Never map top-level `ORDER_PLACED` without checking nested `broker_status`. | Test timeout recovered, unknown, pending and partial-pending all write zero outcomes. |
| 23. Paper broker failure | `PaperBroker._place` 143-180 rejects unavailable quote or paper-ledger error. | Outer `error`; job `completed`; signal `completed_with_errors`; no stable JSON transition. | Closed rejection may map as flow 20; any paper-ledger/JSON split suppresses. | Test quote failure, ledger failure, no Dhan call and no raw paper response. |
| 24. Position-state write failure | Entry/exit JSON write at router 2212-2218/2287-2307 can raise after broker or paper-ledger effects. `dispatch_signal_job` catches it at 879-881 and stores only outer `error` text. | Job `completed`; signal `completed_with_errors`; broker may have acted while JSON authority did not. No closed stage proof. | **Suppress outcome.** Generic `WORKER_FAILED` would be materially false. | Inject `set_open_position`/`clear_open_position` failure after broker success; assert execution durable/uncertain, zero outcome. |
| 25. Worker exception before authoritative completion | `_execute_job` catches validation/private-dispatch exceptions at `strategy_job_worker.py` 175-192. | Job `failed`, `last_error` free-form; signal `completed_with_errors`; no closed proof of how far execution reached. | Suppress unless future code commits a closed `order_sent=false` stage code. | Test parser exception and a post-broker exception separately; neither may be inferred from text. |
| 26. Retryable worker failure | `recover_stale_jobs` 48-75 requeues running jobs when attempts remain. | Job `queued`; signal may remain stale/processing; not terminal. | No outcome. | Test running→queued, no helper/session. |
| 27. Terminal worker failure | Normal caught failure is flow 25; exhausted stale job is set `failed` at 65-68 without `_complete_job` or signal refresh. | Terminal job, but outcome unknown and signal may be stale. | No outcome for exhausted/manual-review failure. A later closed terminal-stage model is prerequisite for `WORKER_FAILED`. | Test max-attempt stale recovery, no outcome and no signal mutation by evidence. |
| 28. Duplicate worker claim | PostgreSQL `_claim_jobs` uses `FOR UPDATE SKIP LOCKED` at 78-101; user lock is process-local at 172-174. | Normal claim has one running winner. `_complete_job` itself does not reject a second terminal callback. | Future terminal transition must be conditional single-winner; only winner calls helper. | PostgreSQL two-worker barrier plus direct conflicting completion callback test. |
| 29. Reconciliation correction | `position_reconciler.py`, `get_reconciled_open_position`, 155-286 mutates JSON/sync metadata and emits position events, but has no job reference. | Existing job/result is unchanged; correction can occur after terminal job commit. | Reconciliation is outside R1B-2C. Uncertain jobs get no outcome; no repair/backfill. | Test later broker-flat correction leaves zero outcome and no job rewrite. |
| 30. EOD square-off | `workers/eod_squareoff.py`, `_try_flatten_once`, 129-207 builds an internal signal and calls `route_signal` directly. | No StrategySignal or StrategyExecutionJob row; retries within its own time window. | Out of scope. | Static test forbids helper/writer calls from EOD worker. |
| 31. Manual/admin exit | `routers/orders.py`, `manual_exit`, lines 137-183 calls `route_signal` directly; panic/control and option monitor are other direct callers. | No private-webhook StrategyExecutionJob. | Out of scope. | Static caller test forbids manual/control/monitor integration. |
| 32. Signal superseded | `StrategySignal`/`StrategyExecutionJob` models at `db/models.py` 393-454 contain no superseded state; worker has no supersede transition. | Unsupported. | No outcome design. | Static/status inventory test proves no hidden supersede path. |
| 33. Stale or cancelled job | Stale behavior is flows 26/27. No cancelled job transition exists in `strategy_job_worker.py` 48-215 or model vocabulary 412-454. | Requeued or failed; no `cancelled` state. | No outcome for requeued or manual-review stale jobs; cancelled unsupported. | Test status inventory and stale branches. |

Every flow uses the same isolation rule: the future helper may only observe
committed identifiers, must open its own session, and its failure must not
change the job, signal, run, broker action, JSON position, worker return or
HTTP response.

## 3. Outcome definition

One R1B-2C row is intended to be one immutable, final, server-normalized
conclusion for one accepted private-webhook `StrategyExecutionJob`. It is not
one broker order, one attempt, one reversal leg, or one mutable position
observation. HOLD remains outside this phase.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `canonical_signal_outcome_persistence.py`, module contract and writer, lines 1-12 and 123-208 | Current library describes append-only observations and supports multiple phases/attempts. | R1B-2C uses only a single final row per job. Intermediate routing observations are not written. | Final row starts after committed terminal authority. | Multiple observations can be mistaken for one final truth. | Static mapping test rejects intermediate phase calls. |
| `execution_router.py`, reversal, lines 1929-2116 | One job can make two broker calls and multiple JSON transitions. | One row represents the complete stable reversal conclusion. | Both legs and final JSON must precede job commit/evidence. | Per-leg evidence can survive an incomplete reversal and mislead. | Reversal barrier and one-row assertion. |

## 4. Existing outcome schema and writer

Supported database fields are `canonical_decision_id`, `execution_job_id`,
`attempt`, `phase`, `current_state`, `routing_result`, `no_op_reason`,
`exit_reason`, `safe_detail_code`, `result_sha256`, `idempotency_key`, and
`created_at`.

Closed database vocabularies:

```text
phase:
  STATE_EVALUATED, ROUTING_COMPLETED, ROUTING_FAILED
current_state:
  UNKNOWN, FLAT, BULLISH, BEARISH
routing_result:
  NOT_EVALUATED, CONNECTIVITY_NO_JOB, STATE_NO_OP, ENTRY_ROUTED,
  EXIT_ROUTED, REVERSAL_ROUTED, BLOCKED, FAILED, PENDING, PARTIAL
no_op_reason:
  CONNECTIVITY_TEST, ALREADY_FLAT, ALREADY_BULLISH,
  ALREADY_BEARISH, INSTANCE_PAUSED
exit_reason:
  EXPLICIT_EXIT, REVERSAL_EXIT, STOP_LOSS, TAKE_PROFIT,
  TRAILING_STOP, SESSION_EXIT, MANUAL_EXIT, RISK_EXIT
```

`safe_detail_code` has no database CHECK, but the reviewed writer restricts it
to the allowlist at `canonical_signal_outcome_persistence.py`, lines 64-96.
The database has no cross-field constraints; therefore syntactically closed
but semantically contradictory combinations remain database-legal.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `db/models.py`, `CanonicalSignalOutcome`, lines 1354-1412; migration `backend/alembic/versions/20260718_0015_r1b_persistence.py`, lines 185-257 | Per-field CHECKs only; decision required; job optional; unique full idempotency hash only. | Migration 0016 must encode optional decision and unique non-null job identity; adapter/writer must enforce a reviewed cross-field matrix. | Schema enforcement is inside evidence transaction only. | Legal nonsense combinations and conflicting final rows. | Direct SQL CHECK/unique tests plus writer matrix tests. |
| `canonical_signal_outcome_persistence.py`, lines 140-183 | Writer validates each enum separately and stores `result_sha256=None`. | Keep raw result excluded; add closed cross-field validation and optional decision binding in the later implementation. | Writer still does not commit. | Caller can pair `ENTRY_ROUTED` with `ALREADY_FLAT`. | Parametrized invalid-combination tests. |

## 5. Natural key and idempotency

`OUTCOME_NATURAL_KEY_IDENTIFIED`: `StrategyExecutionJob.id`.

A signal may have multiple jobs in general fanout, but a private instance
signal creates exactly one owner job. A job can have up to two attempts, and a
reversal can have two broker orders. Those facts do not change the final
evidence identity.

The existing writer key is:

```text
sha256(
  namespace,
  decision_id,
  phase,
  execution_job_id,
  attempt,
  routing_result,
  safe_detail
)
```

That is an observation identity, not a final-job identity.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `canonical_signal_outcome_persistence.py`, `outcome_idempotency_key`, lines 104-120; `test_canonical_signal_outcome_persistence.py`, lines 98-137 | Phase, attempt, result and detail variants intentionally create distinct rows, including two reversal rows. | Add a unique partial index/constraint for non-null `execution_job_id`. Identical derived fields return the winner; different fields raise closed `CANONICAL_OUTCOME_CONFLICT`. | PostgreSQL unique arbitration occurs only in evidence sessions. | Two callers can currently publish contradictory final truths. | Two-thread identical and conflicting races; collect both caller results and assert one row. |
| `db/models.py`, `StrategyExecutionJob`, lines 415-453 | Private unique key is strategy name + signal id + user; job attempts are mutable. | Helper signature uses `strategy_execution_job_id` plus owner/instance identifiers; it reloads and validates the unique chain. | No uncommitted ORM object crosses sessions. | Using signal alone collapses general fanout users. | Two jobs for one signal produce distinct outcomes. |

No `latest`, `first`, ordering or `limit(1)` query may resolve ambiguity.

## 6. Authoritative terminal commit boundary

`POST_COMMIT_OUTCOME_POINT_IDENTIFIED`: immediately after `_complete_job(job,
...)` returns in the one future guarded terminal-worker integration path.

The order is:

```text
broker/closed result and JSON transition return
→ UserRun update commits
→ StrategyExecutionJob terminal row commits in _complete_job session
→ StrategySignal summary commits in _refresh_signal_summary session
→ _complete_job returns
→ best-effort outcome helper checks flag and opens a new session
```

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `strategy_job_worker.py`, `_complete_job`, lines 104-120; `_refresh_signal_summary`, 123-158 | Job commit and signal-summary commit are separate. | Call helper only after `_complete_job` returns. | Strictly post-both-commits, separate session. | Calling between the commits observes a stale signal; calling inside can roll back authority. | Commit-failure injection before/after each boundary. |
| `state_store.py`, `_atomic_write_json` and setters, lines 203-231, 306-400 | JSON position uses temp-file replacement and completes before router returns. DB shadow failure is deliberately non-authoritative. | Stable mapping requires the router result to prove the intended JSON transition completed. | File authority precedes SQL job result; no distributed transaction exists. | Broker action can precede a failed JSON write. | Broker success + JSON failure gives zero outcome. |

## 7. Terminal-state eligibility matrix

| Committed job/result state | Classification | Outcome | Evidence, transaction, risk and test |
|---|---|---|---|
| `queued` | Retry pending | No | `strategy_job_worker.py` 78-101. Uncommitted execution; test helper opens no session. |
| `running` | Processing/retry possible | No | Same file 95-99. Test concurrent helper suppression. |
| `completed` + stable closed no-op/block | Terminal no-op/block | Yes after `_complete_job` | Result must match allowlist. Risk is free-form reasons; test closed-code-only mapping. |
| `completed` + TRADED + completed JSON transition | Terminal achieved | Yes after `_complete_job` | Router 1551-1748, 2212-2238, 2287-2323. Test committed row reload. |
| `completed` + closed rejection before state change | Terminal failure | Yes | Test `REJECTED/CANCELLED/EXPIRED` and unchanged state proof. |
| `completed` + `PENDING_CONFIRMATION`, `PARTIAL_FILL_PENDING`, `ORDER_STATE_UNKNOWN` | Uncertain/non-final | No | Router 1595-1598, 1704-1715. Test zero row. |
| `completed` + wrapper `error` but no nested closed execution result | Terminal job, outcome unknown | No | `strategy_fanout.py` 879-881. Could be post-broker JSON failure; test suppression. |
| `failed` + free-form `last_error` | Terminal job, outcome unknown | No | Worker 189-192. Do not classify exception text; test suppression. |
| stale `running→queued` | Retry pending | No | Worker 64-73. |
| stale exhausted `running→failed` | Manual-review unknown | No | Worker 65-68; no signal refresh/call point. |

`StrategySignal.status` is corroborating only. It aggregates jobs and treats
outer `blocked` differently from outer `error`; it is not outcome authority.

## 8. Committed outcome source

For eligible stable results, the source is the reloaded committed
`StrategyExecutionJob`:

```text
id, strategy_signal_id, user_id, strategy_name, signal_id,
signal_payload (accepted action envelope), execution_mode,
status, attempts, completed_at, result_summary, last_error
```

Classification:

| Source | Class | Evidence, observed/proposed behavior, transaction, risk and test |
|---|---|---|
| Terminal job status + closed nested result codes | Authoritative for stable allowlisted combinations | `db/models.py` 426-450; reload post-commit. Risk: row is application-mutable. Test conditional terminal-winner prerequisite. |
| Job `signal_payload` accepted action envelope | Authoritative accepted-action identity, application-immutable | `trading_canonical_decision_evidence.py` 158-194. Never accept action from helper caller. Test event/job action mismatch. |
| Job `execution_mode` | Authoritative job provenance | `models.py` 440. Use for validation/join, not broker-specific semantics. Test paper/live same mapping. |
| Signal `result_summary` | Derived, mutable aggregate | Worker 137-158. Do not classify from it. Test adversarial signal summary. |
| JSON position read after the fact | Mutable and timing-sensitive | `state_store.py` 306-400. Do not infer historical transition by a later read. Test a newer signal changes JSON before helper reload. |
| Position events | Diagnostic/typed shadow, not R1B-2C authority | `position_operations.py`; focused test exposed nondeterministic event ordering. Do not order by UUID/current query order. Test outcome adapter has no import. |
| Raw broker response/error text | Untrusted/diagnostic | Router 1666-1701 stores it inside the job result. Adapter ignores it. Taint tests. |
| Live broker fetch after job | Mutable external state | Reconciler 155-286. Forbidden as immediate outcome input. Static import test. |

`COMMITTED_OUTCOME_SOURCE_IDENTIFIED` therefore applies only to the stable
closed allowlist. Uncertain or structurally incomplete results are suppressed.

## 9. Result normalization design

Design a pure, versioned adapter:

```text
nova.execution-outcome-adapter.v1

normalize_committed_execution_job(job, accepted_action)
  -> CanonicalOutcomeFields | INELIGIBLE
```

The adapter receives a reloaded ORM snapshot internally, not caller-provided
classification. It first validates the job terminal winner and private
instance identity, then matches exact structural shapes:

1. Direct private no-op/block result.
2. Fanout wrapper with closed `status` and nested `execution_result`.
3. Nested router `status`, `broker_status`, `success`, `blocked`,
   `partial_fill`, and closed reversal structure.
4. Closed server `block_code`/normalized failure class when allowlisted.

It must reject unsupported key combinations and ignore `reason`, `error`,
`raw_response`, `orderJourney`, broker IDs, credentials and request payloads.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `strategy_fanout.py`, `dispatch_signal_job`, lines 752-908 | Result shapes differ between pre-dispatch return and nested router return; exception text can be stored. | Structural allowlist, no message parsing. | Adapter runs after reloading committed job in evidence session. | A generic `status=completed` is not execution success. | Golden mapping corpus plus unknown-key/shape fail-closed tests. |
| `execution_router.py`, `_place_order`, 1666-1748 | Raw response and normalized codes coexist; top route status can overwrite broker pending status. | Require nested `broker_status=TRADED` for achieved full entry/exit; explicitly reject uncertain statuses. | Read-only normalization before writer. | `ORDER_PLACED` can conceal `PENDING_CONFIRMATION`. | Pending-under-ORDER_PLACED regression test. |

## 10. BUY_CE mapping

| Conclusion | Exact mapping | Evidence / transaction / risk / required test |
|---|---|---|
| New full achieved entry | `ROUTING_COMPLETED, ENTRY_ROUTED, BULLISH, null, null, ENTRY_TRADED` | Router 2212-2238 and JSON setter 356-400; post-job commit. Risk: submission vs fill. Test requires `broker_status=TRADED`. |
| Stable terminal partial entry | `ROUTING_COMPLETED, PARTIAL, BULLISH, null, null, PARTIAL_FILL_REMAINS_OPEN` | Router 1602-1605, 2212-2238. Only terminal partial, never `PARTIAL_FILL_PENDING`. Test remaining broker order is not open. |
| Same-direction no-op | `STATE_EVALUATED, STATE_NO_OP, BULLISH, ALREADY_BULLISH, null, ALREADY_IN_CE` | Private pre-check 193-205; no broker transaction. Test zero routing. |

## 11. BUY_PE mapping

| Conclusion | Exact mapping | Evidence / transaction / risk / required test |
|---|---|---|
| New full achieved entry | `ROUTING_COMPLETED, ENTRY_ROUTED, BEARISH, null, null, ENTRY_TRADED` | Router 2212-2238; post-commit. Test requires TRADED + PE JSON. |
| Stable terminal partial entry | `ROUTING_COMPLETED, PARTIAL, BEARISH, null, null, PARTIAL_FILL_REMAINS_OPEN` | Same code. Test excludes pending. |
| Same-direction no-op | `STATE_EVALUATED, STATE_NO_OP, BEARISH, ALREADY_BEARISH, null, ALREADY_IN_PE` | Private pre-check 193-205. Test zero routing. |

## 12. EXIT mapping

| Conclusion | Exact mapping | Evidence / transaction / risk / required test |
|---|---|---|
| Full achieved close | `ROUTING_COMPLETED, EXIT_ROUTED, FLAT, null, EXPLICIT_EXIT, EXIT_TRADED` | Router 2287-2323 clears JSON before result/job commits. Test TRADED and flat. |
| No position | `STATE_EVALUATED, STATE_NO_OP, FLAT, ALREADY_FLAT, EXPLICIT_EXIT, ALREADY_FLAT` | Private 196-203. This is an idempotent no-op, not broker success. Test no broker. |
| Stable partial exit | `ROUTING_COMPLETED, PARTIAL, prior direction, null, EXPLICIT_EXIT, PARTIAL_FILL_REMAINS_OPEN` | Router 2287-2302 writes reduced JSON. Test closed partial status and reduced quantity. |
| Closed broker rejection | `ROUTING_FAILED, FAILED, UNKNOWN, null, EXPLICIT_EXIT, EXIT_REJECTED` | Router 2325-2328. `UNKNOWN` avoids a later JSON read. Test original JSON remains. |
| Uncertain exit | Suppress | Router 1595-1598 and reconciliation 155-286. Test no immutable premature flat/failed outcome. |

## 13. Reversal semantics

Stable one-row reversal mappings are:

| Final committed conclusion | Exact mapping | Evidence / transaction / risk / required test |
|---|---|---|
| Reversed successfully | `ROUTING_COMPLETED / REVERSAL_ROUTED / target state / — / REVERSAL_EXIT / REVERSAL_COMPLETED` | Router 2029-2098. Both JSON transitions finish before job commit. Test one row. |
| Flattened; terminal entry failed | `ROUTING_FAILED / PARTIAL / FLAT / — / REVERSAL_EXIT / REVERSAL_ENTRY_FAILED` | Router 2029-2033 then 2100-2116. Test no target JSON. |
| Exit failed; original remains | `ROUTING_FAILED / FAILED / original state / — / REVERSAL_EXIT / REVERSAL_EXIT_FAILED` | Router 1979-1994. Test entry never called. |
| Exit or entry pending/unknown | Suppress | Router 1996-2027 and `_place_order` pending statuses. Test later reconciliation cannot invalidate a row. |

The core columns can describe the three stable final states. The existing
writer's historical multi-row reversal behavior is not valid for R1B-2C, and
the missing unique job constraint remains the schema blocker. Unsupported
blocked-stage detail must be null or a genuinely matching existing closed
code; `REVERSAL_*_FAILED` must not be used for a materially different
unresolved state.

## 14. Blocked/no-op semantics

| Runtime conclusion | Mapping | Evidence / transaction / risk / required test |
|---|---|---|
| Paused entry | `STATE_EVALUATED/STATE_NO_OP/UNKNOWN/INSTANCE_PAUSED/—/PAUSED_ENTRY_BLOCKED` | Private 76-77; no broker. Test no guessed current state. |
| Same side / already flat | Sections 10-12 | Private 193-205. Test no risk/broker. |
| Risk rejection | `STATE_EVALUATED/BLOCKED/UNKNOWN/—/optional RISK_EXIT/RISK_BLOCKED` | Fanout 810-833/router risk. Test closed reason source. |
| Contract unavailable | `ROUTING_FAILED/FAILED/UNKNOWN/—/—/CONTRACT_RESOLUTION_FAILED` | Private 156-161. Test no order. |
| Live execution disabled | `STATE_EVALUATED/BLOCKED/UNKNOWN/—/—/LIVE_EXECUTION_DISABLED` | Private 183-187. Test phase flag unchanged. |
| Entitlement blocked | `STATE_EVALUATED/BLOCKED/UNKNOWN/—/—/ENTITLEMENT_BLOCKED` | Fanout 785-808. Test no execution router. |
| Market closed/safety lock | `STATE_EVALUATED/BLOCKED/UNKNOWN/—/action exit reason/null` unless a reviewed closed code exactly applies | Router 1251-1319, 2141-2153. Never derive from message text. Test unsupported cause fails closed. |
| Signal-only/accepted without execution | `STATE_EVALUATED/NOT_EVALUATED/UNKNOWN/—/—/null`, only if explicitly authorized later | Fanout 782-783. It is not an achieved trading outcome and should initially be suppressed. Test scope exclusion. |

## 15. Paper/live semantics

Paper and live share the canonical semantic mapping. The referenced job
retains `execution_mode`; the outcome table has no provenance column.
R1B-2C does not need a duplicate `PAPER/LIVE` field to classify achieved
position state, so no provenance enum is invented.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `models.py`, `StrategyExecutionJob.execution_mode`, line 440; `PaperBroker`, lines 125-204; live router, 1264-1748 | Mode is committed on job; raw broker data differs. | Join through job for provenance; identical canonical fields for identical achieved state. | Job mode is read in evidence session. | Copying Dhan payload leaks broker material; relying on mutable engine state changes history. | Paper/live golden pair and taint test. |

## 16. Decision/outcome relationship

Answers:

1. A canonical decision FK is currently required.
2. Outcome has no direct `StrategySignal` FK.
3. It has a nullable `StrategyExecutionJob` FK.
4. It cannot be inserted through the writer when decision persistence was
   disabled and no decision exists.
5. It cannot be inserted when the decision write failed.
6. Linking later would require an update, which the evidence immutability hook
   forbids; no backfill/update is authorized.
7. Only `idempotency_key` is unique; neither job nor decision+job is unique.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `models.py`, lines 1396-1402; migration 0015 lines 185-211 | Decision non-null/CASCADE; job nullable/SET NULL. | Migration 0016: decision nullable with `SET NULL`; require a job for R1B-2C rows; unique non-null job identity. | Decision may be linked only when already present in the same evidence reload; absence is not failure. | Optional decision becomes mandatory execution evidence dependency. | Decision on/off/failed/absent matrix. |
| `canonical_signal_outcome_persistence.py`, signature 123-135 | Writer requires a decision object. | Later writer revision accepts optional decision plus required job for R1B-2C and validates signal consistency when decision exists. | Writer remains caller-transaction controlled. | Synthesizing/backfilling a decision would violate approved policy. | Missing decision inserts outcome; no decision writer call. |

Missing-decision behavior after migration: persist the outcome with
`canonical_decision_id=NULL`; never create, repair or later attach a decision.

## 17. Feature-flag design

Use only `R1B_CANONICAL_OUTCOME_PERSISTENCE`.

```text
Gate 1: helper checks `is True` before session creation.
Gate 2: writer independently checks `is True` before DB access.
```

The default and fail-safe parser are at `backend/app/config.py`, lines
124-174. Missing, empty and invalid values resolve false. No API, webhook,
Pine or frontend parameter may bypass either gate.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `config.py` 129-174; writer 137-138 | Outcome flag defaults false and writer fails closed. | Add helper gate; keep writer gate; leave both outcome and shared decision flags disabled. | False helper opens no session; helper true/writer false rolls back evidence only. | Coupling to decision flag recreates dependency. | False/missing/empty/invalid, mid-request true→false and false→true tests. |

## 18. Helper architecture

Future file:

```text
backend/app/services/canonical_execution_outcome_evidence.py

persist_execution_outcome_best_effort(
    *,
    strategy_execution_job_id: UUID,
    strategy_instance_id: UUID,
    owner_user_id: UUID,
) -> None
```

The helper accepts only committed identifiers. It must not accept an action,
classification, raw result, exception, success boolean, position, fingerprint,
decision fields or flag bypass.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `trading_canonical_decision_evidence.py`, helper pattern, lines 97-214 | Existing evidence helper reloads committed identifiers in a separate session and suppresses failures. | Reuse the isolation pattern, not its decision dependency or mutable status assumptions. Reload job/owner/instance/signal and optional decision; normalize stable result; call writer; commit evidence only. | One independent `session_scope`. | Passing transient router dict crosses authority boundary. | Exploding caller input objects impossible by signature; committed-row mutation tests. |

## 19. Owner/reference binding

Required SQL predicates:

```text
StrategyExecutionJob.id = job_id
AND StrategyExecutionJob.user_id = owner_user_id
AND StrategyExecutionJob.strategy_name = "instance:" + strategy_instance_id

StrategySignal.id = job.strategy_signal_id
AND StrategySignal.strategy_name = job.strategy_name
AND StrategySignal.signal_id = job.signal_id

StrategyInstance.id = strategy_instance_id
AND StrategyInstance.user_id = owner_user_id
```

If an optional decision exists, require its signal, instance and user to match.
If a WebhookEvent is consulted for defense in depth, require owner, provider,
event id and committed metadata instance/action to match. No foreign row should
be selected without owner predicates where owner columns exist.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `models.py`, job 426-450, decision 1316-1340; `trading_canonical_decision_evidence.py` 123-194 | Job has owner and accepted signal envelope; outcome has no owner column. | Owner-scoped joins reload all authority. Missing/foreign/mismatch suppresses evidence only. | Read validation and insert occur in evidence transaction. | Cross-tenant outcome association. | Foreign owner/instance/signal/job/event/decision, all mismatches, deleted refs and ambiguous retry fixtures. |

## 20. Separate-session and failure isolation

Helper algorithm:

1. Check helper flag.
2. Parse identifiers into UUIDs.
3. Open an independent evidence session.
4. Owner-scope reload committed job, signal and instance.
5. Verify private accepted action and terminal single-winner eligibility.
6. Optionally load a matching decision; absence is allowed after migration.
7. Normalize closed result or return ineligible.
8. Call reviewed/revised insert-only writer.
9. Let only the evidence session commit.
10. Catch all failures, roll back/close, emit only a closed diagnostic, return
    `None`.

Allowed log:

```text
event=r1b_execution_outcome_evidence_failed
writer_version=nova.canonical-outcome-writer.v2
outcome_class=<ENTRY|EXIT|REVERSAL|NO_OP|BLOCKED|FAILURE when safely known>
error_code=<closed code>
```

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `db/engine.py`, `session_scope`, 124-134; writer lines 185-212 | Scope commits/rolls back; writer uses SAVEPOINT and does not commit. | Helper owns only evidence transaction and returns nothing authoritative. | No shared session/entity. | Evidence outage could otherwise trigger worker retry after broker action. | DB outage, flush, commit, rollback and logger failures leave job/signal/JSON byte-identical. |

## 21. Retry and duplicate behavior

| Event | Designed behavior | Evidence / transaction / risk / required test |
|---|---|---|
| Crash before job commit | No outcome | Worker job is not terminal. Test helper no call. |
| Crash after job commit but before signal refresh/evidence | Execution remains valid; no automatic repair | Worker 104-120. Test process stop and missing optional outcome. |
| Retry-pending job | No outcome | Worker 69-72. |
| Money-mode recovered attempt | Retry suppressed; no final outcome because broker state is unknown | Worker 161-170, 196-199. Test no `RETRY_SUPPRESSED` row. |
| Helper called twice | Same immutable row | Future unique job constraint; PostgreSQL race. |
| Conflicting helper interpretation | Preserve original, closed conflict, no execution effect | Future writer compares winner fields. |
| Duplicate webhook | Existing response only; no outcome repair | `private_webhook_service.py` 436-443, 643-664. Test flag enabled after original completion still gives zero repair. |

## 22. Reconciliation and uncertain execution

Selected policy: **Option A — no outcome until resolved**, implemented as
suppression for R1B-2C because current reconciliation does not finalize a job
and backfill is forbidden. Option B is not used for “final achieved” evidence;
Option C mutation is forbidden.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `dhan_client.py`, timeout recovery 318-399 and polling 930-1016; router 1551-1715 | Unknown/pending accepted orders are possible; router may track a provisional position. | No outcome for unknown/pending/open-order states. | Job may commit before later broker truth. | Immutable false positive. | Timeout/poll exhaustion followed by both broker-open and broker-flat reconciliation. |
| `position_reconciler.py`, 155-286 | Reconciliation mutates JSON but not the originating job. | No reconciliation writer call, no mutation/backfill of outcome. | Separate later file operation. | A second “correction outcome” would violate one-final-row/no-backfill rules. | Static no-call and dynamic no-row tests. |

This gap is explicit: some valid executions will have no optional canonical
outcome until a future separately authorized job-linked reconciliation model
exists.

## 23. EOD/internal path scope

Initial scope is only private-webhook jobs whose committed accepted action is
BUY_CE, BUY_PE or EXIT.

| Path | Scope decision | Evidence / transaction / risk / required test |
|---|---|---|
| EOD square-off | Excluded | `workers/eod_squareoff.py` 129-207 has no job. Static forbidden caller test. |
| Manual entry/exit/reverse | Excluded | `routers/orders.py` 109-224 routes directly. Static forbidden caller test. |
| Panic/control exit | Excluded | `routers/control.py` around 125-133 routes directly. Static test. |
| Option monitor exits | Excluded | `option_position_monitor.py` around 551-586 routes directly. Static test. |
| Broker reconciliation/ghost watcher | Excluded | `position_reconciler.py` and `workers/ghost_position_watcher.py`; no reviewed job contract. Static test. |

## 24. No-backfill policy

```text
flag false at terminal completion → no outcome
duplicate webhook later → no repair
restart → no scan
worker/reconciliation inspection → no repair
decision created later → no outcome repair or link update
```

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `private_webhook_service.py`, duplicate path 436-443 and existing response 643-664 | Duplicate does not rerun worker. | Outcome helper has no ingress/duplicate/startup/scheduler caller. | Only live terminal callback can attempt evidence. | Repair can accidentally rerun execution or create time-dependent evidence. | Static graph and enable-after-completion duplicate test. |

## 25. PostgreSQL concurrency design

Use PostgreSQL 18, migrations through the future 0016 head, distinct sessions
and threads/processes with barriers.

| Scenario | Required assertion | Evidence / transaction / risk |
|---|---|---|
| Identical helper race | One row; both calls resolve to same row/semantic winner; job unchanged | Unique non-null job index arbitrates. |
| Conflicting interpretation race | One row; loser gets closed conflict; original immutable; no reroute | Current full-observation key fails this and is the primary schema blocker. |
| Worker completion race | One conditional terminal winner; only winner invokes helper; one outcome | Current `_complete_job` 104-120 lacks winner predicate. |
| Evidence commit failure | Job, signal and JSON remain durable; outcome absent; worker success unchanged | Independent session rollback. |
| Retry transition | queued/running: zero; later eligible terminal: one | Helper reloads current committed terminal state. |
| Process stops post-authority/pre-evidence | Execution valid, zero outcome, no repair | Optional evidence contract. |
| Decision absent race | Outcome succeeds with null decision after migration | Proves independence. |

## 26. Static call-site constraints

Future allowed graph:

```text
one terminal-worker integration path
→ canonical_execution_outcome_evidence.persist_execution_outcome_best_effort
→ canonical_signal_outcome_persistence.persist_canonical_signal_outcome
```

Forbidden imports/calls include ingress, duplicate handler, HOLD, parser,
replay store, router, risk manager, PaperBroker, Dhan client, state store,
position services, reconciliation, EOD, manual routes, scheduler, startup and
frontend.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `canonical_signal_outcome_persistence.py`, module lines 1-12 | Zero production callers at this SHA. | AST allowlist: writer caller count 1 helper; helper worker caller count 1; rejection writer 0; decision call graph unchanged. | Graph enforces post-commit architecture statically. | Hidden direct caller can run before authority. | AST import/call ancestry and forbidden-module scan. |

## 27. Test architecture

Future focused files:

```text
test_r1b2c_outcome_integration.py
test_r1b2c_outcome_mapping.py
test_r1b2c_outcome_failure_isolation.py
test_r1b2c_outcome_concurrency.py
test_r1b2c_runtime_call_sites.py
```

The suites must cover all 43 prompt scenarios. In particular:

| Test group | Required proof | Evidence / transaction / risk |
|---|---|---|
| Flags and graph | False opens no session; dual gate; one helper/writer path; no decision/rejection changes | Config 124-174 and AST. |
| Mappings | Exact BUY_CE, BUY_PE, EXIT, no-op, paused, risk, contract, paper/live, stable partial and reversal matrices | Adapter golden corpus; no message parsing. |
| Eligibility | Retry, unknown, pending, position-write split and stale jobs write zero | Committed-job fixtures. |
| References | Owner-scoped full chain and optional decision | Real DB rows and foreign fixtures. |
| Isolation | Evidence outage/commit/log failure changes no authoritative byte/row | Separate sessions and forced failures. |
| Concurrency | Identical/conflicting job races and terminal winner | PostgreSQL 18 barriers. |
| Backfill | Duplicate/restart/reconciliation create no row | Runtime call graph and integration tests. |
| Secrets | No raw broker/request/exception/credential in row or logs | Taint corpus. |

## 28. Migration decision

Migration 0016 is required; none is created in this design phase.

Minimum schema correction:

1. Make `canonical_decision_id` nullable and change delete behavior to
   `SET NULL`, preserving an independent outcome.
2. Enforce at most one outcome for every non-null `execution_job_id` with a
   unique partial index/constraint.
3. Retain nullable job only for the pre-existing connectivity/no-job schema
   capability; R1B-2C helper requires a job.
4. Revise the writer to reselect by job after a unique race, return only an
   identical winner, and raise a closed conflict for different fields.
5. Add reviewed cross-field validation (database CHECKs where practical,
   writer enforcement in all cases).

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| Migration 0015 lines 185-257; model 1348-1416 | Cannot write without decision; permits multiple rows/job. | 0016 above, followed by independent migration/schema review before runtime implementation. | Migration is separate from later feature-disabled runtime code. | Implementing first would create evidence gaps and ambiguity. | Upgrade/downgrade, empty/non-empty table, FK delete, unique race and Alembic single-head tests. |

## 29. Baseline failure treatment

At starting SHA `5a31d5da829600c450060aecaf93fb5ab6e41851`,
`test_dhan_payload_validation_passes_after_security_id_resolution` still fails
exactly at `assert validation["ok"] is True` with actual `False`, after
resolution succeeds and `securityId=="999999"`. No Dhan code or test was
changed.

A separate existing position-operation test was observed to be order-flaky:
`test_same_entry_order_with_conflicting_payload_is_visible` passed, failed,
then passed in three isolated runs because the returned event order alternated.
This is not introduced by R1B-2C (the repository had no changes during the
runs). It reinforces the decision not to use unordered position-event reads as
outcome authority.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `app/tests/test_security_id_resolver.py`, lines 87-97 | Deterministic reviewed baseline failure. | Record only; do not fix or contaminate R1B-2C. | No outcome transaction involved. | Scope creep into Dhan validation. | Exact single-test comparison at starting SHA. |
| `app/tests/test_position_operations.py`, failing assertion around line 125 | Event query order is nondeterministic in the local SQLite suite. | Exclude event ordering from adapter authority; defer test hardening. | Diagnostic DB reads only. | “First/latest event” inference is unstable. | Future test must use explicit deterministic ordering if this subsystem is used. |

Any failure introduced after the sole design document change would be a phase
regression; none was introduced.

## 30. Rollback

Future rollback:

1. Set `R1B_CANONICAL_OUTCOME_PERSISTENCE=false`.
2. Confirm helper returns before database access.
3. Revert the future runtime integration commit.
4. Leave existing evidence immutable.
5. Do not alter decisions, signals, jobs or JSON positions.
6. Do not retry broker actions.
7. Downgrade 0016 only under a separately reviewed data-safe migration plan;
   ordinary runtime rollback does not downgrade the database.

| Evidence | Observed current behavior | Proposed outcome behavior | Transaction relationship | Safety risk | Required test |
|---|---|---|---|---|---|
| `config.py` 129-174; writer 137-138 | Flag-off is inert. | Flag first, code revert second; no data repair. | Authority remains live while optional evidence is disabled. | Rollback-triggered broker retry or evidence mutation. | Flag-off zero-session and retained-row tests. |

## 31. Blocking risks

| Blocking finding | Evidence | Required correction and test |
|---|---|---|
| Required decision dependency | `models.py` 1396-1399; writer 123-142 | Migration 0016 nullable FK + optional writer; decision-disabled/failed PostgreSQL tests. |
| No one-row-per-job natural key | `models.py` 1355-1394; writer 104-120; outcome tests 98-137 | Unique non-null job constraint; identical/conflicting races. |
| Mutable terminal winner | `strategy_job_worker.py` 104-120 | Conditional running+attempt winner before helper; two-completion barrier. |
| Uncertain completed jobs | Router 1551-1715; fanout 879-881; reconciler 155-286 | Closed eligibility adapter suppresses uncertainty; position-write split tests. |
| No job-linked reconciliation finalization | Reconciler 155-286 | Explicit evidence gap; no repair in R1B-2C. Any future solution requires a separate phase. |
| Cross-field schema ambiguity | Model 1357-1391 | Reviewed CHECK/writer matrix; direct SQL invalid-combination tests. |

These are genuine implementation blockers, not documentation findings.

## 32. Implementation authorization recommendation

`BLOCKED_SCHEMA`

Do not implement or connect R1B-2C outcomes on the current schema. Authorize a
separate migration/schema-design correction for 0016, followed by independent
review. Only after that review should a new R1B-2C implementation prompt
authorize:

1. a single-winner terminal worker transition;
2. the closed committed-result adapter;
3. the independent best-effort helper;
4. the revised one-row-per-job writer;
5. focused SQLite and genuine PostgreSQL 18 tests.

No runtime code changed. No test logic changed. No migration was created.
Decision integration was not modified. The shared decision flag remains
disabled. Outcome persistence remains disconnected during design. Rejection
persistence remains disconnected. No worker, router, broker or position
authority changed. No Dhan validation code changed. No prompt or transport
changed. No AI conversion was enabled. No live order occurred. No merge or
deployment occurred.
