# Phase 2G-0 — Next Mutation / Feature Design Review (design only, no code)

Grounded against the working tree after Phase 2F-3 (verified from code, not from summary):
lifecycle endpoints live in the debug router behind the 2F-1 gate stack; planner skip constant
`PAUSED_INSTANCE` confirmed; `side_preference` feeds `OptionIntentMapper._side_allowed` (it gates
which sides an instance accepts); normalized draft lots come from the signal payload.

Reminder before anything else: `docs/phase2f3_ui_lifecycle_smoke.md` is still uncommitted — run the
commit/tag commands locally (git via this sandbox remains unusable due to the sync-corrupted view).

---

## 1. Executive Summary

The safest next phase with real value is **Candidate A — lifecycle polish (edit instance label,
lots, side preference), with one hard rule: edits are allowed only while the instance is PAUSED.**
That rule turns three editable fields into the same risk class as pause/resume: a paused instance
is invisible to the planner, so no edit can ever race a planning cycle or change an in-flight paper
job. Candidate B (soft disable) is marginally safer but delivers less; it should be Phase 2G-2 or a
trivially reviewable rider. Candidates C/D/E remain deferred — C is execution-adjacent, D has no
consumer until E exists, E is the highest-risk non-live item on the board. **GO for Phase 2G-1,
scoped to paused-only instance settings edits.**

## 2. Current Verified Baseline

Confirmed in code at review time: three lifecycle endpoints under the debug router (create born
paused / pause / resume), gate stack DEBUG_ENABLED + MULTI_STRATEGY_MODEL +
STRATEGY_INSTANCE_MUTATION_DEBUG + admin/dev + confirm_paper_only; six feature flags all default
OFF; planner skips paused instances (`PAUSED_INSTANCE`) and real_orders instances
(`REAL_ORDERS_NOT_SUPPORTED_YET`); no v2 wiring in main/workers/webhooks; 2F-3 smoke evidence:
signals/normalized/jobs/credentials all 0 after full UI lifecycle, 705 tests green, three build
variants green.

## 3. Candidate Comparison Table

| Candidate | Safety | Usefulness | Complexity (lower=simpler) | Regression risk | Live-trading risk |
|---|---|---|---|---|---|
| **A** Lifecycle polish (paused-only edits) | 9 | 8 | 3 | 2 | 1 |
| **B** Soft disable (paused → disabled, re-enable → paused) | 9 | 5 | 2 | 2 | 1 |
| **C** UI debug paper run button | 4 | 7 | 3 | 5 | 4 |
| **D** Webhook credential generation | 6 | 3 (now) | 6 | 3 | 2 |
| **E** Public custom TradingView webhook route | 4 | 9 (later) | 8 | 6 | 5 |
| **F** User-facing catalog polish | 7 | 6 | 4 | 3 | 1 |
| **G** Live/real_orders activation | 1 | — | — | — | 10 |

