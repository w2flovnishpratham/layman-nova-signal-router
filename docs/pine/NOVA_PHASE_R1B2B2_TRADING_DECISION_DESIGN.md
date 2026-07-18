# NOVA Phase R1B-2B2 — Trading-Action Canonical Decision Design

## 1. Executive recommendation

**Verdict: APPROVED_TO_IMPLEMENT_R1B2B3_TRADING_DECISIONS**

- Integration point: in `private_webhook_service.ingest`, immediately after
  `_persist_execution_job(...)` returns, guarded by
  `status == "fresh" and not result.get("duplicate")` — strictly after the
  one transaction that commits both the StrategySignal and its
  StrategyExecutionJob, and after the authoritative response dict is already
  built.
- Helper architecture: **Option B** — a new, separate
  `trading_canonical_decision_evidence.py` helper
  (`persist_trading_decision_best_effort`) mirroring the reviewed HOLD
  helper pattern; the reviewed HOLD path is not touched.
- Fingerprint: read exclusively from the committed
  `WebhookEvent.event_metadata["payload_fingerprint"]`; the helper signature
  carries committed identifiers only (no fingerprint, no action-as-text
  override, no compatibility fields).
- Eligibility: fresh replay claim + committed signal + committed job +
  committed action ∈ {BUY_CE, BUY_PE, EXIT}; committed-job proof is
  **required** (the private path guarantees exactly one job per accepted
  trading signal — no legitimate job-less accepted EXIT exists).
- Flag rollout: **Approach 1** — reuse `R1B_CANONICAL_DECISION_PERSISTENCE`
  (no new flag); it must be false during deployment, and enabling it after
  R1B-2B3/2B4 activates **both** HOLD and trading-action decisions.
- Migration 0016: **not required**.
- Decisions record accepted strategy intent only; worker no-ops, router
  blocks, broker failures and reversals are future outcome evidence and
  never touch a decision row.

## 2. Current trading-action flow map

Grounded at `990385f` (all paths re-verified this phase). The shared ingress
spine is identical for the three actions — proven, not assumed: `ingest`
(`private_webhook_service.py:388-465`) branches only at
`action == "HOLD"` (svc:445); every non-HOLD accepted action flows through
the same `_persist_execution_job` (svc:545-599). Entries and EXIT therefore
share one transaction path; they differ only in the worker's later handling.

| # | Flow | Path / boundary | Replay | Signal / Job | HTTP | Decision? | Evidence point / failure behavior |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1-3 | Fresh BUY_CE / BUY_PE / EXIT | `ingest` → claim (txn B, replay store 72-92) → `_persist_execution_job` (txn C: signal+job, svc:554-581) → wake worker → response | fresh | 1 + 1 committed | 202 `{ok, signal_id, status: "QUEUED", action}` | **yes** | after `_persist_execution_job` returns; failure = metric only |
| 4-6 | Identical duplicate | claim → `duplicate` → `_existing_status_response` (svc:602-627) | duplicate | unchanged | 200 `{duplicate: true, …}` | no | never invoked (`status != "fresh"`) |
| 7 | Conflicting duplicate | claim → `tampered` → raise (svc:425-431) | tampered | none | 409 CONFLICTING_DUPLICATE | no | never reached |
| 8-10 | Stale / future-skew entry or EXIT | `validate_signal_time` (svc:121-136) raises before claim | — | none | 422 STALE_SIGNAL / MISSING_TIMEZONE | no | never reached |
| 11 | Invalid action | `normalize_action` (svc:391-396) | — | none | 422 INVALID_ACTION | no | never reached |
| 12-13 | Invalid / revoked credential | `authenticate_credential` (svc:162-218) uniform 401 | — | none | 401 | no | never reached |
| 14 | Foreign-instance attempt | credential→instance binding; owner isolation | — | none | 401/404 | no | never reached |
| 15-17 | Paused BUY_CE / BUY_PE / EXIT | `validate_instance_lifecycle` (svc:238) **accepts PAUSED**; signal+job commit normally; the entry block happens later in the worker (`_revalidate_instance`, exe:76-77 → `INSTANCE_PAUSED_ENTRIES_BLOCKED` no-op); paused EXIT executes | fresh | 1 + 1 | 202 QUEUED | **yes** (intent was accepted) | same as fresh; worker outcome is future R1B-2C evidence |
| 18-20 | Verification-mode actions | lifecycle allows verification paper instances (svc:237-252); worker enforces paper-only (exe:85-89) | fresh | 1 + 1 | 202 | **yes** | same as fresh |
| 21 | Accepted, worker later no-op (ALREADY_IN_CE/PE, ALREADY_FLAT; exe:196-205) | worker path, after evidence | fresh | job completes NO_OP | 202 already sent | decision already exists | decision untouched; no outcome yet |
| 22 | Accepted, worker rejects (INACTIVE_INSTANCE / INVALID_LOTS / TENANT_MISMATCH, exe:69-92; blocked entitlement/risk, fan:783-831) | worker | fresh | job rejected/blocked | 202 already sent | decision exists | intent evidence stays valid |
| 23 | Accepted, broker later fails (router statuses rtr:1585-2328) | worker/router | fresh | job failed | 202 already sent | decision exists | broker result never touches the decision |
| 24 | Accepted reversal (opposite side while open; router reversal sequencing rtr:1972-2108) | worker/router | fresh | 1 + 1 | 202 | **one** decision (desired state) | exit-first/entry steps are future outcomes |

