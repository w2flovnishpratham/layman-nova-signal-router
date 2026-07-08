# Phase 2F-0 — Multi-Strategy Mutation Design Review (read-only, no code)

Reviewed at HEAD `11fd276` (phase2e8) + working tree. All claims verified from code.

---

## 1. Executive Summary

The safest first mutation is a **paper-instance lifecycle triplet: create (born paused), pause,
resume** — Candidates A+B fused into one phase, with creation defaulting to `paused` so that a
freshly created instance is inert even against the already-existing debug fanout trigger. It
touches exactly one DB table, zero runtime state files, zero secrets, zero legacy code paths, and
sits behind five independent gates. Candidate C (UI button for the debug paper runner) must NOT be
first: its backend halves (`POST /api/debug/v2/paper-fanout/run-once|process-ready`) already exist
and call `route_signal` via the paper bridge — adding a button lowers the friction to execution
before mutation discipline is proven. Candidate D introduces a secret-handling surface with zero
utility while `CUSTOM_WEBHOOKS` is off. **GO for Phase 2F-1, scoped to the lifecycle triplet only.**

## 2. Current Safe Baseline (verified)

- **Flags (5)**: `MULTI_STRATEGY_MODEL`, `MULTI_STRATEGY_FANOUT`, `V2_PAPER_RUNNER_DEBUG`,
  `CUSTOM_WEBHOOKS`, `STRATEGY_CATALOG_UI` — all default OFF, fail-closed parser.
- **Debug router**: router-level `Depends(require_debug_enabled)` (`settings.DEBUG_ENABLED`);
  v2 endpoints additionally require `MULTI_STRATEGY_FANOUT` + `V2_PAPER_RUNNER_DEBUG` +
  `confirm_paper_only: true` + `_reject_non_paper_execution_marker`.
- **Read-only surfaces**: `GET /api/strategies/catalog|instances` (auth-gated via
  `_require_internal_strategy_status_user`, user-scoped instances query); three GET debug
  inspector endpoints; two frontend panels behind `VITE_ENABLE_V2_PAPER_STATUS` /
  `VITE_ENABLE_STRATEGY_CATALOG_STATUS` (off → hidden menu, disabled state, no API calls).
- **2D-6 fixes confirmed in code**: instance detection requires an internal signal
  (`secret == ""`) plus `v2_internal: true` trust marker — legacy TV alerts (non-empty secret) can
  never activate instance routing; planner skips `real_orders` instances
  (`REAL_ORDERS_NOT_SUPPORTED_YET`); DB partial-unique
  `uq_user_strategy_instances_active_real_orders_per_user` exists.
- **Execution reachability today**: only via debug POSTs; runner filters plans and claims to
  `paper_live_data` only, with per-instance job locking. No worker/startup/scheduler wiring
  (re-verified: v2 entry points referenced nowhere in `main.py`/workers/webhook routers).
- **Instance statuses**: `active | paused | error | disabled`; planner skips non-active.

## 3. Candidate Mutation Comparison

| Candidate | Safety | Usefulness | Complexity (lower=simpler) | Regression risk | Live-trading risk |
|---|---|---|---|---|---|
| **A** Create paper instance (born paused) | 9 | 8 | 3 | 2 | 1 |
| **B** Pause/resume paper instance | 9 | 7 | 2 | 2 | 1 |
| **C** UI trigger for debug paper runner | 5 | 7 | 3 | 4 | 4 |
| **D** Generate webhook credentials | 6 | 3 (now) | 6 | 3 | 2 |
| **E** Subscribe to Nova-owned fanout | 6 | 6 | 5 | 5 | 4 |
| **F** Live/real_orders activation | 1 | — | — | — | 10 |

Notes: A and B are the only candidates whose worst-case outcome is "an extra inert DB row" /
"a flipped status string". C is execution-adjacent by definition (its handler path ends in
`route_signal`). D's cost is a display-once secret policy with nothing to consume the credential.
E writes into the legacy-adjacent subscription concept and becomes an execution feeder later.
F stays blocked by three independent layers (planner skip, adapter `V2_LIVE_BLOCKED`, DB
partial-unique) and is out of scope.

## 4. Recommended First Mutation

**Phase 2F-1 = paper instance lifecycle only:**
1. Create instance from an enabled catalog strategy — server-side hardcoded
   `execution_mode="paper_live_data"`, `status="paused"`, `source_type="nova_owned_tradingview"`,
   owner = authenticated user (never from request body).
