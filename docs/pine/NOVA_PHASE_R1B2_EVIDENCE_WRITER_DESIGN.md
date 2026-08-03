# NOVA Phase R1B-2 — Canonical Evidence Writer Design

## 1. Executive recommendation

**Verdict: APPROVED_TO_IMPLEMENT_R1B2A_WRITERS_ONLY**

- Failure policy: **BEST_EFFORT_SYNCHRONOUS, post-commit** — every evidence
  write runs after the authoritative transaction has committed, in its own
  short session, and swallows its own failures into a sanitized metric/audit
  event. No outbox, no background worker, no async retry in R1B-2.
- Decision writer: option B (separate best-effort transaction immediately
  after the StrategySignal commit). One decision per signal via
  `uq_canonical_decision_signal`; conflicts recorded as audit events, never
  as updates.
- Outcome writer: called only from the worker after `_complete_job`, i.e.
  after the authoritative job result and broker side effects are already
  durable. Deterministic hash idempotency key; retries write distinguishable
  rows and never re-run execution.
- Rejection writer: post-authentication only, narrow allowlist, best-effort,
  after the rejection decision is final; response codes never change.
- Migration 0016: **not required**. The 0015 closed vocabulary is sufficient;
  finer-grained detail rides in `safe_detail_code` under a writer-enforced
  closed vocabulary. PostgreSQL hex-regex hardening (review finding 3) stays
  deferred to a separate hardening migration.
- First implementation slice: **R1B-2A writer libraries with zero production
  call sites**, including the finding-1/finding-2 error-envelope fixes in the
  semantic writer. Runtime integration (R1B-2B/C/D) stays separately
  reviewed.

Evidence observes the pipeline; it never controls it. Every writer's contract
is: *any* failure inside the writer leaves webhook acceptance, replay claims,
signal rows, job rows, routing, positions, broker calls and HTTP responses
byte-identical.

## 2. Current authoritative flow

Code-grounded pipeline (all paths verified at `731177a`):

```
POST /api/webhooks/private
  routers/private_webhook.py
  → private_webhook_service.parse_request            (752-line service)
  → authenticate_credential            svc:162-211   [txn A: credential lookup + last_used_at]
  → validate_instance_lifecycle        svc:230-252   (no DB)
  → ingest                             svc:388-450
      normalize_action                 svc:87-89     → 422 INVALID_ACTION
      validate_signal_time             svc:121-136   → 422 MISSING_TIMEZONE / STALE_SIGNAL
      payload_fingerprint              svc:139-155
      webhook_replay_store.claim_webhook_event       [txn B: WebhookEvent claim]
          "tampered"  → 409 CONFLICTING_DUPLICATE
          "duplicate" → existing-status response (no new rows)
      _persist_hold                    svc:505-542   [txn C: StrategySignal only, HOLD]
      _persist_execution_job           svc:545-599   [txn C: StrategySignal + StrategyExecutionJob, one commit]
      wake_strategy_job_worker
  → 200 NO_OP (HOLD via test endpoint 202) / 202 QUEUED / 200 duplicate

worker (workers/strategy_job_worker.py)
  _claim_jobs                          wrk:78-101    [txn D: status=running, attempts+=1, FOR UPDATE SKIP LOCKED on PG]
  _execute_job                         wrk:161-193
      _retry_would_risk_duplicate_execution wrk:196-199 (money-mode attempt>1 → fail, no re-run)
      private_webhook_execution.execute_private_job  exe:167-214
          _revalidate_instance         exe:58-94     [txn E: instance FOR UPDATE — status/lots/mode recheck]
          JSON position pre-check      exe:196-205   (ALREADY_FLAT / ALREADY_IN_CE / ALREADY_IN_PE)
          strategy_fanout.dispatch_signal_job        fan:752+ (entitlements, risk, run row)
          → execution_router.route_signal            (orders, reversal sequencing, position JSON writes)
  _complete_job                        wrk:104-120   [txn F: job result_summary + status]
  _refresh_signal_summary              wrk:123-158   [txn G: StrategySignal roll-up]
```

Authority statement: the four evidence tables have no reader or writer on any
of these paths today (verified by the R1B-1 review, §7). R1B-2 adds only
observers positioned strictly *after* the authoritative commits above.