## 3. Authoritative commit boundaries

**POST_COMMIT_TRADING_DECISION_POINT_IDENTIFIED**

`_persist_execution_job` (svc:545-599) opens exactly one `session_scope`;
the StrategySignal and StrategyExecutionJob are added and flushed inside it
(`flush()` at svc:566/581 is *not* commit); the commit happens at the
context-manager exit (svc:581→583 boundary); `wake_strategy_job_worker()`
(svc:591-593) and the response dict (svc:594-599) follow the commit. The
IntegrityError branch (svc:582-589) returns a duplicate response without a
fresh commit.

Correct integration point: **immediately after `_persist_execution_job()`
returns inside `ingest`** — one level above the transaction context, exactly
mirroring the reviewed HOLD placement (svc:445-460):

```python
else:
    result = _persist_execution_job(auth, payload, action, strategy_name, received_at)
    if status == "fresh" and not result.get("duplicate"):
        try:
            trading_canonical_decision_evidence.persist_trading_decision_best_effort(
                webhook_event_id=claim.get("webhook_event_id"),
                strategy_instance_id=auth["instance_id"],
                owner_user_id=auth["user_id"],
            )
        except Exception:
            …suppressed sanitized recorder, mirroring the HOLD block…
```

The `not result.get("duplicate")` guard is stricter than the HOLD call site:
it also excludes the theoretically unreachable fresh-claim+IntegrityError
branch that the R1B-2B review documented as an accepted edge. Forbidden
placements (inside txn C, before job commit, in `finally`, passing detached
uncommitted ORM objects) are all avoided by construction; an exception from
`_persist_execution_job` propagates out of `ingest` before the evidence line
is reached (same inverse-ordering property proven for HOLD).

Note on timing: because the worker is woken *before* `ingest` regains
control, the job may already be claimed — or finished — when the evidence
helper runs. The design therefore forbids any signal/job **status**
precondition in the helper (§8); eligibility binds only to immutable
committed facts.

## 4. Decision eligibility matrix

A decision may be attempted iff **all** hold: credential resolved; owner +
instance resolved; lifecycle + freshness passed; replay claim returned
exactly `fresh`; the fresh (non-duplicate) response path was taken;
StrategySignal committed; StrategyExecutionJob committed; committed action ∈
{BUY_CE, BUY_PE, EXIT}.

| Ingress result | Decision |
| --- | --- |
| Fresh accepted BUY_CE / BUY_PE / EXIT (active, paused, or verification instance) | attempt one |
| Identical duplicate / conflicting duplicate / stale / future-skew / malformed / invalid action / invalid or revoked credential / lifecycle rejection (stopped, non-personal, live-verification block) | never |
| Fresh HOLD | existing reviewed path (unchanged) |

Paused behavior determined from code, not assumption: `PAUSED` instances
pass ingress for **all** actions (svc:238); the entry restriction is a
worker-time no-op, so paused entries and paused EXITs both produce committed
signal+job and therefore decisions. Verification-mode instances likewise.

## 5. Canonical mappings

Produced solely by `adapt_legacy_action` (the approved adapter) applied to
the committed action, then mapped by the reviewed writer's closed inverse
tables — no hand-written mapping:

| Action | event_type / desired_state / intent_reason | compatibility |
| --- | --- | --- |
| BUY_CE | STRATEGY_SIGNAL / BULLISH / DIRECTIONAL_SIGNAL | ENTRY / BUY / CE |
| BUY_PE | STRATEGY_SIGNAL / BEARISH / DIRECTIONAL_SIGNAL | ENTRY / BUY / PE |
| EXIT | STRATEGY_SIGNAL / FLAT / EXPLICIT_EXIT | EXIT / SELL / null |

