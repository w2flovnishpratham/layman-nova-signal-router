# Spec — Signals read endpoint (`GET /api/signals`)

Status: **specification only. No backend code, no migration, has been written.**
This is the first genuinely new backend work the redesign requires; it is written
against the real models so it can be reviewed before anything is built.

## Why this is needed

`/app/signals` is currently a truthful placeholder. The data exists, but there is
no endpoint that lists it. Verified today:

- `WebhookEvent` — **owner-scoped** (`user_id` FK) with a composite index
  `ix_webhook_events_user_received (user_id, received_at)`. Columns: `id`,
  `provider`, `event_id`, `user_id`, `raw_body_sha256`, `signature_ok`,
  `replay_status`, `processed_status`, `error`, `metadata` (JSON, attribute
  `event_metadata`), `received_at`, `updated_at`.
- `StrategySignal` — `id`, `strategy_name`, `signal_id`, `status`,
  `result_summary` (JSON), `created_at`, `updated_at`; unique
  `(strategy_name, signal_id)`.

**Key constraint:** `StrategySignal` has **no `user_id`**. It is a global
idempotency record, not a per-owner feed. Therefore the owner-scoped Signals list
must be driven by `WebhookEvent`, optionally enriched from `StrategySignal` via
`signal_id`. Serving `StrategySignal` directly would leak across owners.

## Proposed contract

```
GET /api/signals
    ?limit=50            (1..200, default 50)
    &cursor=<opaque>     (keyset pagination on (received_at, id))
    &status=routed|blocked|duplicate|rejected|all   (default all)
    &since=<ISO8601>     (optional lower bound)
```

Owner scoping is implicit from the session — never accept a `user_id` parameter.

Response:

```json
{
  "items": [
    {
      "id": "uuid",
      "received_at": "2026-07-23T08:07:45.128Z",
      "provider": "tradingview",
      "event_id": "…",                 // from WebhookEvent.event_id
      "signature_ok": true,
      "replay_status": "fresh",
      "processed_status": "routed",
      "error": null,
      "strategy_name": "nova-supertrend",   // enrichment, nullable
      "signal_status": "accepted",          // StrategySignal.status, nullable
      "summary": { }                        // curated subset of result_summary
    }
  ],
  "next_cursor": "…|null",
  "counts": { "received": 148, "routed": 146, "blocked": 2 }
}
```

`counts` is a windowed aggregate (e.g. last 24h) computed with a separate grouped
query — not by loading all rows.

## What must NOT be exposed

- `raw_body` / raw payload. Only `raw_body_sha256` if a fingerprint is needed.
- Webhook secrets, in any form. The mockup shows a revealable secret; that stays
  prohibited.
- Another owner's rows. Every query filters on the session user and uses the
  existing `(user_id, received_at)` index.

## Honest gap: latency

The mockup shows per-signal **ingest → fill latency**. Neither model stores it.
`updated_at - received_at` on `WebhookEvent` is *processing* time, not fill
latency, and conflates later status updates. Options, in preference order:

1. **Omit latency for now** and ship the list without it (recommended first cut).
2. Derive it from `AuditLog` if the ingest and fill entries can be correlated by
   `signal_id` — needs verification that both rows reliably exist.
3. **Only if 1–2 are insufficient:** add a nullable `routed_latency_ms` column to
   `strategy_signals` (or `webhook_events`) written at routing time. This is the
   **only** migration this feature would justify, and it should be a separate,
   reviewed change.

Do not display a latency figure that is actually processing time — that would be
the same class of untruthful metric we removed from the placeholders.

## Implementation notes

- Read-only `GET`. No writes, no side effects, no engine interaction.
- Keyset pagination (`received_at`, `id`) — avoid `OFFSET` on a growing table.
- Reuse the existing session/auth dependency used by the other routers.
- Add to the existing `dashboard`-style router or a new `signals` router; no new
  DB table is required for the basic feed.
- Backend tests: owner isolation (user A cannot see user B), pagination
  stability, status filtering, no raw payload in the response, and an empty-state
  case.

## Frontend follow-up (after the endpoint exists)

Replace the `/app/signals` placeholder with the real list. Until then the
placeholder stays — no fabricated rows.

## Approval gate

Nothing here is implemented. Confirm (a) the contract, (b) whether latency is
omitted or derived, and (c) whether a migration is authorised, before any backend
code is written.