## 3. Decision writer design

File: `backend/app/services/canonical_signal_decision_persistence.py`
(insert-only; mirrors the structure and error envelope of
`pine_semantic_analysis_persistence.py`, with findings 1–2 corrected: **every**
query, including any owner/consistency SELECT, lives inside one closed
`try/except SQLAlchemyError` envelope, and any unexpected exception is
wrapped to a closed code).

```
persist_canonical_decision(
    session,                      # dedicated evidence session (see §7)
    *,
    strategy_signal_id,           # from the committed StrategySignal row
    webhook_event_id,             # from the replay claim, when available
    user_id, strategy_instance_id,# from authenticate_credential (trusted)
    wire_action,                  # normalized action, post normalize_action()
    canonical_event,              # adapt_legacy_action() result (R1A adapter)
    adapter_version, contract_version,
    payload_fingerprint,          # server-computed sha256 hexdigest
    signal_time, received_at,
    safe_metadata,                # build_safe_metadata() output ONLY (R1B-0)
    provenance_kind="LIVE",
) -> CanonicalSignalDecision
```

Inputs it must never accept (enforced by signature — no kwargs dict — plus a
taint assertion on `safe_metadata` keys): credential plaintext, raw webhook
body, user-supplied desired state or reason, quantity/lots, metadata option
side, strike, expiry, security ID, execution mode, SL/TP, broker credentials,
Pine source. The action→(event_type, desired_state, intent_reason,
compatibility_*) mapping is computed inside the writer from `wire_action`
via the R1A adapter's closed tables (`legacy_signal_adapter`), then
double-checked against `canonical_event` — a mismatch refuses the row
(closed error `DECISION_MAPPING_CONFLICT`) and emits a sanitized audit
event. The DB cross-field CHECKs (`ck_canonical_decision_*_consistency`)
are the final guard.

Idempotency: pre-select on `strategy_signal_id`; if a row exists and its
canonical fields equal the new interpretation → return it; if it exists and
*differs* → return the existing row unchanged, emit
`CANONICAL_DECISION_CONFLICT` audit metric (no update, no execution effect).
Insert path uses SAVEPOINT + unique-violation fallback exactly like the
semantic writer (proven concurrency-safe in the R1B-1 review §10). No insert
can precede the StrategySignal because `strategy_signal_id` is a CASCADE FK
and the call site runs only after that row's commit.

Transaction options compared:

| Option | Signal-loss risk | False-reject risk | Crash window | Complexity | Verdict |
| --- | --- | --- | --- | --- | --- |
| A. same txn as StrategySignal | none | **evidence failure can abort/poison the authoritative flush** (needs SAVEPOINT and perfect session hygiene inside `_persist_execution_job`) | none | medium | rejected — couples evidence to acceptance |
| B. post-commit, separate best-effort txn (same request) | none | none (failures swallowed) | tiny gap: signal committed, decision missing; reconcilable because decisions are derivable from signal+job rows | low | **recommended** |
| C. asynchronous task | none | none | larger gap; new thread lifecycle | medium-high | rejected — unjustified |
| D. outbox | none | none | none | high (new table → migration 0016, worker, retention) | rejected for R1B-2 |

Safety property satisfied: with B, `persist_canonical_decision` runs after
`_persist_execution_job`/`_persist_hold` return, wrapped in
`try/except Exception: emit metric`. A decision failure cannot reject or
alter an accepted signal, and an evidence gap is observable
(`decision_missing` metric) and later repairable idempotently by calling the
writer again with the same trusted inputs.

## 4. Outcome writer design

File: `backend/app/services/canonical_signal_outcome_persistence.py`.

Phases: keep exactly the 0015 CHECK set — `STATE_EVALUATED`,
`ROUTING_COMPLETED`, `ROUTING_FAILED`. The Part-3 candidate phases
(INGRESS/JOB_CREATED/WORKER_CLAIMED/...) are not adopted: they would require
migration 0016 and add rows with no decision value; attempt number +
`safe_detail_code` already distinguish worker lifecycle points.

Placement of outcomes:

- Webhook ingestion writes at most one outcome: HOLD →
  `phase=STATE_EVALUATED, routing_result=CONNECTIVITY_NO_JOB,
  no_op_reason=CONNECTIVITY_TEST` (with the decision, post-commit).
