# Phase 2D-0 Position State Audit

Scope: audit and compatibility plan only. No production behavior changed in this phase.

## 1. Current single-position flow

`backend/app/services/state_store.py` owns the runtime position files.

- `open_position.json` is the legacy live/current live position file.
- `paper_position.json` is the paper-mode position file.
- `get_open_position()` dispatches by `get_engine_mode()`: paper mode reads `paper_position.json`; every other current/fallback mode reads `open_position.json`.
- `set_open_position(position)` writes to the same current-mode target.
- `clear_open_position()` clears only the current-mode target.
- `set_engine_mode()` refuses to switch modes if either the live or paper position file has `has_open_position=true`.

This means the system currently supports one tracked position for the active user in the selected engine mode. The rest of the backend assumes that current-mode object is the source of truth for entry blocking, exits, reversals, monitor updates, dashboard active trade display, and startup/broker reconciliation.

The order ledger is separate from the position file. `audit_logger.log_order_event()` appends per-user JSONL records to `order_events.jsonl`, always adding `timestamp` and current `mode`. Portfolio analytics and NIFTY chart markers reconstruct history from this ledger.

## 2. Open-position readers found

Direct `get_open_position()` readers:

- `state_store.get_open_position()` reads the current-mode tracked position.
- `state_store.set_engine_mode()` reads both explicit live and paper positions to block mode switches while either file is open.
- `risk_manager.evaluate_entry()` blocks a second legacy entry when a current-mode open position exists.
- `risk_manager.evaluate_reversal_entry()` requires a current-mode open position and opposite option side.
- `risk_manager.evaluate_exit()` requires a current-mode open position and uses its quantity.
- `execution_router._reconcile_tracked_position_before_entry()` checks local position before live entry and may reconcile it against Dhan.
- `execution_router._is_opposite_option_entry()` checks the current tracked position when a caller does not pass one.
- `execution_router.route_reversal_signal()` reads the current position to build the exit leg before opposite entry.
- `execution_router._route_side_filter_exit_only()` reads the current position to build an exit-only signal.
- `execution_router.route_exit_signal()` reads the current position for normal exits.
- `option_position_monitor.monitor_once()` reads the current position before PnL update, LTP sync, and server-side exit decisions.
- `option_position_monitor._unlock_exit_attempt()` rereads the current position before clearing server-exit-in-progress state.
- `position_reconciler.get_reconciled_open_position()` reads the current position for dashboard/positions endpoint broker reconciliation.
- `startup_reconciler.reconcile_open_position_on_startup()` reads the current position and compares it with Dhan positions/orders.
- `market_snapshot.build_nifty_snapshot()` reads the current position for active option LTP, active side, and sparkline display.
- `routers/orders.manual_exit()` reads the current position to build a manual exit signal.
- `routers/orders.manual_reverse()` reads the current position to build an opposite-side manual entry signal.
- `routers/orders.update_active_position_quantity()` reads the current position before paper quantity adjustment.
- `routers/orders.update_active_position_exit_levels()` reads the current position before manual SL/TP adjustment.
- `routers/control.fresh_start()` reads the current position and refuses destructive reset while open.
- `routers/control.panic_exit()` reads the current position to build a panic-exit signal.
- `routers/engine` reconfiguration reads the current position and blocks with an open position.
- `api/session` reads the current position for chat/session active-trade state.
- `api/ws` reads the current position for active-trade websocket updates, tracked-position exits, reverse flows, and S/R suggestion application.

Indirect readers:

- `routers/dashboard.dashboard_summary()` calls `position_reconciler.get_reconciled_open_position()`.
- `routers/positions.positions()` calls `position_reconciler.get_reconciled_open_position()`.
- `paper_broker.get_positions_snapshot()` reads `get_paper_position()` directly to emulate Dhan positions in paper mode.

## 3. Open-position writers found

Direct current-mode writers:

- `state_store.set_open_position(data)` writes the current-mode position.
- `state_store.clear_open_position()` clears the current-mode position.
- `state_store.reset_to_waiting_entry()` clears the current-mode position and resets app state.
- `execution_router._reconcile_tracked_position_before_entry()` clears stale live local position when Dhan is flat before entry.
- `execution_router.route_entry_signal()` creates `_entry_position(...)` and writes it after a successful entry.
- `execution_router.route_reversal_signal()` clears the old position after successful reversal exit, then writes the new entry position after successful opposite entry.
- `execution_router.route_exit_signal()` clears the position after full exit, or `_apply_partial_exit_fill()` writes the remaining quantity after partial exit.
- `option_position_monitor._with_live_pnl()` writes live PnL snapshots.
- `option_position_monitor._sync_entry_fill_if_needed()` writes entry fill price, fill source, Super Order post-fill levels, or waiting-fill status.
- `option_position_monitor._mark_exit_attempt()` writes server-exit-in-progress state.
- `option_position_monitor._unlock_exit_attempt()` writes server-exit result state.
- `option_position_monitor.monitor_once()` writes market-closed, websocket-waiting, LTP-error, and normal PnL snapshots.
- `position_reconciler._with_sync(..., persist=True)` writes broker sync metadata.
- `position_reconciler.get_reconciled_open_position()` writes a cleared default position when Dhan confirms flat.
- `startup_reconciler.reconcile_open_position_on_startup()` writes restart sync metadata, clears stale local position, or writes verified restart metadata.
- `routers/orders.update_active_position_quantity()` writes paper quantity changes.
- `routers/orders.update_active_position_exit_levels()` writes active manual SL/TP levels.
- `api/ws` writes accepted S/R suggestion levels into the current position.

Explicit live/paper writers:

- `state_store.set_live_open_position()` and `clear_live_open_position()` write `open_position.json`.
- `state_store.set_paper_position()` and `clear_paper_position()` write `paper_position.json`.
- `routers/setup` clears paper position during paper portfolio reset/setup paths.
- `state_store.fresh_runtime_start()` clears paper position and resets paper portfolio.

## 4. JSONL ledger and marker events

`audit_logger.log_order_event(event)` writes to the per-user `order` JSONL log and prepends:

- `timestamp`
- `mode`
- sanitized event fields

Current ledger producers:

- `execution_router._blocked()` writes `phase="blocked"` records.
- `execution_router._place_order()` writes `phase="before_request"` and `phase="after_response"` records.
- `_place_order()` may write a second `phase="after_response"` record with `fill_poll_update=true`; portfolio analytics dedupes by `order_id` and prefers the record with `avg_price`.
- `_place_order()` may write `phase="super_order_level_retry"`.
- `execution_router` also writes named events:
  - `DHAN_SUPER_ORDER_POST_FILL_LEVEL_SYNC`
  - `DHAN_SUPER_ORDER_EXIT_LEGS_CANCELLED_AFTER_MANUAL_EXIT`
  - `DHAN_SUPER_ORDER_MANUAL_LEVEL_UPDATE`
  - `DHAN_ENTRY_PREFLIGHT`
  - `LOCAL_OPEN_POSITION_RECONCILED`
- `paper_broker.PaperBroker._place()` writes `PAPER_ORDER_FILLED` and also records paper orders in the paper-order log.
- `option_position_monitor` writes:
  - `SERVER_SIDE_OPTION_EXIT_CONFIRMATION_FAILED`
  - `SERVER_SIDE_OPTION_EXIT_CONFIRMATION_REJECTED`
  - `ENTRY_FILL_PRICE_SYNCED`
  - `SERVER_SIDE_OPTION_EXIT_TRIGGERED`
  - `OPTION_LTP_FETCH_FAILED`
- `position_reconciler` writes:
  - `OPEN_POSITION_DHAN_SYNC`
  - `LOCAL_OPEN_POSITION_CLEARED_AFTER_DHAN_SYNC`
- `startup_reconciler` writes `STARTUP_OPEN_POSITION_VERIFIED` to the order log and writes stale-clear details to the audit log.
- Debug tooling can append order events for diagnostics.