All rows: `provenance_kind=LIVE`,
`adapter_version=nova.legacy-signal-adapter.v1`. Explicit semantics: a
BUY_CE/BUY_PE decision does **not** imply an order succeeded; an EXIT
decision implies neither that a position existed nor that a broker exit
succeeded; the row records accepted strategy intent only, and the schema has
no column for router or broker results (structurally enforced).

## 6. Fingerprint provenance

Identical to the reviewed HOLD guarantee: the canonical fingerprint is
computed server-side by `payload_fingerprint` (svc:139-155) *before* the
replay claim, committed as server-owned
`WebhookEvent.event_metadata["payload_fingerprint"]` (svc:408-417), and read
by the helper **only** from that committed record. The helper signature has
no fingerprint parameter; request JSON, Pine metadata, comments, frontend
input, worker results, job metadata and router output cannot reach it.
Required test: `decision.payload_fingerprint ==
event.event_metadata["payload_fingerprint"] ==` an independent
recomputation, and a 64-hex user comment must not substitute it.

Relationships (restated to prevent drift): `payload_fingerprint` = canonical
alert identity hash; `WebhookEvent.raw_body_sha256` = SHA-256 **of the
canonical fingerprint string** (the private path passes the fingerprint as
`raw_body` to the claim; it is not a literal HTTP-body hash); replay
identity = (provider, event_id) with `raw_body_sha256` as tamper check; the
fingerprint participates in the decision writer's conflict tuple, so a
different fingerprint for the same signal is `CANONICAL_DECISION_CONFLICT`,
never a replacement.

## 7. Helper architecture decision

**Option B — separate trading helper**, file
`backend/app/services/trading_canonical_decision_evidence.py`:

```python
persist_trading_decision_best_effort(
    *,
    webhook_event_id: uuid.UUID | str | None,
    strategy_instance_id: uuid.UUID | str,
    owner_user_id: uuid.UUID | str,
) -> None
```

plus a sanitized `record_trading_decision_failure(code, action_class=None)`.

Rationale vs alternatives: Option A (extend the HOLD helper) modifies a
reviewed, frozen path and widens its action gate — rejected. Option C
(shared loader module) requires refactoring the HOLD helper to import the
shared code, which is still a modification of the reviewed file — deferred;
the ~60 duplicated loading/validation lines are small, stable and
independently testable, and static call-site proofs stay trivial (one more
allowed module, one more single-caller assertion). Consolidation may be
revisited later only with a proof that HOLD behavior and its review
guarantees are bit-identical.

The helper must not accept: action text (read from committed state),
fingerprint, desired state, compatibility fields, quantity, lots, strike,
expiry, security ID, execution mode, broker/worker results.

## 8. Separate-session design

Mirroring the reviewed HOLD helper, with the trading-specific deltas:

1. `settings.R1B_CANONICAL_DECISION_PERSISTENCE is not True → return`
   (before any parsing or DB access).
2. Open one independent `session_scope()`.
3. Reload committed WebhookEvent, StrategyInstance by id; verify
   event.user_id == instance.user_id == owner, event.provider ==
   `instance-webhook:{instance.id}`, metadata instance id matches.
4. Committed action check: `metadata["action"] ∈ {BUY_CE, BUY_PE, EXIT}`.
5. Reload the committed StrategySignal by
   (`instance:{instance.id}`, event.event_id); require
   `result_summary["action"]` to equal the metadata action. **No
   signal.status precondition** — the worker may already have advanced it
   (queued/processing/completed/completed_with_errors are all acceptable);
   binding uses only immutable committed facts. (Contrast: the HOLD helper's
   `status == "completed"` check is safe because HOLD has no job.)
6. **Job proof (required):** reload the committed StrategyExecutionJob by
   (strategy_name, signal_id) and require `strategy_signal_id == signal.id`
   and `user_id == owner`. The private path guarantees one job per accepted
   trading signal (svc:567-580; unique `uq_strategy_signal_user_job`); no
   accepted EXIT path is job-less, so no global weakening is needed. Job
   status is deliberately unconstrained.
7. Build the canonical event via `adapt_legacy_action(metadata_action,
   signal_id=signal.signal_id, signal_time=parse(metadata["signal_time"]))`.
8. Call the reviewed `persist_canonical_signal_decision` with the committed
   fingerprint and `received_at` from event metadata.
9. Evidence session commits at scope exit; any failure rolls back and closes
   only that session; return `None` always.

Freshness remains enforced solely at the single guarded call site (carried
forward from R1B-2B review finding 2, restated here as a standing
constraint): the helper verifies "committed genuine trading signal", the
ingress gate verifies "fresh".