- The execution worker writes all other outcomes from
  `_execute_job`/`_complete_job` context **after** the authoritative
  `result_summary` commit (txn F) — one call site, reading only the committed
  job snapshot + result dict.
- Router-internal steps never write evidence; the worker translates the final
  result (§8). One job attempt normally yields exactly one outcome row; an
  exit-first reversal yields up to two ordered rows (exit observation, then
  final result) distinguished by `safe_detail_code` and the idempotency key.

Retry model: worker attempts are already bounded (`max_attempts=2`,
`wrk:196-199` blocks money-mode re-runs). Each attempt's outcome carries
`attempt` (from the job snapshot) so retries are distinguishable; the
idempotency key (§8) includes the attempt, so a crash between txn F and the
evidence write simply loses one evidence row (observable gap, repairable from
`result_summary`) — it never re-runs execution, because the evidence writer
takes results as data and has no reference to the router, brokers or
position store (statically testable import rule).

Safety requirements satisfied by construction: the writer runs after broker
side effects are durable, in a separate session (no rollback can reach them);
evidence retry re-invokes only the writer with the same inputs (idempotent
unique key → same row); the writer imports neither `state_store` nor any
broker/router module, so it cannot mutate JSON position state.

## 5. Rejection writer design

File: `backend/app/services/strategy_signal_rejection_persistence.py`.

Persistable only after `authenticate_credential` succeeded (trusted
`user_id` + `strategy_instance_id` in hand). Allowlist mapped to the 0015
closed codes:

| Ingress failure (source) | Stage | Code | signal_id storage |
| --- | --- | --- | --- |
| unsupported action (`ingest` svc:392-396) | CANONICAL_NORMALIZATION | INVALID_ACTION | safe-projected + hash |
| missing tz (`validate_signal_time` svc:121-127) | SIGNAL_TIME | MISSING_TIMEZONE | safe-projected + hash |
| stale / future skew (svc:128-136) | SIGNAL_TIME | STALE_SIGNAL | safe-projected + hash |
| lifecycle rejection (`validate_instance_lifecycle` svc:230-245) | LIFECYCLE | INACTIVE_INSTANCE | hash only (pre-normalization) |
| verification live attempt (svc:246-252) | LIFECYCLE | LIVE_EXECUTION_SAFETY_BLOCK | hash only |
| conflicting duplicate (`ingest` svc:424-431) | REPLAY_CONFLICT | CONFLICTING_DUPLICATE | safe-projected + hash + fingerprint |
| replay/store failure (svc:419-422, 439-442) | REPLAY_CONFLICT | STORE_UNAVAILABLE | hash only — best-effort likely also failing; acceptable |
| signal/job insert IntegrityError beyond duplicate handling | JOB_CREATION | JOB_PERSISTENCE_FAILED | safe-projected + hash |
| worker paused-entry no-op | — not a rejection: it is an outcome (`STATE_NO_OP`/`INSTANCE_PAUSED`) | — | — |
| semantic adapter failure (shadow path svc:374-381) | SEMANTIC_POLICY | (deferred — shadow is diagnostic-only in R1A; no rejection row in R1B-2) | — |

Never persisted / never owner-attributed: unknown or revoked credential
(uniform 401 anti-enumeration, svc:214-218 — writing a row would require
attributing a hostile probe to a tenant), malformed body before credential
extraction (`parse_request` 422), oversized body / wrong content type
(router-level), any unauthenticated payload, and never the full request body.
`signal_id_safe` uses `project_safe_signal_id` (R1B-0); `signal_id_sha256`
and `payload_fingerprint` are server-computed hexdigests; `safe_detail` uses
the closed `validate_safe_detail` vocabulary (extended with the rejection
codes themselves). Dedupe key: §8. Retention class: short (§12). API
response impact: none — the writer is called after the `PrivateWebhookError`
is constructed and before it is raised, inside `try/except Exception`; the
same error object is raised regardless of the evidence result, so a rejection
can never become an acceptance and vice versa.

## 6. Failure-isolation policy

Comparison (full matrix in the terms of the prompt):

