# Hardening Sprint 02 - Durable Webhook Replay Store + Strategy Risk Caps

Branch: `hardening-sprint-02`, stacked on top of Phase 1.

This sprint does not rebuild webhook crypto. The existing routes already verify
their secrets/signatures and timestamp windows. The gap is durability: replay
and duplicate protection must survive restarts and must be shared across
workers.

## Decisions

- Keep the legacy `/webhook/tradingview` path live because the app still mounts
  it and existing TradingView alerts may use it.
- Harden the legacy path with durable `webhook_events` dedup when a database is
  configured; keep JSON `seen_signals.json` only as the local/dev fallback.
- Keep the shared `/api/webhook/strategy/{strategy_name}` path backed by
  `strategy_signals`, and also record auditable webhook event claims.
- Add per-user and per-user/per-strategy risk controls with all money stored as
  integer paise.

## New Tables

- `webhook_events`: durable provider/event idempotency and body-hash records.
- `webhook_nonces`: durable per-user nonce replay guard.
- `user_risk_controls`: per-user kill switch and default caps.
- `user_strategy_risk_controls`: per-strategy overrides for a user.
- `user_strategy_daily_risk_counters`: durable IST-day order and PnL counters.

## Acceptance Checks

- Same per-user nonce is rejected after clearing the process cache.
- Same webhook event id is not routed twice.
- Same event id with a different raw body is rejected as a conflict.
- Per-user and per-strategy kill switches block only their scope.
- Lot, notional, orders/day, and loss/day caps fail closed before routing.
- Existing paper/live session behavior and chatbot flow are unchanged.
