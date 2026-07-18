# NOVA Phase R1B-2B3 — Trading-Action Canonical Decision Integration

## Scope statement

R1B-2B3 connects the reviewed canonical decision writer to freshly accepted
and committed BUY_CE, BUY_PE and EXIT signals, exactly as designed in
R1B-2B2. Explicitly:

- Canonical decisions record accepted intent only.
- Decisions do not prove execution: an entry decision does not mean an order
  was submitted or traded; an EXIT decision implies neither an open position
  nor a successful broker exit.
- Worker results never alter the immutable decision.
- Missing decision evidence does not invalidate an accepted signal.
- Duplicate requests do not repair missing evidence.
- Outcomes remain disconnected. Rejections remain disconnected.
- The reviewed HOLD integration is untouched (hash-pinned by a test).

## Authoritative commit boundary and call sequence

`_persist_execution_job` ([private_webhook_service.py:545-599](../../backend/app/services/private_webhook_service.py))
commits the StrategySignal **and** its StrategyExecutionJob in one
`session_scope`; the in-block `flush()` calls are not commits — the commit is
the context-manager exit. `wake_strategy_job_worker()` and the authoritative
response dict follow the commit. The new evidence block sits in `ingest`,
after `_persist_execution_job(...)` returns, guarded by:

```python
if (
    status == "fresh"
    and action in trading_canonical_decision_evidence.TRADING_DECISION_ACTIONS
    and not result.get("duplicate")
):
```

so evidence begins only after both authoritative rows are durable, only on
the fresh non-duplicate path, and never from a `finally` or exception
handler. A failure inside `_persist_execution_job` propagates out of
`ingest` before the evidence line is reached (inverse ordering proven in
tests and on PostgreSQL).

## The worker wake-up race, and why transient status is not validated

The worker is woken **before** `ingest` regains control, so by the time the
evidence helper reloads the committed rows the job may already be claimed,
completed, failed or resolved as a no-op, and the signal's status/summary
may have been rewritten by `_complete_job`/`_refresh_signal_summary`. The
helper therefore validates only immutable identity and accepted-intent
facts. The server-owned `WebhookEvent.event_metadata["action"]` must equal
the independently committed
`StrategyExecutionJob.signal_payload["raw_payload"]["normalized_action"]`.
The latter is the normalized signal ingress snapshot written when the job is
created; no runtime service mutates `signal_payload`. The mutable
`StrategySignal.result_summary` is never read as action authority. The helper
also has **no** precondition on `StrategySignal.status`,
`StrategyExecutionJob.status`, attempts, or execution results. Worker-ahead
completed, failed and blocked/no-op results are tested through the real
`_complete_job()` → `_refresh_signal_summary()` machinery and still record
the correct decision, because a decision records accepted intent, not
execution outcome.

## Separate helper and session

[trading_canonical_decision_evidence.py](../../backend/app/services/trading_canonical_decision_evidence.py)
(`persist_trading_decision_best_effort(webhook_event_id,
strategy_instance_id, owner_user_id)`) mirrors the reviewed HOLD helper: the
helper gate is read at entry (`is not True` → return before any parsing,
session, query or logging); one independent `session_scope` reloads
the committed WebhookEvent, StrategyInstance, StrategySignal and
StrategyExecutionJob by committed identifiers/unique keys. The job SQL
predicate includes `strategy_signal_id`, `strategy_name`, `signal_id` and
`user_id`, so a foreign-owner job is not selected. Owner, provider,
metadata-instance, metadata-action, signal↔event, job↔signal and job↔owner
consistency are enforced; the canonical event is built by the approved
legacy adapter from the committed action; the reviewed decision writer runs
inside the evidence session and independently evaluates the same feature
flag at the final persistence boundary. The helper returns `None` always.

## Exact action mappings

Adapter-produced, writer-verified, DB-CHECK-guarded:
BUY_CE → STRATEGY_SIGNAL/BULLISH/DIRECTIONAL_SIGNAL + ENTRY/BUY/CE;
BUY_PE → STRATEGY_SIGNAL/BEARISH/DIRECTIONAL_SIGNAL + ENTRY/BUY/PE;
EXIT → STRATEGY_SIGNAL/FLAT/EXPLICIT_EXIT + EXIT/SELL/null; all rows
`provenance_kind=LIVE`, `adapter_version=nova.legacy-signal-adapter.v1`.

## Exact committed fingerprint provenance

The helper reads the fingerprint only from the committed
`WebhookEvent.event_metadata["payload_fingerprint"]` (server-computed before
the replay claim); its signature has no fingerprint parameter; tests assert
`decision.payload_fingerprint` equals both the committed metadata value and
an independent recomputation, and that a 64-hex user comment cannot replace
it. `raw_body_sha256` remains the replay layer's hash of the canonical
fingerprint representation (not a literal HTTP-body hash).