| Criterion | Strict txn | **Best-effort sync (chosen)** | Outbox | Post-commit async |
| --- | --- | --- | --- | --- |
| Signal loss | possible (evidence failure aborts signal) | none | none | none |
| False webhook rejection | **yes — disqualifying** | none | none | none |
| Replay safety | unchanged | unchanged | unchanged | unchanged |
| Duplicate broker risk | none | none | none if worker idempotent | none if scheduler idempotent |
| Crash window | none | small, observable, repairable | none | medium |
| Observability | error = outage | metric per suppressed row | queue depth | task backlog |
| Complexity / local dev / PG needs | low / trivial / none | **low / trivial / none** | high / heavier / new table | medium / thread lifecycle |
| Rollback | couples evidence to authority | trivial (flags off) | drop worker + table | stop scheduler |
| Testing | poisons existing suites | straightforward failure injection | queue simulation | timing-sensitive |

Decision: **BEST_EFFORT_SYNCHRONOUS** for all three writers. Evidence is
explicitly non-authoritative; a lost evidence row is a metric, not an
incident. No background worker.

## 7. Transaction boundaries

Existing behavior (documented from code): txns A–G in §2 are each one
`session_scope()` (commit on exit, rollback on exception,
`expire_on_commit=False`). The replay claim (txn B) commits before signal
persistence (txn C) — the documented crash-recovery path at svc:432-438
already tolerates the gap.

Evidence writer boundaries:

- Each writer call opens its **own** `session_scope()` (never the caller's
  session), strictly after the authoritative transaction of its flow has
  committed. This structurally prevents: unexpected outer commits, rollback
  of authoritative rows, rollback after broker effects, outcomes referencing
  uncommitted jobs (job committed in txn C/F first), and lock interaction
  with authoritative tables (evidence sessions only ever lock evidence rows;
  FK parent reads are plain SELECTs).
- Inside a writer, the insert uses SAVEPOINT + unique-conflict fallback (the
  R1B-1-proven pattern) so duplicate evidence retries return the winner.
- Orphan analysis: decisions require the committed signal (FK); outcomes
  require the committed decision — the outcome writer looks the decision up
  by `strategy_signal_id` and, when the decision write itself was lost,
  first repairs it idempotently (decision inputs are all present in the job
  snapshot) or skips with a metric. No deadlock surface: evidence writers
  acquire locks in one fixed order (decision → outcome), never hold them
  across broker or JSON I/O, and never touch `strategy_execution_jobs` rows
  with `FOR UPDATE`.

## 8. Idempotency keys

- **Decision**: natural key = `strategy_signal_id`
  (`uq_canonical_decision_signal`). No composed key needed.
- **Outcome** (`idempotency_key CHAR(64)`, hash-shaped CHECK):
  `sha256("nova.r1b2.outcome.v1|" + decision_id + "|" + phase + "|" +
  (execution_job_id or "-") + "|" + str(attempt or 0) + "|" +
  routing_result + "|" + safe_detail_code).hexdigest()`.
  Stable across retries of the same observation; distinct per phase, per
  worker attempt, per ordered reversal step (safe_detail differs); contains
  no credential or source material (all inputs are server enums/UUIDs);
  always exactly 64 lowercase hex; collision risk is SHA-256-negligible.
- **Rejection** (`dedupe_key CHAR(64)`):
  `sha256("nova.r1b2.rejection.v1|" + strategy_instance_id + "|" + stage +
  "|" + rejection_code + "|" + (signal_id_sha256 or payload_fingerprint or
  webhook_event_id or received_at.date().isoformat())).hexdigest()`.
  Behavior: identical invalid retries dedupe to one row; the same signal_id
  with a different invalid payload dedupes per (code, signal-hash) — the
  fingerprint variant is captured only for CONFLICTING_DUPLICATE where the
  fingerprint is part of the key; no-signal-id failures fall back to the
  event id or day bucket (bounded rows per instance/day); stale retries
  dedupe; conflicting duplicates get one row per conflicting fingerprint.

## 9. Reason/result translation matrix

Closed, writer-owned translation from the actual server vocabulary (sources:
`private_webhook_execution._no_op/_result/_failed` exe:34-56,
`dispatch_signal_job` fan:770-831, router statuses rtr:1585-1605, 1972-2108,
2237-2328). Pine metadata is never a reason source. Intent reason lives on
the decision (from the R1A adapter); the rest live on outcomes:

| Server result (actual string) | routing_result | no_op_reason | exit_reason | safe_detail_code |
| --- | --- | --- | --- | --- |
| HOLD (ingress, no job) | CONNECTIVITY_NO_JOB | CONNECTIVITY_TEST | — | HOLD |
| NO_OP ALREADY_FLAT | STATE_NO_OP | ALREADY_FLAT | — | ALREADY_FLAT |
| NO_OP ALREADY_IN_CE | STATE_NO_OP | ALREADY_BULLISH | — | ALREADY_IN_CE |
| NO_OP ALREADY_IN_PE | STATE_NO_OP | ALREADY_BEARISH | — | ALREADY_IN_PE |
| NO_OP INSTANCE_PAUSED_ENTRIES_BLOCKED | STATE_NO_OP | INSTANCE_PAUSED | — | PAUSED_ENTRY_BLOCKED |
| blocked: PRIVATE_WEBHOOK_LIVE_EXECUTION_DISABLED / live_entitlement_required / strategy_entitlement_required / live_orders_not_armed / egress_* / no_credentials / risk reasons | BLOCKED | — | — | closed per-reason code (uppercased, bounded 60) |
| rejected: INACTIVE_INSTANCE / TENANT_MISMATCH / INVALID_LOTS / LIVE_EXECUTION_SAFETY_BLOCK | FAILED | — | — | per-reason code (VERIFICATION_PAPER_ONLY maps from LIVE_EXECUTION_SAFETY_BLOCK) |
| FAILED CONTRACT_RESOLUTION_FAILED | FAILED | — | — | CONTRACT_RESOLUTION_FAILED |
| skipped user_not_found / worker exception / stale-recovery failure | FAILED | — | — | WORKER_FAILED / RETRY_SUPPRESSED |
| entry TRADED / ORDER_PLACED | ENTRY_ROUTED | — | — | ENTRY_TRADED / ENTRY_SUBMITTED |
| entry PENDING_CONFIRMATION / PARTIAL_FILL_PENDING | PENDING | — | — | ENTRY_PENDING |
| entry PARTIAL_FILL / PARTIAL_ENTRY_FILLED | PARTIAL | — | — | PARTIAL_FILL_REMAINS_OPEN |
| entry rejected (`_failed_order_status`) | FAILED | — | — | ENTRY_REJECTED |
| exit TRADED / ORDER_PLACED | EXIT_ROUTED | — | EXPLICIT_EXIT | EXIT_TRADED / EXIT_SUBMITTED |
| exit PENDING / PARTIAL_EXIT_FILLED | PENDING / PARTIAL | — | EXPLICIT_EXIT | EXIT_PENDING / PARTIAL_FILL_REMAINS_OPEN |
| exit rejected | FAILED | — | EXPLICIT_EXIT | EXIT_REJECTED |
| REVERSAL_EXIT_PENDING/BLOCKED/FAILED | FAILED (terminal) or PENDING | — | REVERSAL_EXIT | REVERSAL_EXIT_STARTED / REVERSAL_EXIT_FAILED |
| REVERSAL_ORDER_PLACED / completed | REVERSAL_ROUTED | — | REVERSAL_EXIT | REVERSAL_COMPLETED |
| REVERSAL_ENTRY_BLOCKED/FAILED (exit done, entry failed) | PARTIAL | — | REVERSAL_EXIT | REVERSAL_ENTRY_FAILED |
| broker state unknown / PENDING_CONFIRMATION timeout | PENDING | — | — | BROKER_STATE_UNKNOWN |

Gap analysis against 0015 CHECKs: every required Part-3 concept maps into
the existing `routing_result`/`no_op_reason`/`exit_reason` sets plus a
writer-enforced closed `safe_detail_code` vocabulary (~25 values, all ≤60
chars, `safe_detail`-pattern-compatible). `current_state` maps from the JSON
position pre-check (FLAT/BULLISH/BEARISH; UNKNOWN when the pre-check was
skipped). **No CHECK value is missing → no migration 0016.** STOP_LOSS /
TAKE_PROFIT / TRAILING_STOP / SESSION_EXIT / MANUAL_EXIT / RISK_EXIT exit
reasons are reserved for monitor/EOD/manual flows, which are *out of scope*
for R1B-2 evidence (they do not pass through the private-webhook job path);
wiring them is future R1C work.