2. Pause: `active → paused`.
3. Resume: `paused → active`.
Restrictions: pause/resume allowed **only** on instances whose `execution_mode` is
`paper_live_data` or `signal_only`; `real_orders` instances (backfilled Supertrend) return 403.
No delete in 2F-1 — `disabled` (soft) can be added later as the destructive-ish operation.

Why create-paused matters: `run-once` fanout already exists behind debug flags. If creation
produced `active` instances, the mutation phase would silently widen the blast radius of an
existing execution trigger. Born-paused means resume is a distinct, auditable second decision —
the same two-step discipline the live path uses.

## 5. Mutations to Defer

- **C** — until lifecycle mutations have run clean in staging for a full scenario matrix; the
  backend triggers already exist for terminal/HTTP use, which is enough for 2F-x testing.
- **D** — until `CUSTOM_WEBHOOKS` phase starts; bundle key generation + hashing + display-once +
  rotation + revocation as one reviewed unit.
- **E** — after lifecycle + C are proven; it feeds future fanout execution.
- **F** — remains blocked; not revisited before the dedicated live-readiness phase.

## 6. Backend API Design (for 2F-1, not implemented now)

Namespace: **debug router** (inherits router-level `DEBUG_ENABLED` dependency — production builds
without debug cannot even route to it), not `strategies.py`.

```
POST /api/debug/v2/instances                     body: {catalog_code, instance_label?, lots?, confirm_paper_only: true}
POST /api/debug/v2/instances/{id}/pause          body: {confirm_paper_only: true}
POST /api/debug/v2/instances/{id}/resume         body: {confirm_paper_only: true}
```

Handler rules: reject any body containing `execution_mode`/`mode`/`live`/`real` markers (reuse
`_reject_non_paper_execution_marker` pattern); validate catalog row exists, `status=enabled`, has
an active version; instance ownership check on pause/resume (`user_id == current user`); status
transitions restricted to the two legal edges (`active<->paused`); idempotent (pausing a paused
instance = no-op success with `changed: false`). Responses return the same public projection the
read-only endpoints already use — no new fields, no secrets.

## 7. Frontend UI Design

Buttons inside the existing `StrategyCatalogStatusPanel` only (no new panel): "Create paper
instance" on catalog rows, "Pause"/"Resume" on the user's own paper instances. Confirmation modal
naming the strategy and stating "paper only - no orders will be placed". Controls render only when
**both** `VITE_ENABLE_STRATEGY_CATALOG_STATUS=true` and new `VITE_ENABLE_STRATEGY_INSTANCE_MUTATION=true`.
Flag off → panel stays exactly as today (read-only), no dead buttons. No optimistic updates —
re-fetch after mutation so the panel always reflects DB truth.

## 8. Feature Flags and Access Gates (Q5–Q8)

Five stacked gates, each independently sufficient to stop the mutation:

| Gate | Answer |
|---|---|
| `DEBUG_ENABLED` (existing, router-level) | **Yes, required** (Q6: yes) |
| New backend flag `STRATEGY_INSTANCE_MUTATION_DEBUG` in `FEATURE_FLAGS`, default OFF | **Yes** (Q7: yes) |
| `MULTI_STRATEGY_MODEL` also required | Yes — mutation writes the multistrategy model |
| New frontend build flag `VITE_ENABLE_STRATEGY_INSTANCE_MUTATION`, default off | **Yes** (Q8: yes) |
| `confirm_paper_only: true` request-body confirmation | Yes (existing pattern) |

Deliberately NOT required: `MULTI_STRATEGY_FANOUT` / `V2_PAPER_RUNNER_DEBUG` — those stay scoped
to execution triggers, keeping "can mutate rows" and "can execute paper jobs" as separate switches.

## 9. DB Write Design (Q9, Q10)

Writes: `INSERT user_strategy_instances` (create) and `UPDATE user_strategy_instances.status`
(pause/resume). **Nothing else** — no signals, no jobs, no credentials, no legacy
`strategy_subscriptions`, and zero runtime state files (`open_position*.json` untouched; verified
no state_store call is needed anywhere in this design).
Protecting constraints (existing): FKs to catalog/version; `uq_user_strategy_instances_legacy_subscription`;
`uq_user_strategy_instances_active_real_orders_per_user` (last line against Q11 even if the
hardcode failed); user/status indexes. Add in 2F-1: server-side allowlist validation of `status`
and `execution_mode` strings (columns are strings, not DB enums — the allowlist is the guard).

