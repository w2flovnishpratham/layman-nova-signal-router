# Phase 2I-0 — Next Capability Design Review (design only, no code)

Status: Proposed

This is a design review only. No backend code, frontend code, database migrations,
routes, APIs, tests, or docs other than this file were added in this phase.

## 1. Preconditions Verified

All four Phase 2H checkpoints exist before this review started:

- `pre-multistrategy-phase2h0`
- `pre-multistrategy-phase2h1`
- `pre-multistrategy-phase2h2`
- `pre-multistrategy-phase2h3`

(`phase2h2` — archive/restore — had been implemented and tested in the working
tree but not yet committed/tagged; it was committed and tagged before this
review began, so the checkpoint sequence is now unbroken.)

## 2. Current Verified Baseline (do not redesign)

Grounded from `backend/app/routers/debug.py`, `backend/app/db/models.py`, and
`backend/app/services/strategy_fanout_v2.py` at review time:

- Read-only V2 Paper status panel and read-only strategy catalog.
- Lifecycle endpoints under the debug router only:
  `POST /api/debug/v2/instances` (create, born paused),
  `.../pause`, `.../resume`, `.../settings` (paused-only edit of
  `instance_label`/`lots`/`side_preference`), `.../archive` (paused → archived),
  `.../restore` (archived → paused).
- `GET /api/debug/v2/instances/{id}/details` — read-only safe details + sanitized
  lifecycle/settings history.
- Gate stack on every mutation: `DEBUG_ENABLED` (settings) → router 404 when off,
  `MULTI_STRATEGY_MODEL` + `STRATEGY_INSTANCE_MUTATION_DEBUG` feature flags,
  admin/dev internal user (`_require_internal_mutation_user`), ownership check
  (non-owner → 404, existence not leaked), `confirm_paper_only: true` body flag.
- Eligible modes for every lifecycle/settings/archive mutation:
  `paper_live_data` and `signal_only` only. `real_orders` → 403, and a partial
  unique index (`uq_user_strategy_instances_active_real_orders_per_user`) caps
  active real_orders instances at one per user at the DB layer regardless of API
  behavior.
- Planner (`plan_strategy_fanout_v2`) skips non-active instances with typed skip
  reasons: `PAUSED_INSTANCE`, `ARCHIVED_INSTANCE`, `DISABLED_INSTANCE`,
  `INACTIVE_INSTANCE`.
- Audit: one `AuditLog` row per state-changing mutation
  (`V2_INSTANCE_CREATED/PAUSED/RESUMED/SETTINGS_EDITED/ARCHIVED/RESTORED`),
  actor/instance/strategy_code/previous→new status or fields, no request bodies,
  no secrets. Idempotent no-op calls do not write or audit.
- `user_strategy_webhook_credentials` table exists in the schema but has **zero
  production writers** — only referenced by tests/scripts asserting it stays
  empty. No webhook generation, rotation, or revocation code exists anywhere.
- No route to `route_signal`, no PaperBroker calls, no Dhan calls, no worker,
  scheduler, or startup wiring for any of this. Confirmed by spy-fixture tests
  across all phase2f/2g/2h test files and the lifecycle regression matrix
  (`phase2h3`).
- No generic per-user mutation rate limiter exists. Rate limiting today is
  scoped to Dhan API calls (`dhan_rate_limiter.py`) and public webhook ingress
  (`_webhook_rate_limited` in `routers/strategies.py` / `routers/webhook.py`) —
  neither applies to the debug instance-mutation surface.

Do not redesign any of the above in this phase.

## 3. Candidates

**A — Paper strategy clone/duplicate.** Copy configuration only into a new,
born-paused instance. Never copy runtime state, history, signals, jobs, or
webhook credentials.

**B — Read-only analytics/history enhancements.** Planner history, lifecycle
timeline, settings history, audit summaries. No writes.

**C — Internal paper execution controls.** One-click run/process from the debug
panel. Review only in this phase.

**D — Webhook credential management.** Secret lifecycle: generation, rotation,
display-once, revoke. Review only.

**E — Public strategy catalog.** Widening the currently admin/dev-gated
read-only catalog to all authenticated users (or unauthenticated). Review only.