## 10. Secret-taint policy

Reuse the R1B-0 helpers as the writer boundary: every string field passes
`is_credential_shaped` (with request-local known secrets when available),
`safe_metadata` only ever comes from `build_safe_metadata`, signal IDs from
`project_safe_signal_id`, details from an extended closed
`validate_safe_detail` vocabulary. Writers reject externally supplied hash
values: `payload_fingerprint`, `signal_id_sha256`, idempotency/dedupe keys
are always recomputed internally with `hashlib.sha256(...).hexdigest()`
(finding 3 discipline); a caller-supplied hash parameter does not exist in
any writer signature. Forbidden everywhere (enforced by closed signatures +
taint scan + static tests): credential, credential hash, access token, TOTP,
webhook URL, raw body, Pine source, authorization headers, broker account
details, user email, arbitrary comments, exception text, file paths. Maximum
lengths follow the column widths; normalization is upper-snake for codes.
A taint finding suppresses the evidence row, emits one sanitized metric
(`evidence_taint_suppressed` + stage), and never alters authoritative
behavior. Structured logging allowlist: correlation_id, instance_id,
stage/code enums, counts — nothing else.

## 11. Feature-flag rollout

Existing three flags stay the per-writer gates; **no new flags**. The
prompt's staged activation maps onto them:

| Stage | Configuration |
| --- | --- |
| 0 | all false (default; R1B-2A ships here) |
| 1 | `R1B_CANONICAL_DECISION_PERSISTENCE=true`, writer call site limited to HOLD (code-staged in R1B-2B, not a new flag) |
| 2 | same flag; call site extended to all accepted actions |
| 3 | `+R1B_CANONICAL_OUTCOME_PERSISTENCE=true` for no-op/HOLD outcomes |
| 4 | same flag; worker paper-execution results |
| 5 | `+R1B_SIGNAL_REJECTION_PERSISTENCE=true`, allowlisted codes |
| 6 | live-mode evidence — separate review, no new flag needed (live evidence uses the same writers; the gate is the review, not configuration) |

Paper-only outcome persistence, the rejection allowlist and stage limits are
code constants per slice, not flags — avoiding flag explosion while keeping
rollback trivial (flip the writer flag false). Async-retry and
shadow-mismatch evidence are excluded from R1B-2 entirely. All flags default
false with the existing fail-safe validator.

## 12. Retention and privacy

- `canonical_signal_decisions` / `canonical_signal_outcomes`: long-lived
  audit evidence — retain ≥ 400 days (align with trading-audit norms),
  archive by yearly partition export before deletion; user deletion cascades
  via `users → strategy_instances → decisions` (already structural).
- `strategy_signal_rejections`: operational/security evidence — retain 30–90
  days; no retention job in R1B-2 (defer with the existing pattern of
  `prune_webhook_replay_records`).
- `pine_semantic_analyses`: immutable qualification provenance — retain for
  the life of the strategy version (cascade on artifact deletion already
  reviewed).
- Payload minimization is structural (closed columns, no raw bodies);
  metrics aggregate by enum only; no secret is stored, so none is
  recoverable. Legal/audit review before any deletion-policy automation.

## 13. Backfill recommendation

**Exclude backfill from R1B-2 entirely** (preferred answer adopted).
Technically, decisions for historical private-webhook signals are largely
reconstructible from `strategy_signals` + `strategy_execution_jobs.signal_payload`
(`raw_payload.normalized_action`, timestamps) with
`provenance_kind='BACKFILL'` + `backfill_version`; outcomes are only
partially reconstructible from `result_summary` (attempt granularity and
current_state are lossy); rejections are not reconstructible (never
persisted). Any future backfill must mark every row BACKFILL, never claim
real-time capture, exclude rows with missing/ambiguous payloads, and be
rehearsed with dry-run counts. Not designed further here.

## 14. API and UI boundary

No signal-ledger API or UI in R1B-2. Future R1C read model (owner-scoped):
event type, wire action, desired state, current state, routing result,
no-op reason, exit reason, decision/outcome timestamps, adapter version and
an evidence-completeness marker (decision-present/outcome-present booleans).
Confirmed exclusions for any normal-user API: raw rejection details,
internal exception text, credential-related fields, hostile metadata, Pine
source, and internal database IDs beyond the opaque evidence-row ID.