## 9. Failure isolation

**BEST_EFFORT_SYNCHRONOUS_POST_COMMIT** (approved policy). All helper and
writer failures — disabled, invalid committed input, owner/instance
mismatch, foreign reference, canonical conflict, taint, DB
unavailable/timeout, evidence commit failure, unexpected exception, logging
failure — are suppressed at the integration boundary with the same
double-suppression structure as HOLD. Diagnostic format:
`event=r1b_trading_decision_evidence_failed writer_version=<closed>
action_class=<ENTRY|EXIT, only when derived from committed metadata>
error_code=<closed>` — never credential, signal ID, fingerprint, source,
email, broker response, SQL text, path or request body. Evidence failure
must not change webhook status/body/headers, replay identity, duplicate
semantics, signal, job, worker scheduling, claimability, execution or
position state — each asserted by the failure-isolation suite (§17).

## 10. Response equivalence

For each of BUY_CE/BUY_PE/EXIT, capture and compare (status, parsed body,
content-type, duplicate semantics, and the signal/job fields already exposed
by `list_webhook_executions`) across: flag false; flag true + success;
writer closed error; evidence DB outage; evidence commit failure; taint
suppression; unexpected helper exception; logging failure. The fresh
authoritative body is exactly `{"ok": true, "signal_id": …, "status":
"QUEUED", "action": …}` (svc:594-599) and must remain byte-equal. No
response may ever contain a canonical decision ID, evidence status, error or
retry field. Any evidence-dependent difference is a blocker for R1B-2B4.

## 11. Feature-flag rollout

**Approach 1 — reuse `R1B_CANONICAL_DECISION_PERSISTENCE`.** No new flag
(honors the original R1B anti-flag-explosion rule); Approach 2 adds a
temporary flag with near-zero rollback value over reverting one commit;
Approach 3 (allowlist config) is configuration surface without a present
need. Operational contract, stated explicitly:

- the flag defaults false and **must be false during deployment**;
- after R1B-2B3 lands and R1B-2B4 approves, enabling the flag activates
  **both HOLD and trading-action decisions** — this is the documented,
  intended coupling;
- enable first in the paper environment, observe evidence rows and the
  `r1b_*_decision_evidence_failed` diagnostics, then consider production;
- rollback = set false (writers return before DB access), then revert the
  integration commit if needed.

Flag flipped between authoritative commit and evidence attempt
(deterministic rule): the flag is evaluated exactly once, at helper entry —
flip-to-false mid-request ⇒ no decision (a permanent, acceptable evidence
gap; no repair); flip-to-true mid-request ⇒ the decision is written iff the
helper had not yet checked. No re-check, no retry.

## 12. Worker/outcome separation

Decisions are written from the ingress thread immediately after the
signal/job commit and never from the worker; the worker, router, brokers and
position services cannot reach the writer (import graph + static tests).
Proven consequences: accepted BUY_CE with a later worker no-op → decision
exists, no outcome; accepted EXIT while flat → decision exists (signal+job
are committed regardless; the ALREADY_FLAT no-op is worker-time,
exe:202-203), no outcome; accepted reversal → exactly one decision recording
the desired state, exit-first/entry sequencing belongs to future outcomes;
broker rejection → decision remains valid intent evidence. Decision
persistence is never delayed until worker completion, and the race with an
already-running worker is harmless by design (§8 step 5).

## 13. Duplicate/no-backfill policy

Natural key stays `strategy_signal_id`. Fresh accepted action with flag
false → no decision; the same request redelivered with flag true → duplicate
response unchanged, no repair; conflicting duplicate → 409, no decision for
the rejected request, the accepted signal untouched. No scans for
decision-less signals, no duplicate-triggered repair, no startup/scheduled
repair, no worker-side implicit backfill. Historical backfill remains a
separate unauthorized phase.

## 14. Owner/reference safety

The helper re-verifies, from committed state only: event↔owner,
instance↔owner, event↔instance (provider + metadata), signal↔instance
(strategy name), signal↔event (event_id), job↔signal (FK id match) and
job↔owner. Required tests: foreign owner / instance / signal / event / job;
signal↔event mismatch; signal↔job mismatch; provider referencing another
instance; metadata action mismatch (incl. HOLD metadata reaching the trading
helper and vice versa); missing/deleted signal, event, job. Every mismatch
suppresses evidence only — committed signal and job intact, response
unchanged, no cross-owner row.

## 15. Concurrency design

PostgreSQL 18, disposable container, real threads + barrier through the real
`ingest` (extend `scripts/r1b2b_pg_verify.py`'s pattern into a new
`scripts/r1b2b3_pg_verify.py`):