**Q11 (accidental real_orders): No** — mode is hardcoded server-side, marker-rejected from input,
and the partial-unique constraint plus planner skip plus adapter block back it up.
**Q12 (accidental fanout): No** — creation is data-only; execution still requires the separately
flagged debug POST, and a created instance is paused, which the planner skips.
**Q13 (legacy Supertrend): No** — no shared code paths; legacy webhook/parser/state untouched;
backfilled real_orders instances are excluded from pause/resume by rule.
**Q14 (webhook secrets): No** — Candidate D deferred; no credential rows involved.

## 10. Secret / Logging / Audit Design (Q16)

No secrets exist in this mutation's data. Audit: `log_audit_event` on every mutation —
`V2_INSTANCE_CREATED` / `V2_INSTANCE_PAUSED` / `V2_INSTANCE_RESUMED` with instance_id,
strategy code, actor user_id, prior→new status, and flag snapshot; metadata only, never request
bodies. Mutations also append to the instance's `config_json` a small
`{"last_mutation": {...}}` breadcrumb for the panel. Responses reuse the existing public
projections (already secret-free).

## 11. Rollback / Undo Design (Q15)

- Pause is the undo of resume and vice versa (pure status flip, idempotent).
- Undo of create = pause (row stays, inert); hard delete deliberately excluded from 2F-1;
  `disabled` soft-state reserved for a later phase.
- Operational rollback: turn off any one of the five gates → surface disappears; existing rows are
  plain data the planner ignores unless active+paper+fanout-flagged.
- DB rollback: rows are additive; no migration needed for 2F-1 (schema already supports it) —
  therefore nothing to downgrade.

## 12. Test Plan (Q17 — required before coding)

1. **Gate matrix**: each of {DEBUG_ENABLED off, STRATEGY_INSTANCE_MUTATION_DEBUG off,
   MULTI_STRATEGY_MODEL off, confirm_paper_only false/missing} → 403/404, no DB write.
2. Create: defaults are `paused` + `paper_live_data` + correct owner; client-supplied
   `execution_mode`/`user_id`/`status` rejected or ignored (test both header and metadata smuggling).
3. Pause/resume: legal edges only; idempotency; ownership enforced (user A cannot mutate user B's
   instance — 404 not 403 to avoid existence leak); real_orders instance → 403.
4. Planner interaction: paused created instance is skipped by `plan_strategy_fanout_v2`; after
   resume (+fanout flags on) it is planned — and still only `paper_live_data` jobs result.
5. **No-execution proof**: spy/monkeypatch `route_signal` — zero calls across all mutation tests.
6. **Legacy characterization**: run the existing Supertrend/position characterization suites with
   all new flags ON — byte-identical behavior.
7. Constraint test: attempted second active real_orders row still violates the partial unique
   (regression guard for the hardcode).
8. Audit: each mutation writes exactly one audit event with expected metadata, no bodies.
9. Frontend: flag off → no controls rendered and no mutation API in the bundle path; flag on →
   confirm modal required; response errors render sanitized messages.

## 13. Manual Smoke Plan (Q18)

Staging only, flags on: create instance → appears `paused` in Catalog Status panel → debug
`run-once` → planner reports instance skipped (paused) → resume → `run-once` → plan + paper job →
`process-ready` → paper position visible in V2 Paper Status → pause → next `run-once` skips again.
Then flip each gate off one at a time and confirm the mutation surface vanishes/403s while
read-only panels keep working. Finally: full legacy Supertrend paper ENTRY/EXIT smoke to confirm
zero interference.

## 14. Red Flags

None blocking. Three watch items:
- **W1**: Debug execution POSTs (`run-once`, `process-ready`) already exist — the mutation phase
  increases the value of `V2_PAPER_RUNNER_DEBUG` hygiene; keep it OFF except during smoke windows.
- **W2**: `_require_internal_strategy_status_user` currently gates read-only viewing; before 2F-1
  ships, confirm its membership is the intended *mutation* audience too (viewing ≠ mutating).
- **W3**: `status`/`execution_mode` are plain string columns; the server-side allowlist (§9) is
  mandatory, not optional.

## 15. Final GO/NO-GO for Implementation Phase 2F-1

**GO for Phase 2F-1: paper strategy instance lifecycle only — create (born paused), pause,
resume — behind DEBUG_ENABLED + STRATEGY_INSTANCE_MUTATION_DEBUG + MULTI_STRATEGY_MODEL +
VITE_ENABLE_STRATEGY_INSTANCE_MUTATION + confirm_paper_only. No execution, no webhook, no live,
no worker, no delete.**

Everything else (C, D, E) is explicitly deferred; F remains blocked by design.