## Accepted paused and verification-mode behavior

`validate_instance_lifecycle` accepts PAUSED instances for **all** actions;
the entry restriction is a worker-time no-op. Freshly accepted paused
BUY_CE/BUY_PE/EXIT and verification-mode actions therefore commit signal+job
and receive decisions — recording what ingress accepted, not what the worker
later did. No ingress-time pause or verification rejection was introduced
and no worker behavior changed.

## Feature-flag coupling

`R1B_CANONICAL_DECISION_PERSISTENCE` (default/missing/empty/invalid →
false; server-side only) gates **both** the HOLD and trading integrations. The
trading path deliberately uses `DUAL_FAIL_CLOSED_GATE`: the helper must
observe literal `True` before parsing or database access, and the reviewed
insert-only writer independently checks the flag immediately before its
query/insert behavior. Enabling can never activate a request whose helper
already observed false. Disabling can safely suppress optional evidence
before final insertion. A later re-enable does not retry or backfill the
suppressed attempt. This asymmetry is intentional defense in depth; no flag
bypass exists.

## Response equivalence and failure isolation

For each action, the response (status, exact JSON body, content-type) is
identical across flag-off, success, all six closed writer errors, evidence
session outage, evidence commit failure, unexpected helper exception and
logging failure; no response ever contains a decision ID or evidence status.
All failures are double-suppressed at the call site; the only diagnostic is
`event=r1b_trading_decision_evidence_failed writer_version=…
[action_class=ENTRY|EXIT] error_code=…` with codes clamped to a closed set —
never credential, signal id, fingerprint, source, email, broker response,
SQL text or path (log-capture tested). The committed signal and job stay
durable and claimable in every failure mode.

## Duplicate/no-backfill, owner/reference safety

Identical duplicates (including after a later flag enable) return the
existing duplicate response and never backfill; conflicting duplicates
(409) never produce a decision for the rejected request and never alter the
winner's decision. Foreign owner/instance/event, HOLD-event↔trading-helper
mismatch, missing signal/event/instance/job and deleted references all
suppress evidence only.

## Static call-site proof

[test_r1b2b3_runtime_call_sites.py](../../backend/app/tests/test_r1b2b3_runtime_call_sites.py):
AST call-graph proves `persist_canonical_signal_decision` has exactly two
callers (the HOLD helper and the trading helper); each helper has exactly
one `ingest` caller; the trading call's `If`-ancestry contains
`status == 'fresh'`, `TRADING_DECISION_ACTIONS` and
`result.get('duplicate')`; the trading helper imports no execution/broker/
position machinery and does not import the HOLD helper; outcome and
rejection writers keep zero production callers; and the reviewed HOLD helper
is **hash-pinned** to its exact R1B-2B-approved bytes — any edit fails the
suite and forces a new review. The 2B-era static tests were updated to the
new two-helper boundary (the changes this integration causes), and the
forced-flags dynamic proof now expects exactly the three authorized
decisions (HOLD + fresh entry + paused entry) with every rejected flow still
writing nothing.

## PostgreSQL verification

[scripts/r1b2b3_pg_verify.py](../../backend/scripts/r1b2b3_pg_verify.py) on
a disposable `postgres:18-alpine` (removed after): flag-off zero-write;
duplicate/no-backfill after flag enable; exact mapping + committed
fingerprint + job per action; conflicting duplicate (winner decision only);
concurrent identical ingest races per action (one signal/job/decision);
direct-helper unique race (one row); worker-ahead states; evidence DB outage
and evidence **commit-failure rollback** after the authoritative commit
(signal/job durable, decision fully rolled back — PostgreSQL is
authoritative here because the pysqlite driver auto-commits around
SAVEPOINT); authoritative failure inverse (zero helper calls);
owner-scoped job suppression; real worker-ahead mutation and worker/helper
races; adversarial mutable summaries; and the dual fail-closed gate. ALL
PASS.

## Rollback

1. `R1B_CANONICAL_DECISION_PERSISTENCE=false` — both helpers return before
   any database access.
2. Revert the single R1B-2B3 commit.
3. Already-written decisions remain optional immutable audit evidence.
4. No signal/job/replay/position changes; no database downgrade; no data
   repair.

## Remaining boundaries

R1B-2B4 independent review gates this integration and any flag enablement.
R1B-2C (outcomes, at the worker's `_complete_job`) remains blocked until
trading decisions are approved; R1B-2D (authenticated rejections) follows
later. Historical backfill, signal-ledger APIs, live activation and AI
conversion remain unauthorized.