**F — Public TradingView webhook.** Threat model only.

**G — Live (`real_orders`) activation.** Must almost certainly remain deferred.

## 4. Candidate Comparison

| Candidate | Safety | Product usefulness | Engineering complexity (lower=simpler) | Regression risk | Live risk | Operational burden | Rollback complexity |
|---|---|---|---|---|---|---|---|
| **A** Clone/duplicate | 9 | 8 | 3 | 2 | 1 | 1 | 1 |
| **B** Read-only analytics/history | 10 | 6 | 3 | 1 | 1 | 1 | 1 |
| **C** Internal paper execution controls | 4 | 7 | 5 | 5 | 4 | 3 | 3 |
| **D** Webhook credential management | 6 | 3 (no consumer yet) | 6 | 3 | 2 | 4 | 3 |
| **E** Public strategy catalog | 6 | 6 | 4 | 3 | 1 | 3 | 2 |
| **F** Public TradingView webhook | 4 | 9 (later) | 8 | 6 | 5 | 6 | 5 |
| **G** Live (`real_orders`) activation | 1 | — | — | — | 10 | — | — |

Notes:

- **A**: Same table, same gate stack, same audit shape as every prior phase2f/
  2g/2h mutation. The only genuinely new concern is copying *configuration*
  without ever copying *identity-bearing or execution-adjacent* state (see §6).
  Complexity is low because create-born-paused already validates catalog/
  version/mode; clone reuses that exact path with a source instance as input
  instead of a bare `catalog_code`.
- **B**: Safest possible next step — no new write path at all, reuses the
  existing details/history projection and safe-field allowlist from 2H-1.
  Lower product value than A because it doesn't reduce the "recreate from
  scratch" friction that clone solves.
- **C**: The runner path still ends in `route_signal` via the paper bridge.
  A one-click execution button from a debug panel is a boundary crossing —
  same conclusion as the 2G-0 review — and deserves a dedicated review only
  after clone and analytics have soaked, not bundled into this phase.
- **D**: The webhook credential table has been sitting in the schema unused
  since at least phase2f. Building rotation/revoke/display-once now means
  maintaining a live secret-management surface with no consumer (no public
  webhook route exists to authenticate against). Same conclusion as 2G-0: only
  worth building alongside E/F as one security unit.
- **E**: Not a styling change — the read-only endpoints are gated by
  `_require_internal_strategy_status_user` today. Widening the audience is an
  access-control decision that needs its own review scoped to auth, not
  bundled with a feature build.
- **F**: Needs D first (nothing to authenticate an inbound custom webhook
  against without credentials), plus a dedicated replay/rate-limit/threat
  review. Out of scope until D is scheduled.
- **G**: Stays blocked. Planner skip, adapter block, and the DB partial-unique
  index all remain load-bearing safety controls; nothing in this review
  changes that.

## 5. Recommended Order

1. **Phase 2I-1 — Clone/duplicate (Candidate A).** Highest product value at the
   lowest incremental risk; reuses the exact gate stack and creation path
   that's already been through four rounds of review and regression testing.
2. **Phase 2I-2 (or folded into 2I-1 if trivial) — read-only analytics/history
   enhancements (Candidate B).** Zero write-path risk; can happen any time,
   including in parallel with A's frontend work, but is lower priority because
   it doesn't unblock a new user workflow the way clone does.
3. Candidate C only after A and B have soaked, and only with its own dedicated
   execution-boundary review (same posture as 2G-0's conclusion on this
   candidate — nothing has changed to justify moving it up).
4. Candidates D and F stay paired and deferred together — building D without F
   creates an unused secret system; building F without D has nothing to
   authenticate against.
5. Candidate E deferred until product explicitly wants the catalog public —
   it's an auth-scope decision, not a feature build.
6. Candidate G remains blocked indefinitely absent a separate, explicit,
   dedicated live-trading safety review.

Why not B first: B is strictly safer but delivers materially less — it doesn't
remove the "recreate the instance from scratch to try a variant" friction that
motivates A. Given A's risk profile is already close to B's (same gate stack,
same audience, same paper-only guarantee), the extra product value justifies
sequencing A first.

