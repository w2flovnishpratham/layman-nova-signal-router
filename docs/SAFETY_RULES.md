# Safety Rules

## Hard Blocks

- `ENABLE_LIVE_ORDERS=false` blocks real Dhan order placement even when the engine is started.
- Runtime engine stopped blocks new entries.
- Runtime emergency stop blocks new entries immediately.
- Runtime global kill switch blocks new entries.
- The product default kill-switch setting can also block exits if changed in code.
- Runtime max quantity per order rejects oversized alerts.
- Runtime max trades per day rejects additional entries after the daily limit.
- Duplicate signal protection rejects duplicate signal IDs.
- `REQUIRE_MARKET_HOURS=true` rejects alerts outside market hours.

## Credential Safety

- Dhan tokens are submitted only to the backend.
- Dhan tokens are stored only in `runtime_state/credentials.enc.json`.
- `TOKEN_ENCRYPTION_KEY` is required for production.
- API responses and logs must never include a raw Dhan token.
- Frontend localStorage is not used for Dhan credentials.

## Live Orders

When `DHAN_MODE=REAL` and `ENABLE_LIVE_ORDERS=true`, the frontend shows a persistent warning and `/api/engine/start` requires live-order confirmation.

For first production verification:

- Keep `ENABLE_LIVE_ORDERS=false` in backend env.
- Set max quantity per order to `1` in the setup UI.
- Set max trades per day to `1` in the setup UI.