Current ledger consumers:

- `portfolio_analytics.build_portfolio_analytics()` reads `read_jsonl("order")`, filters filled live `after_response` legs, excludes paper, and pairs one ENTRY with the next EXIT.
- `routers/market.nifty_markers()` reads `read_jsonl("order")`, filters filled legs by `mode`, dedupes by `order_id`, and emits today's chart markers.
- `routers/orders.order_history()`, `routers/dashboard` log feeds, `routers/debug`, chat/session surfaces, and frontend components read or display the same per-user log stream.

Existing records do not require `instance_id`. Any future ledger enrichment must be additive.

## 5. Path classification

Manual orders:

- Manual entry is built in `routers/orders.manual_entry()` and sent through `route_signal()`.
- Manual exit, manual reverse, panic exit, quantity update, and manual SL/TP all use the single current open position.
- Manual live order placement has entitlement and idempotency checks before `route_signal()`.

TradingView webhook orders:

- Normalized TradingView/NOVA signals flow through `route_signal()`, `route_entry_signal()`, `route_exit_signal()`, and `route_reversal_signal()`.
- Risk checks use the single current open position to block second entries or require exits/reversals.
- Current Supertrend behavior depends on this global single-position block.

Paper-only paths:

- Paper mode position storage uses `paper_position.json`.
- `PaperBroker` simulates fills and writes `PAPER_ORDER_FILLED`.
- Paper portfolio reset clears paper position.
- Manual paper quantity changes are local state mutations.
- Phase 2C-2's v2 paper bridge currently changes global engine mode for manual/test-only execution; background worker wiring must not use that until mode isolation is designed.

Live paths:

- Live mode position storage uses `open_position.json`.
- Live entry preflight checks Dhan positions/orders before entry.
- Live Super Order level sync, manual Super Order SL/TP update, and exit-leg cancellation use the tracked single live position.
- Live MVP should continue allowing only one active live strategy per user until per-instance live broker safety is explicitly designed.

Dashboard/analytics only:

- Dashboard summary and `/positions` reconcile and return one open position.
- Portfolio analytics reconstructs live closed trades from the JSONL order ledger and returns at most one inferred open ledger entry.
- NIFTY chart markers render today-only filled legs from the JSONL order ledger.
- Frontend active-position cards, manual order controls, portfolio dashboard, and chart markers currently expect one active position or one global marker stream.

## 6. Risk of changing each area

- `state_store.get_open_position/set_open_position/clear_open_position`: highest risk. All runtime behavior fans out from these functions. Phase 2D-1 must preserve `instance_id=None` exactly.
- `risk_manager.evaluate_entry/evaluate_reversal_entry/evaluate_exit`: high risk. Incorrect scoping can allow duplicate live exposure or block valid legacy exits.
- `execution_router.route_entry_signal/route_exit_signal/route_reversal_signal`: high risk. Incorrect scoping can write, clear, or reverse the wrong position and can place broker orders.
- `option_position_monitor.monitor_once`: high risk. It can route server-side exits and persist PnL; per-instance support needs explicit iteration and cooldown scoping.
- `position_reconciler` and `startup_reconciler`: high risk for live state. Incorrect scoping can clear valid positions or hide broker exposure.
- Manual order routers and websocket commands: medium/high risk. They are user-driven but currently target the one active position.
- `portfolio_analytics._pair_round_trips`: medium risk. It assumes a depth-one global stack, so per-instance ledgers need grouping by `instance_id` before pairing.
- `routers/market.nifty_markers`: medium risk. It can continue showing a global stream, but future filtering/grouping by instance needs additive fields.
- Dashboard/frontend active-position displays: medium risk. They are display-only, but UI contracts currently represent a single open position.
- `paper_broker`: medium risk. It snapshots one paper position and should not be reused as-is for multiple virtual positions without instance-aware broker simulation.

## 7. Recommended Phase 2D-1 storage design

Use Option A for the MVP:

- Keep `open_position.json` exactly as-is.
- Keep `paper_position.json` exactly as-is.
- Add a future `open_positions_by_instance.json` for v2 positions only.
- Keep `instance_id=None` mapped to current legacy behavior.
- Use `instance_id=<uuid>` only for v2 per-instance position state.

Why Option A:

- It preserves the legacy Supertrend/manual/live contract without migrating existing runtime files.
- It makes rollback simple because legacy readers and writers can remain on the old files.
- It avoids changing frontend contracts during the storage step.
- It lets v2 paper strategies hold separate virtual positions without weakening the current one-live-position-per-user safety rule.
- It defers a DB positions table until after runtime behavior, monitor ownership, and analytics grouping are proven.

Suggested future file shape:

```json
{
  "version": 1,
  "positions": {
    "<instance_id>": {
      "has_open_position": true,
      "strategy_code": "ST_2025_M1",
      "strategy_version_id": 123,
      "execution_mode": "paper_live_data",
      "trading_symbol": "NIFTY ...",
      "qty": 75
    }
  }
}
```

## 8. Recommended Phase 2D-1 function signatures

Do not change callers first. Add backward-compatible signatures and keep default behavior identical:

```python
def get_open_position(instance_id: str | None = None) -> dict[str, Any]: ...
def set_open_position(data: dict[str, Any], instance_id: str | None = None) -> dict[str, Any]: ...
def clear_open_position(instance_id: str | None = None) -> dict[str, Any]: ...
```

Rules:

- `instance_id is None`: use today's current-mode dispatch unchanged.
- `instance_id is not None`: read/write only the per-instance v2 storage file.
- Live MVP: reject or no-op per-instance live routing until a separate live exposure gate exists.
- Paper v2: allow separate per-instance virtual positions after tests prove isolation.
- Keep explicit helpers like `get_live_open_position()` and `get_paper_position()` unchanged for legacy direct callers.

Possible helper for a later phase:

```python
def get_signal_instance_id(signal: NormalizedSignal) -> str | None:
    raw = signal.raw_payload if isinstance(signal.raw_payload, dict) else {}
    return raw.get("instance_id") or raw.get("strategy_instance_id")
```

This helper should not be wired into production routing until the storage signatures and tests exist.

## 9. JSONL compatibility plan

Keep writing to the existing per-user order JSONL log. Future v2 events should add fields without removing or renaming legacy fields:

- `strategy_code`
- `instance_id`
- `strategy_version_id`
- `execution_mode`
- `v2_job_id`
- `source_signal_id`

Compatibility requirements:

- Existing records without `instance_id` must continue to render in portfolio analytics, order history, dashboard feeds, and chart markers.
- Portfolio analytics can later group by `instance_id` before pairing ENTRY/EXIT. Legacy records should be grouped under `None` or `"legacy"`.
- Chart markers can continue showing a global stream by default, with optional future filters by `instance_id` or `strategy_code`.
- Paper events and live events must keep the current `mode` field because marker filtering depends on it.
- Do not split the order ledger per instance in Phase 2D; the current frontend depends on the per-user stream.

## 10. Phase 2D-1 sequencing recommendation

1. Add the backward-compatible state-store signatures with `instance_id=None`.
2. Add tests proving `None` still reads/writes/clears the exact same live and paper files.
3. Add the separate per-instance JSON file and tests for isolated v2 paper positions.
4. Keep risk manager, execution router, monitor, reconcilers, and frontend on `instance_id=None`.
5. Only after storage is proven, pass `instance_id` through the v2 paper worker path.
6. Defer live per-instance execution, monitor iteration, analytics grouping, and frontend multi-position display to later phases.

## 11. No-change confirmation for Phase 2D-0

This audit phase intentionally does not change:

- Current Supertrend webhook behavior.
- Current manual order behavior.
- `route_signal()` behavior.
- Dhan order placement.
- Paper/live mode behavior.
- Risk blocking behavior.
- Dashboard, ledger, or marker behavior.
- v2 startup wiring or fanout execution.
