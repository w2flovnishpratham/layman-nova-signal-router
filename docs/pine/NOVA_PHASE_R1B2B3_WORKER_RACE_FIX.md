# NOVA Phase R1B-2B3F — Worker-Race and Owner-Binding Correction

## Status and review boundary

`R1B2B3_WORKER_RACE_FIX_IMPLEMENTED_PENDING_REREVIEW`

This correction addresses the blocking `BLOCKED_WORKER_RACE` verdict in
`NOVA_PHASE_R1B2B4_TRADING_DECISION_REVIEW.md` at review commit
`509197944d11127726a9a5af4e7f3aca4a0c337c`. The blocked review report is
preserved unchanged. This work does not authorize R1B-2C or feature-flag
activation.

## Immutable accepted-action source

There is no dedicated immutable action column on `StrategySignal`, and this
phase forbids a schema change. The selected independent committed ingress
source is:

```text
StrategyExecutionJob.signal_payload.raw_payload.normalized_action
```

`private_webhook_service._persist_execution_job()` writes the normalized
signal payload in the same authoritative transaction that creates its
`StrategySignal`. The job worker reads this payload but does not mutate it.
The evidence helper requires this action to match the server-owned committed
`WebhookEvent.event_metadata.action`. It also cross-checks the instance,
strategy name, signal identifier, private-webhook marker and job-to-signal
relationship. Any mismatch suppresses optional evidence with a closed
diagnostic; neither value wins heuristically.

`StrategySignal.result_summary` is mutable worker output. The real worker's
`_complete_job()` → `_refresh_signal_summary()` path replaces it with a
completed or failed aggregate that need not contain ingress action. The
helper no longer reads `result_summary` at all for action selection,
eligibility, identity, ownership or job binding.

## Owner-scoped job lookup

The execution-job SQL predicate now contains all available committed
bindings:

```text
strategy_signal_id + strategy_name + signal_id + user_id
```

A foreign-owner job therefore selects no row; ownership is not deferred to a
post-query Python rejection.

## Dual fail-closed feature flag

The contract is `DUAL_FAIL_CLOSED_GATE`:

1. Helper observes false: return before identifier parsing, session access,
   query or logging. Later enablement does not activate or backfill the
   completed request.
2. Helper and writer both observe true: attempt the optional decision insert.
3. Helper observes true and writer later observes false: writer raises the
   closed `PERSISTENCE_DISABLED` code; evidence is suppressed, the
   authoritative signal/job and webhook response are unchanged.
4. Re-enabling after the writer check does not retry or backfill that attempt.

The writer's independent gate remains intact. No bypass parameter was added.
The sanitized diagnostic may contain only event name, closed writer version,
`ENTRY|EXIT` action class and closed error code.

## Dynamic and PostgreSQL proofs

SQLite tests execute the real worker completion machinery for BUY_CE, BUY_PE
and EXIT across completed/traded, failed and blocked/no-op results. They also
replace mutable summaries with empty, HOLD and opposite-action values and
prove the decision continues to reflect immutable ingress intent.

The disposable PostgreSQL 18 verification executes:

- real completed-worker summary replacement for all three trading actions;
- simultaneous worker/helper races using separate threads and sessions;
- empty and opposite mutable summaries;
- correct and foreign-owner job reload behavior;
- evidence commit rollback with authoritative rows retained;
- helper-false, true/true and true/false dual-gate behavior.

The PostgreSQL container is removed after verification.

## Invariants and rollback

Evidence remains post-commit and uses a separate session. Failures cannot
change status, serialized response, replay/duplicate behavior, signal, job,
worker execution, router, broker or position state. Decisions record accepted
intent only. Outcome and rejection writers remain disconnected.

Rollback is `R1B_CANONICAL_DECISION_PERSISTENCE=false`, followed by reverting
the correction commit if required. No migration, data repair, prompt,
transport, frontend, worker or HOLD-helper change is involved.