## 6. Candidate A Design (for Phase 2I-1 — not implemented in this phase)

### Backend API

```
POST /api/debug/v2/instances/{source_instance_id}/clone
body: { "confirm_paper_only": true }
```

Reuses the create-born-paused code path (`create_v2_paper_instance_debug`)
with the source instance substituted for a bare `catalog_code` lookup. Decision
tree: `DEBUG_ENABLED` → `MULTI_STRATEGY_MODEL` + `STRATEGY_INSTANCE_MUTATION_DEBUG`
→ admin/dev user → source instance exists and is owned by the caller (404 if
not, existence not leaked, same as every other endpoint) → source
`execution_mode` is `paper_live_data` or `signal_only` (`real_orders` source →
403, cloning a real_orders instance is out of scope entirely) →
`confirm_paper_only` → build a new `UserStrategyInstance` row.

No new request fields beyond `confirm_paper_only` — clone takes no
overrides in 2I-1. If a user wants a different label/lots/side on the clone,
they edit it after creation via the existing paused-only settings endpoint.
Keeping the clone request body empty avoids re-litigating the settings
validation surface inside a new endpoint.

### What Is Copied

- `strategy_id`, `strategy_version_id` (pinned to the *source's* version, not
  re-resolved to "latest active" — a clone should reproduce what the source
  was actually running, not silently upgrade it)
- `source_type`
- `lots`
- `side_preference`
- `instance_label`, with a suffix (e.g. `"{label} (Copy)"`, truncated to the
  120-char cap via the existing `_sanitized_instance_label`) so cloned rows
  are visually distinguishable in the table

### What Is Intentionally NOT Copied

- `id` — fresh UUID, always
- `legacy_subscription_id` — always null; a clone is never linked to a legacy
  subscription row
- `status` — always `paused`, regardless of source status (including cloning
  an `archived` source — the clone is a fresh paused instance, not a restore)
- `execution_mode` — always `paper_live_data`, matching create-born-paused
  today (source `signal_only` instances still clone to `paper_live_data`,
  consistent with the existing create endpoint's hardcoded invariant — cloning
  does not need a new mode-selection surface)
- `config_json` — fresh object (`{"created_by": "debug_lifecycle_v2i1",
  "born_paused": true, "cloned_from": "<source_instance_id>"}`), never the
  source's raw `config_json` wholesale (it may carry breadcrumbs from prior
  mutations that shouldn't propagate)
- `risk_config` — fresh `{}`, never copied
- `created_at` / `updated_at` — fresh timestamps
- runtime state — none exists per-instance today beyond the row itself, so
  there is nothing to copy; this stays true after clone
- analytics / order history — instance has no history table rows to copy;
  clone starts with zero lifecycle/settings history entries
- `user_strategy_webhook_credentials` rows — none exist for any instance
  today; clone must not become the first code path that writes to this table
- execution jobs / signals — clone never touches `strategy_signals`,
  `normalized_option_signals`, or `strategy_execution_jobs`

### Frontend UX

