# Current Supertrend Flow

Phase 0 freezes the current working Supertrend flow. This document maps the
existing code paths so later multi-strategy work can be added behind feature
flags without rewriting the live/paper execution path.

## Flow Map

1. TradingView alert
   - Public webhook URL is built by `tradingview_webhook_url()` in
     `backend/app/routers/setup.py`.
   - `backend/app/main.py` mounts the same webhook router at `/webhook` and
     `/api/webhook`; the current TradingView path is `/webhook/tradingview`.

2. Webhook router
   - `tradingview_webhook()` in `backend/app/routers/webhook.py` reads the raw
     request body, logs a sanitized raw event, parses JSON, authenticates the
     legacy body secret, optionally checks `x-nova-signature`, blocks duplicate
     `signal_id` values, and serializes concurrent signals per strategy.
   - `_route_payload()` in the same file is the handoff point to execution. It
     calls `route_signal(payload)`.

3. Signal parser / validator
   - `parse_webhook_payload()` in `backend/app/services/signal_parser.py`
     dispatches to `_parse_nova_payload()` for current NOVA-style JSON or
     `_parse_pine_multi_leg_payload()` for TradingView Pine `multi_leg_order`
     alerts.
   - Both parser paths produce `NormalizedSignal` from
     `backend/app/schemas/signal.py`.
   - `validate_signal()` in `backend/app/services/signal_validator.py` performs
     the existing normalized signal checks.

4. CE/PE option mapping
   - `_parse_pine_multi_leg_payload()` reads `option_type`, `strike_price`, and
     `expiry_date` from the TradingView leg and preserves them as
     `option_side`, `strike`, and `expiry`.
   - `resolve_option_security_id()` in
     `backend/app/services/instrument_resolver.py` maps option contracts to Dhan
     security IDs when the scrip master is available.
   - `_build_dhan_payload_and_resolution()` in
     `backend/app/services/execution_router.py` creates the final Dhan payload
     and records the security-ID resolution result.

5. Execution router / `route_signal()`
   - `route_signal()` in `backend/app/services/execution_router.py` snapshots
     runtime settings once, records `last_signal`, then routes `ENTRY` to
     `route_entry_signal()` and all other current signals to `route_exit_signal()`.
   - `route_entry_signal()` applies risk checks through `evaluate_entry()` and
     then calls `_place_order()`.
   - `route_exit_signal()` applies `evaluate_exit()`, builds an exit signal from
     the tracked position, and then calls `_place_order()`.
   - `route_reversal_signal()` is the current opposite CE/PE reversal path.
   - `route_signal()` is working and must not be rewritten.

6. Paper/live mode and broker selection
   - `get_engine_mode()` in `backend/app/services/state_store.py` selects
     `paper` or `live`.
   - `_place_order()` in `backend/app/services/execution_router.py` keeps the
     current branch: live mode requires `ENABLE_LIVE_ORDERS=true`, credentials,
     security ID resolution, egress checks, and live idempotency before broker
     submission; paper mode uses shared market-data credentials and the paper
     broker.
   - `get_broker_client()` in `backend/app/services/dhan_client.py` returns
     `PaperBroker` for paper mode and `RealDhanClient` for live mode.
   - `PaperBroker.place_order()` in `backend/app/services/paper_broker.py`
     simulates fills and writes `paper_orders`.
   - `RealDhanClient.place_order()` and `RealDhanClient.place_super_order()` in
     `backend/app/services/dhan_client.py` are the live Dhan write paths and are
     not changed in Phase 0.

7. Order event logging
   - `log_webhook_event()`, `log_order_event()`, `log_audit_event()`, and
     `log_error_event()` in `backend/app/services/audit_logger.py` append
     sanitized JSONL records.
   - Runtime log file names are defined in `LOG_FILES` in
     `backend/app/services/state_store.py`.
   - `scoped_runtime_path()` in `backend/app/services/state_store.py` maps state
     and JSONL logs into per-user runtime directories when a user execution
     context is bound.
   - Order events must continue writing to per-user JSONL logs.

8. Ledger / chart markers
   - `dashboard_portfolio()` in `backend/app/routers/dashboard.py` returns
     `build_portfolio_analytics()` from
     `backend/app/services/portfolio_analytics.py`.
   - `logs()` and `chat_feed()` in `backend/app/routers/dashboard.py` read the
     webhook/order/audit/error JSONL logs through `read_jsonl()`.
   - The frontend dashboard/chart components consume this API state under
     `frontend/src/dashboard/`.

9. UI state
   - `frontend/src/App.tsx` owns view switching with local React state:
     `useState<NovaView>('trading')`.
   - The current frontend does not use a router dependency for switching
     between Trading and Dashboard views; future strategy catalog UI should not
     add one in Phase 0.

## Constraints To Preserve

- Current system assumes one open position per user. `evaluate_entry()` in
  `backend/app/services/risk_manager.py` blocks a new entry when
  `get_open_position()` reports an open position.
- `route_signal()` in `backend/app/services/execution_router.py` is working and
  must not be rewritten.
- Order events must keep writing to per-user JSONL logs through
  `audit_logger.py` and `state_store.scoped_runtime_path()`.
- Legacy webhook secret authentication in
  `backend/app/routers/webhook.py` must continue working.
- Existing fanout scaffolding in `backend/app/services/strategy_fanout.py`,
  `backend/app/workers/strategy_job_worker.py`, and the `StrategySignal` /
  `StrategyExecutionJob` models in `backend/app/db/models.py` should be extended
  later, not replaced.
- Frontend view switching uses `App.tsx` state; no new router dependency should
  be added for Phase 0.
- Idempotency already exists and should be reused later:
  `state_store.has_seen_signal()` / `add_seen_signal()`,
  `webhook_replay_store.claim_webhook_event()`,
  `strategy_fanout.claim_strategy_signal()`, and
  `order_idempotency.claim_live_order_intent()`.
- Alembic is used for schema changes. Future DB changes must be Alembic
  migrations under `backend/alembic/versions/`.