## 15. Test architecture

Per writer (all R1B-2A, PostgreSQL + SQLite):

- **Decision**: exact four-action mapping vs the DB cross-field CHECKs;
  owner/instance composite-FK enforcement; one-per-signal; identical call →
  same row; conflicting interpretation → existing row + conflict metric, no
  update (immutability guard already blocks it); no credential/source field
  reachable; flag off → zero query/zero write; injected writer failure →
  ingress response unchanged (byte-diff assertion).
- **Outcome**: every matrix row in §9 (parametrized); no-op paths; ordered
  reversal pair; retry attempts distinguishable; duplicate calls idempotent;
  genuine PostgreSQL two-session concurrency (reuse the R1B-1 review
  harness); "broker already completed" simulation (writer failure after
  result commit leaves job/result rows untouched); evidence DB unavailable →
  metric only; static import test proving the writer cannot reach router/
  broker/state_store; partial-fill recording; JSON position files
  byte-identical before/after writer calls.
- **Rejection**: allowlist accepted, forbidden unauthenticated cases
  statically unreachable (no call site pre-auth) and dynamically absent;
  dedupe matrix of §8; taint suppression; no raw payload column populated;
  API response equality with writer failing and succeeding.
- **Integration (R1B-2B+)**: flags off → byte-identical behavior (reuse the
  zero-write suite); flags on → identical responses/replay/signal/job rows
  with evidence rows added; HOLD → decision + connectivity outcome, no job;
  duplicates/conflicts/paused/verification/owner-isolation unchanged.
- **Failure injection**: decision/outcome/rejection insert failures at each
  timing point, taint findings, DB timeout, deadlock (PG), unique conflict,
  session rollback, and process-crash simulation between authoritative
  commit and evidence write (assert gap-metric + idempotent repair).

## 16. Implementation slices

- **R1B-2A — writer libraries only** (the only slice this design authorizes
  for implementation): three insert-only services + closed translation and
  taint modules; the finding-1/finding-2 envelope fixes in
  `pine_semantic_analysis_persistence.py`; exhaustive unit + PostgreSQL
  tests; **zero production call sites**; no migration.
- **R1B-2B — decision writer integration**: post-commit call sites in
  `_persist_hold`/`_persist_execution_job` (HOLD first, then trading
  actions); flag stays default-false.
- **R1B-2C — outcome writer integration**: worker `_complete_job` call site;
  no-op/paper first; live evidence only after separate review.
- **R1B-2D — rejection integration**: allowlisted post-auth call sites in
  `ingest`/`validate_*`; retention policy decision accompanies it.

Separate commits and separate independent reviews per slice.

## 17. Migration decision

**Migration 0016 is not required for R1B-2.** The 0015 vocabulary covers
every mappable concept (§9); safe-detail granularity is writer-enforced;
no retention timestamp, failure marker or outbox table is needed under the
best-effort design. PostgreSQL hex-regex CHECK hardening remains recommended
as a **separate future hardening migration** (per review finding 3), not
part of R1B-2.

## 18. Rollback strategy

R1B-2A: revert the writer-library commit (no runtime call sites, no schema
change — nothing else moves). R1B-2B/C/D: set the writer flag false (instant
inert), then revert the integration commit; evidence rows already written
are additive and harmless; no data repair, no migration rollback, no
position/webhook impact at any stage.

## 19. Blocking risks

None identified for R1B-2A. For later slices, the two watch items are:
(a) the ingest error path ordering — the rejection writer must be inserted
so that the original `PrivateWebhookError` is always re-raised unchanged
(test-enforced byte-diff), and (b) worker outcome translation must remain a
pure function of the committed result dict so a crashed translation can
never affect `_complete_job` (call after it, separate session, swallow).

## 20. R1B-2A authorization recommendation

**APPROVED_TO_IMPLEMENT_R1B2A_WRITERS_ONLY** — build and test the three
insert-only writer libraries (plus the semantic-writer envelope fixes) with
zero production call sites and no migration. Runtime integration (R1B-2B,
R1B-2C, R1B-2D) remains unauthorized until each passes its own independent
review.