Inside `StrategyCatalogStatusPanel`'s instances table only: a "Clone" action
alongside the existing lifecycle controls, available for any lifecycle-eligible
instance regardless of its current status (active, paused, or archived can all
be cloned — cloning reads the source, it doesn't mutate it). Same two-step
confirm pattern and "Paper only. No orders will be placed." copy as every other
mutation. On success, refetch the instance list (no optimistic insert) so the
new paused row appears via the same path as a manually created instance.
Gated by the same existing flag pair
(`VITE_ENABLE_STRATEGY_CATALOG_STATUS` + `VITE_ENABLE_STRATEGY_INSTANCE_MUTATION`)
— no new frontend flag, consistent with 2G-1/2H-2 precedent (same mutation
surface, same audience, same risk class).

### Audit

One `V2_INSTANCE_CLONED` event per successful clone:
`actor_user_id`, `source_instance_id`, `new_instance_id`, `strategy_code`,
copied field names (not raw secret-bearing values — there are none in the
copied set), flag snapshot. No request body logged (there's nothing in it
beyond the confirmation flag).

### Rollback / Undo

A clone is its own undo — delete/archive the new instance and nothing about
the source is affected (clone never mutates the source row). Operationally:
turning off either feature flag removes the Clone control immediately, same
as every other mutation control. No migration required — no new columns or
tables, only a new endpoint and a new audit event type.

### Planner Interaction

The clone is born paused, so it is invisible to
`plan_strategy_fanout_v2` until explicitly resumed by the user — identical
guarantee to create-born-paused. No planning cycle can ever observe a
half-cloned instance because the row is inserted with `status="paused"` in the
same transaction as every other field.

## 7. Clone Safety Requirements

If Candidate A is implemented, the new instance MUST:

- belong to the current authenticated user (never the source owner if
  different — see cross-user note in §8)
- be `paper_live_data` execution mode
- be `paused` status
- receive a new UUID
- receive fresh `created_at`/`updated_at` timestamps
- receive empty runtime state (no runtime state exists per-instance today;
  this stays true)
- receive empty analytics (no analytics rows exist per-instance today; this
  stays true)
- receive empty history (zero lifecycle/settings/archive history rows — the
  clone's history starts at `V2_INSTANCE_CLONED`, nothing from the source)
- receive no webhook credentials (table stays empty; clone must not become its
  first writer)
- receive no execution jobs (`strategy_execution_jobs` untouched)

## 8. Security Review

- **Ownership**: source instance lookup must filter on `user_id == caller.id`
  the same way every existing mutation does (`instance is None or
  instance.user_id != user.id` → 404). There is no scenario in 2I-1 where a
  user can specify *whose* instance to clone beyond their own — the clone
  endpoint takes only a `source_instance_id` path param and the caller's
  identity comes from the auth dependency, not the request body.
- **Cross-user cloning**: explicitly out of scope for 2I-1. The endpoint must
  not accept a `target_user_id` or any field that could redirect the new row
  to a different owner. If cross-user "share a strategy config" ever becomes a
  product ask, it needs its own dedicated review (it changes the trust
  boundary — one user's configuration becoming another user's instance is a
  fundamentally different operation than self-clone).
- **Rate limiting / duplicate storms**: no generic per-user mutation rate
  limiter exists today (confirmed in §2), and clone is the first mutation
  where rapid repeated calls produce unbounded row growth (pause/resume/
  archive/restore/settings are all bounded — they mutate one existing row;
  clone *creates* a row every call). This is a real gap to close before or
  alongside 2I-1: either a simple per-user max-instances-per-strategy cap
  (reusing the existing `V2_INSTANCE_MAX_LOTS`-style constant pattern) or a
  short per-user cooldown on the clone endpoint specifically. This must be
  decided explicitly in the 2I-1 implementation plan, not left as an
  afterthought — an unthrottled clone button is the one mutation in this
  entire surface that can be hammered into unbounded row growth.
- **Audit**: every clone call that succeeds writes `V2_INSTANCE_CLONED`; every
  call blocked by a gate (flag off, wrong owner, ineligible mode) writes
  nothing, matching existing behavior — 404/403 responses are not audited
  today and clone should not become the exception.
- **Planner interaction**: covered in §6 — born-paused guarantees no race is
  possible.
- **Execution isolation**: clone must go through zero execution-adjacent code.
  It should call the same catalog/version lookup helpers create-born-paused
  uses, insert one row, write one audit event, and return. No calls to
  `route_signal`, PaperBroker, or Dhan client code, verifiable with the same
  spy-fixture pattern used in every prior phase's tests.

## 9. Test Plan (if Candidate A becomes Phase 2I-1)

**Backend tests** (extend the existing lifecycle test module or add a
`test_strategy_instance_clone.py` following the `test_strategy_instance_archive.py`
pattern):

1. Gate matrix reuse — each gate off → blocked, no write.
2. Clone a paused eligible instance → 200, `changed: true`, new row with new
   UUID, `status="paused"`, `execution_mode="paper_live_data"`.
3. Clone an active eligible instance → also succeeds (source status doesn't
   gate clone eligibility, only source *ownership* and *mode* do).
4. Clone an archived instance → succeeds, new instance is paused (not
   archived) — confirms clone never propagates source status.
5. Clone a `real_orders` source → 403, no write.
6. Clone a `disabled`/`error` status source → decide explicitly whether this
   is allowed (leaning yes, since only mode is checked) and pin the answer
   with a test either way.
7. Non-owner source instance → 404, no write, existence not leaked.
8. Unknown/malformed source instance id → 404.
9. Copied-field assertions: new instance's `strategy_id`,
   `strategy_version_id`, `lots`, `side_preference`, `source_type` match the
   source exactly; `instance_label` matches source label plus suffix.
10. Not-copied assertions: new instance's `id`, `created_at`, `updated_at`
    differ from source; `legacy_subscription_id` is null; `config_json`
    does not contain any source `config_json` keys beyond the new
    `cloned_from` breadcrumb; `risk_config == {}`.
11. History assertion: new instance's details/history endpoint returns exactly
    one row (`V2_INSTANCE_CLONED`) — none of the source's history rows leak
    into the clone's history.
12. Webhook credentials table row count is 0 before and after clone.
13. `strategy_signals`/`normalized_option_signals`/`strategy_execution_jobs`
    row counts unchanged before/after clone.
14. route_signal/Dhan/PaperBroker spies: zero calls.
15. Repeated rapid clone calls from the same user — pins whatever
    rate-limit/cap decision is made in §8 (this test does not yet exist
    because the decision doesn't exist yet; it is a required deliverable of
    the 2I-1 implementation, not optional).
16. Planner integration: clone a paused instance → resume the *clone* only →
    plan → source instance's status/behavior is completely unaffected by the
    clone or its resume.

**Frontend tests**: static guard extension (same pattern as
`test_lifecycle_mutation_controls_are_flag_gated_and_scoped`) —
`cloneStrategyInstance` (or equivalent helper name) appears in the approved
helpers list; "Clone" confirm copy present; still no `run-once`/
`process-ready`/`paper-fanout`/`runV2`/`processReady` strings anywhere in the
panel.

**Manual smoke** (fresh isolated DB, same flag set as prior phase smokes):
create instance → edit it away from defaults → clone it → verify clone shows
correct copied fields and paused status → verify clone's history is empty
except `V2_INSTANCE_CLONED` → resume the clone → run planner once → verify only
the clone (not the source) produces a plan → verify signals/normalized/jobs/
credentials counts match runner expectations for one instance, not two → all
flags off → Clone control vanishes.

**Cross-user verification**: instance owned by user B, authenticated as user A
→ clone attempt → 404, no row created for either user.

**Execution isolation verification**: full spy-fixture sweep (route_signal,
PaperBroker, Dhan client) asserting zero calls across every test in this list.

## 10. Explicitly Prohibited In This Phase (2I-0)

- No backend code changes.
- No frontend code changes.
- No database changes or migrations.
- No new routes or APIs.
- No new tests.
- No new docs other than this file.

## 11. Final Recommendation

```
GO / NO-GO: GO for Phase 2I-1 (paper strategy clone/duplicate), design-scoped
            exactly as §6-§9 above.

Recommended Phase 2I-1: Candidate A — clone/duplicate. Reuses the existing
            create-born-paused gate stack, ownership model, and audit shape
            verified across phase2f/2g/2h. Adds exactly one new endpoint,
            one new audit event type, one new frontend control.

Reasons:    Highest product value (removes recreate-from-scratch friction) at
            a risk profile nearly identical to the already-shipped lifecycle
            mutations. No new execution surface, no new secret surface, no
            new audience.

Deferred items: B (read-only analytics/history) can proceed any time, before
            or alongside 2I-1, at even lower risk — sequence at
            implementer's discretion. C (execution controls), D (webhook
            credentials), E (public catalog), F (public webhook) all remain
            review-only. G (live/real_orders activation) remains blocked.

Blocking risks for 2I-1: the clone endpoint is the first mutation in this
            entire surface that creates unbounded rows per call rather than
            mutating a single existing row. A per-user cap or cooldown
            decision (§8) MUST be made explicit in the 2I-1 implementation
            plan before that endpoint ships — this is the one open question
            this review does not resolve on its own.
```