Notes per candidate:
- **A**: same single table, same gates, same audit shape as 2F-1. The only field with behavioral
  reach is `side_preference` (drives the intent mapper's CE/PE filter) and `lots` — both only ever
  consulted at future *planning* time, and the paused-only rule guarantees no plan can observe a
  half-edited instance.
- **B**: one status edge. Must define the undo (disabled → paused, never disabled → active) or it
  becomes a trap state. Low value until instance lists get cluttered.
- **C**: the runner path ends in `route_signal` via the paper bridge. One-click execution from a
  browser is a boundary crossing that deserves its own phase with its own review, after lifecycle
  mutations have soaked in staging.
- **D**: display-once secret policy, hashing, rotation, revocation — a real security surface with
  zero consumers until E. Building it now means maintaining an unused secret system.
- **E**: public unauthenticated-JWT route + replay + rate limit + per-key validation. Only after D,
  and only after a dedicated webhook-security review (2F-0 W-series carried forward).
- **F**: safe but it changes the *audience* — the read-only endpoints are currently admin/dev-gated
  (`_require_internal_strategy_status_user`). Widening to all users is an access-control decision,
  not a styling task; it deserves its own small review when product wants the catalog public.

## 4. Recommended Next Phase

**Phase 2G-1 = paused-only instance settings edit:**
- Editable fields, exactly three: `instance_label`, `lots`, `side_preference`.
- **Precondition: `status == "paused"`.** An `active` instance returns 409 with a clear message
  ("Pause the instance before editing.") — this forces the same two-step discipline as born-paused
  create and eliminates every planner/edit race by construction.
- Eligible modes: `paper_live_data` and `signal_only` only; `real_orders` → 403 (same as 2F-1).
- Owner-only (non-owner → 404); idempotent no-op edits return `changed: false`.
- No status changes through this endpoint (pause/resume stay separate endpoints).

Why not B first: B is a status-vocabulary extension with little user value; A unlocks actually
iterating on paper configurations (relabel, resize, re-side) without the create-new-instance
workaround that would otherwise clutter tables and dilute audit history.

## 5. Candidates to Defer

- **B** → Phase 2G-2 (tiny; can reuse everything from this design; requires the disabled→paused
  undo edge and planner-skip test for `disabled`).
- **C** → earliest after a multi-day staging soak of lifecycle + settings mutations; needs its own
  design review (execution boundary).
- **D** → only when E is scheduled; design D+E as one security unit.
- **E** → after D; dedicated webhook-security review required.
- **F** → separate small review focused on the auth-gate change, not UI.
- **G** → remains blocked (planner skip + adapter block + DB partial-unique all stay).

## 6. Backend API Design (for 2G-1 — not implemented in this phase)

One endpoint, debug router (inherits router-level DEBUG_ENABLED → 404 when off):

```
POST /api/debug/v2/instances/{instance_id}/settings
body: {
  "instance_label"?: string,     # optional; sanitized, 120 cap (reuse _sanitized_instance_label)
  "lots"?: int,                  # optional; ge=1 le=20 (same cap as create)
  "side_preference"?: "BOTH"|"CE"|"PE",   # optional; Literal allowlist
  "confirm_paper_only": true     # required, same pattern
}
```

Rules: `extra="forbid"` (rejects smuggled execution_mode/status/user_id/secret fields with 422);
at least one editable field must be present (400 otherwise); POST not PATCH to stay consistent
with the existing lifecycle endpoints and the static guards that forbid PATCH in panel code paths.
Decision tree identical to 2F-1 transitions plus the paused-only precondition:
owner? → mode eligible? → **paused?** → apply only provided fields → `changed` reflects whether
anything actually differed. Response returns the same safe public projection.

## 7. Frontend UI Design (for 2G-1)

Inside the existing `StrategyCatalogStatusPanel` instances table only: an "Edit" control per
paused, lifecycle-eligible instance (hidden for active ones — pausing first is the workflow, with
a hint "Pause to edit"). Expands to an inline row editor (label text input, lots stepper 1–20,
side select BOTH/CE/PE) with the same two-step confirm pattern and the same copy: "Paper only. No
orders will be placed." Refetch after save, no optimistic updates. Renders only when the existing
`VITE_ENABLE_STRATEGY_CATALOG_STATUS` AND `VITE_ENABLE_STRATEGY_INSTANCE_MUTATION` are both true —
**no new frontend flag needed** (Q9: no; it is the same mutation surface, same audience, same
risk class as 2F-1).

## 8. Feature Flags and Access Gates (Q6–Q9)

Reuse the 2F-1 stack unchanged: DEBUG_ENABLED (Q7: yes) + MULTI_STRATEGY_MODEL +
STRATEGY_INSTANCE_MUTATION_DEBUG (Q8: **no new backend flag** — settings edit is the same
mutation class the flag was named for) + admin/dev internal user + `confirm_paper_only: true` +
existing frontend flag pair (Q9: no new one). Adding a seventh flag for this would be flag
sprawl without a safety gain; the meaningful boundary (data-only vs execution) already has a
dedicated switch.

## 9. DB Write Design (Q10–Q16)

- Q10: UPDATE `user_strategy_instances` — columns `instance_label`, `lots`, `side_preference`,
  `updated_at`, `config_json.last_mutation`. Nothing else, no inserts.
- Q11 strategy_signals: **No.** Q12 execution jobs: **No.** Q13 runtime state files: **No.**
- Q14 route_signal: **No** (and the existing spy-fixture pattern will pin it).
- Q15 Dhan/PaperBroker: **No.**
- Q16 legacy Supertrend: **No** — no shared code paths; `strategy_subscriptions` untouched; the
  legacy engine never reads `user_strategy_instances`.
- Constraints protecting it: the paused-only rule (server-side), Literal/range validation,
  ownership check, and the untouched partial-unique real_orders index (edits never touch
  status/execution_mode, so it cannot even be implicated).
- Behavioral note to encode in tests: `side_preference` changes what the intent mapper allows and
  `lots` changes future drafts — both must be observed by the planner ONLY after resume.

## 10. Secret / Logging / Audit Design (Q17, Q18)

Q17: **No secrets involved** — no credential fields are readable or writable through this surface.
Q18: one `V2_INSTANCE_SETTINGS_EDITED` audit event per actual change with actor_user_id,
instance_id, strategy_code, `changed_fields` (names + old→new values for label/lots/side — these
are not sensitive), execution_mode, flag snapshot. Never request bodies. `last_mutation`
breadcrumb updated as in 2F-1.

## 11. Rollback / Undo Design

Field edits are their own undo (edit back); the audit event records old values so any edit is
reconstructable. Operational rollback: any gate off → surface gone. No migration required
(columns exist), so nothing to downgrade.

## 12. Test Plan (Q19)

Extend `test_strategy_instance_lifecycle_mutation.py`:
1. Gate matrix reuse (each gate off → blocked, no write).
2. **Active instance edit → 409, no write** (the core new rule).
3. Paused paper instance: each field edits correctly; `changed:true`; audit records old→new.
4. No-op edit (same values) → `changed:false`, no audit "changed" event.
5. Empty body (only confirm) → 400.
6. Smuggled `status`/`execution_mode`/`user_id`/`secret` → 422; lots 0/21 → 422; side "LIVE" → 422.
7. real_orders instance → 403; signal_only paused → allowed; error/disabled status → 409.
8. Non-owner → 404, no write.
9. Planner integration: edit side to "PE" while paused → resume → plan → mapper skips a BULLISH/CE
   signal for that instance (uses existing mapper behavior); lots edit visible in next draft only
   after resume.
10. route_signal/Dhan/PaperBroker spies: zero calls.
11. Signals/jobs/credentials tables remain empty after edits.
12. Legacy Supertrend characterization with all mutation flags ON — unchanged.
13. Static guards: edit control only in catalog panel, no new endpoints referenced beyond
    `/api/debug/v2/instances/`, PATCH absent, confirmation copy present.

## 13. Manual Smoke Plan (Q20)

Fresh isolated DB, flags on (same set as 2F-3): create instance (paused) → edit label/lots/side →
verify panel reflects new values → try edit while resumed → expect the 409 hint → pause → edit →
resume → run planner once (fanout flags on) → verify plan uses new side/lots and still
paper-only, jobs_created per runner contract → counts: signals/normalized/jobs/credentials as per
runner expectations, credentials always 0 → all flags off → controls vanish, read-only panels
intact → legacy Supertrend view opens normally.

## 14. Red Flags

None for the recommended scope. Watch items carried forward: W1 keep `V2_PAPER_RUNNER_DEBUG` off
outside smoke windows; the 2F-3 smoke doc and this review need the local commit/tag (sandbox git
still unusable); `docs/internal_readonly_surfaces.md` still describes the pre-2F-1 "no mutation
controls" contract and should be refreshed in the same commit.

## 15. Final GO/NO-GO

**GO for Phase 2G-1: paused-only paper instance settings edit (label, lots, side preference) —
one endpoint, same 2F-1 gate stack, no new flags, no status changes, no execution, no webhook, no
live.**

Explicit answers to the header questions: safest next = A (with paused-only rule); best value
without execution risk = A; must NOT be next = **C** (one-click execution) and E (public webhook)
— and G stays blocked entirely.