- Concurrent identical BUY_CE (and BUY_PE, EXIT): one 202 QUEUED + one
  duplicate; one WebhookEvent identity; one signal; **one job**; one
  decision; no duplicate job (guarded by `uq_strategy_signal_user_job`).
- Concurrent conflicting duplicate: one accepted + one 409; one signal; one
  job; one decision belonging to the winner's fingerprint.
- Evidence unique race (two direct helper invocations): one immutable row,
  both callers resolve the same id, no execution rerun (the helper cannot
  reach execution).
- Evidence timeout / commit failure after authoritative commit: response
  unchanged, signal+job durable, worker proceeds normally.
- Authoritative job commit failure: evidence invocation count 0.
- Delayed helper invocation (job already completed by the worker): decision
  still written — proves the no-status-precondition rule.
- Flag flip between commit and evidence: deterministic single-evaluation
  rule of §11.

## 16. Static call-site constraints

Post-R1B-2B3 expected AST-proven counts:
`persist_canonical_signal_decision` callers = {HOLD helper, trading helper};
`persist_hold_decision_best_effort` callers = {ingest, ×1};
`persist_trading_decision_best_effort` callers = {ingest, ×1} with
`If`-ancestor guards `status == 'fresh'` **and** the non-HOLD branch (and
the `not result.get("duplicate")` condition); outcome and rejection writers:
0 production callers; forbidden files (worker, router, brokers, position,
scheduler, startup, background) contain none of the helper/writer names; no
dynamic import or string resolution. The existing
`test_r1b2a_zero_call_sites.py` / `test_r1b2b_runtime_call_sites.py`
patterns extend directly; allowlists grow by exactly the one new helper
module.

## 17. Test architecture

Files: `test_r1b2b2_trading_decision_integration.py`,
`test_r1b2b2_trading_decision_failure_isolation.py`,
`test_r1b2b2_trading_decision_concurrency.py` (SQLite thread-level; PG
authoritative via script), `test_r1b2b2_runtime_call_sites.py`. The thirty
required scenarios enumerated in the prompt map 1:1 onto the suites; the
notable per-scenario anchors: exact mapping per action against the DB
cross-field CHECKs (2-5); committed references (6-7); fingerprint equality +
user-metadata substitution attempt (8-9); response equivalence matrix (10-11,
per §10); job untouched incl. `signal_payload` byte-equality (12);
duplicate/conflict/no-backfill (13-14); pre-ingress rejections (15-17);
paused entry **and** paused EXIT unchanged (18-19); verification mode (20);
inverse ordering (21); foreign references (22); concurrency (23-24); HOLD
regression — behavior unchanged and still exactly one HOLD path (25-26);
outcome/rejection zero paths (27-28); import-graph freeze (29); no evidence
field in any response (30).

## 18. Migration decision

**No migration 0016.** The 0015 `canonical_signal_decisions` schema already
supports all three actions (cross-field CHECKs `ck_canonical_decision_
buy_ce/buy_pe/exit_consistency` were built for exactly these shapes in
R1B-1 and are constraint-tested), LIVE provenance, the adapter version
width, the fingerprint column and owner/instance linkage. Hash-charset
hardening and DB-trigger immutability remain separate future hardening
topics (carried findings).

## 19. Rollback

1. `R1B_CANONICAL_DECISION_PERSISTENCE=false` (writers return before DB
   access — already proven).
2. Revert the single future R1B-2B3 commit.
3. Existing immutable decision rows remain as optional audit evidence.
4. No signal/job repair, no downgrade, no data migration.

## 20. Blocking risks

None identified for the design. Two carried constraints for R1B-2B3/2B4:
(a) the freshness gate lives at the call site — any new caller must
replicate it (R1B-2B review finding 2, restated in §8); (b) the fingerprint
must be proven to be the committed event value at the new call site (R1B-2A
review finding 1, restated in §6). One implementation watch item: the
helper must not assert on signal/job status because the worker races ahead
(§3 note, §8 step 5-6) — a status precondition copied naively from the HOLD
helper would introduce a flaky evidence gap.

## 21. Implementation authorization recommendation

Proceed to **R1B-2B3**: implement the trading helper + single guarded call
site + the four test files + PG script + documentation, flag default false,
zero changes to the HOLD path, outcomes, rejections, worker, router,
brokers, models or migrations. R1B-2B4 independent review follows before
the flag may be enabled anywhere. Outcomes (R1B-2C) remain blocked until
trading decisions exist; rejections (R1B-2D) follow later.
