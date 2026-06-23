# Real-Time ATM LTP + PnL Fix — Exact Diagnosis & Implementation Spec

> Scope: kill the 5–10s lag on ATM CE/PE LTP and live PnL. No microservices.
> The fix is wiring + config inside the existing FastAPI monolith.

---

## 1. The exact issue (root cause, with evidence)

You have **two separate data paths**, and the lag comes from different places in each.

### Path A — ATM CE/PE LTP on the MarketCard  → **THE MAIN PROBLEM (poll + 15s cache)**

Flow today:

```
Dhan WS ──ticks──► dhan_marketfeed_ws (fast, fine)
                          │
Browser ──poll every 4s──► GET /api/market/nifty-snapshot
                          │   (frontend/src/App.tsx:152  setTimeout(poll, 4000))
                          ▼
              routers/market.py  nifty_snapshot()
                          ▼
        services/atm_ltp_service.get_atm_option_snapshot()
                          ▼
              _ltp_with_prefer_ws()  ──reads──► 15s LTP CACHE
              (atm_ltp_service.py: DEFAULT_LTP_CACHE_SECONDS = 15.0)
```

Latency stack for ATM LTP:
- up to **4000 ms** waiting for the next poll (`App.tsx:152`)
- plus up to **15 000 ms** of server-side cache (`atm_ltp_service.py` `DEFAULT_LTP_CACHE_SECONDS = 15.0`; no `option_ltp_cache_seconds` override exists in `config.py` defaults, so it always falls back to 15)
- plus a 5s WS "stale" window (`option_ws_stale_seconds = 5.0`)

**Worst case ≈ 4 + 15 ≈ 19 s; typical 5–10 s.** Exactly what you see. The ATM card is **never pushed** over the WebSocket — it is poll-only.

> Subtlety: the ATM CE/PE contracts stay subscribed on the Dhan feed **only because** the 4s poll keeps calling `ensure_marketfeed_subscription()`. If you remove polling you MUST add a heartbeat that keeps them subscribed (the push loop below does this).

### Path B — Active-position live PnL (`tick.pnl`) → **freezes up to 15s on stale ticks**

This path is *already* push-based and mostly fine:

```
services/option_position_monitor._monitor_loop()   (every option_ltp_poll_seconds = 1.0s)
   └─ monitor_once() ─ get_marketfeed_ltp(stale=5s) ─ publish_tick_pnl_from_sync()
        └─ session_store.append_event("tick.pnl") ─► browser WS ─► sessionStore.ts:251 updates activeTrade
```

But two things stall it:
- `option_ws_stale_seconds = 5.0` — a WS tick older than 5s is treated as unusable.
- `option_rest_fallback_cooldown_seconds = 15.0` — once WS is stale, REST refresh is blocked for 15s. So in a quiet/slow-tick window, LTP (and therefore PnL) can sit frozen for **up to 15 s**.
- Also: `frontend/src/state/sessionStore.ts:252` early-returns if `state.activeTrade` is null, so PnL only updates when an active trade object already exists.

---

## 2. Fix plan (ordered by impact)

### FIX 1 — Push the ATM snapshot over the existing WebSocket (removes the 4s poll gap)  ★ biggest win

Add a server loop that computes the ATM snapshot ~1×/sec and pushes it to all active sessions as a new event, then have the frontend consume that event instead of waiting for the poll.

**Backend**

1. `backend/app/services/chat_event_publisher.py` — add a publisher (mirror `publish_tick_pnl`):

```python
async def publish_market_snapshot(*, snapshot: dict[str, Any], user_id: str | None = None) -> None:
    for session_id in await session_store.active_session_ids(user_id=user_id):
        await session_store.append_event(session_id, event("market.snapshot", **snapshot))

def publish_market_snapshot_from_sync(*, snapshot: dict[str, Any]) -> None:
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        return
    coro = publish_market_snapshot(snapshot=snapshot, user_id=_current_execution_user_id())
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        loop.create_task(coro)
    else:
        asyncio.run_coroutine_threadsafe(coro, loop)
```

2. `backend/app/services/option_position_monitor.py` — inside `monitor_once()` (runs every 1s and already has user context bound + market-open guard), after the existing PnL work, also push the market snapshot. This reuses the loop that already exists, so no new thread:

```python
from app.services.atm_ltp_service import get_atm_option_snapshot
from app.services.chat_event_publisher import publish_market_snapshot_from_sync
# ... at the end of monitor_once(), when _market_is_open():
snapshot = get_atm_option_snapshot(option_side="BOTH", lots=1, allow_rest_fallback=True)
publish_market_snapshot_from_sync(snapshot={"atm": snapshot, "niftySpot": snapshot.get("niftySpot")})
```

   - This call also runs `ensure_marketfeed_subscription()` for the ATM CE/PE legs every second, so the Dhan feed stays subscribed even after polling is reduced.
   - If you want the full nifty-snapshot shape, factor the body of `routers/market.py::nifty_snapshot()` into a `build_nifty_snapshot()` helper in a service and call that here so the WS payload === the REST payload.

**Frontend**

3. `frontend/src/state/sessionStore.ts` — handle the new event (add near the `tick.pnl` block, ~line 251). Store it where `App.tsx` can read it, OR expose a callback. Simplest: add `marketSnapshot` to the store and set it here:

```ts
if (event.type === 'market.snapshot') {
  return { marketSnapshot: event.data as MarketSnapshot }
}
```

   (Move `marketSnapshot` state from `App.tsx:38` into the store, or dispatch a `window` event that `App.tsx` listens to and calls `setMarketSnapshot`.)

4. `frontend/src/App.tsx:152` — keep the poll **only as a fallback heartbeat**: change `4000` → `15000`. The WS push now drives the card; the slow poll just covers reconnect gaps.

### FIX 2 — Shrink the server caches (LTP freshness)

5. `backend/app/services/atm_ltp_service.py:30` — `DEFAULT_LTP_CACHE_SECONDS = 15.0` → **`1.0`**.
6. `backend/app/config.py` (runtime defaults block ~line 46) — tighten:
   - `"option_ws_stale_seconds": 5.0` → **`2.0`**
   - `"option_rest_fallback_cooldown_seconds": 15.0` → **`3.0`**
   - add `"option_ltp_cache_seconds": 1.0` (so it's an operator-tunable knob instead of the hardcoded 15).

### FIX 3 — PnL never goes blank

7. `frontend/src/state/sessionStore.ts:252` — instead of `if (!state.activeTrade) return {}`, allow a `tick.pnl` to seed a minimal active trade (or ensure `activeTrade` is created on entry). Prevents the "PnL shows nothing until the next full snapshot" gap.

### FIX 4 (optional, robustness) — isolate the feed, NOT microservices

If a frontend/API hiccup ever drops your Dhan tick stream, run the market-feed as a **separate OS process** writing ticks to Redis, and have the API read from Redis. This is one extra process for fault-isolation — not a microservices migration, and not needed to fix the latency. Do it only after Fixes 1–3.

---

## 3. Verification

- Each option already carries `ltpAgeSeconds` (`atm_ltp_service._option_snapshot`). After the fix, confirm ATM `ltpAgeSeconds` stays < ~2s during market hours.
- In browser devtools WS tab, confirm `market.snapshot` frames arrive ~1/sec and the card updates without a REST `nifty-snapshot` call in between.
- Confirm PnL (`tick.pnl`) keeps moving during a low-volume minute (REST cooldown no longer freezes it for 15s).
- Target: ATM LTP + PnL visible age < 1.5s end-to-end.

---

## 4. Copy-paste prompt for a coding agent

> Paste everything below into your coding agent, run it against this repo.

```
You are fixing real-time latency in the Layman-Nova FastAPI + React trading app.
The ATM CE/PE LTP card and live PnL lag 5–10s. Root cause (already diagnosed, do not re-litigate):

1. ATM CE/PE LTP is POLL-ONLY: frontend/src/App.tsx:152 polls GET /api/market/nifty-snapshot
   every 4000ms, and backend services/atm_ltp_service.py serves a 15s cache
   (DEFAULT_LTP_CACHE_SECONDS = 15.0). The ATM card is never pushed over the WebSocket.
2. Live PnL (tick.pnl) IS pushed (services/option_position_monitor.py 1s loop) but freezes up
   to 15s because option_rest_fallback_cooldown_seconds=15 and option_ws_stale_seconds=5.

Implement, exactly:

A) Push ATM snapshot over the existing session WebSocket:
   - In services/chat_event_publisher.py add async publish_market_snapshot(...) and a sync wrapper
     publish_market_snapshot_from_sync(...), mirroring publish_tick_pnl / publish_tick_pnl_from_sync.
     Emit event type "market.snapshot" via session_store.append_event to all active_session_ids.
   - In services/option_position_monitor.py monitor_once(), when market is open, after the existing
     PnL publish, call get_atm_option_snapshot(option_side="BOTH", lots=1, allow_rest_fallback=True)
     and publish it via publish_market_snapshot_from_sync. (This also keeps ATM CE/PE subscribed
     on the Dhan feed every second.)
   - Frontend: in src/state/sessionStore.ts handle event.type === 'market.snapshot' to set
     marketSnapshot (move the marketSnapshot state from App.tsx into the store, or window-dispatch
     to App). In src/App.tsx change the poll interval 4000 -> 15000 (fallback heartbeat only).

B) Tighten freshness:
   - services/atm_ltp_service.py: DEFAULT_LTP_CACHE_SECONDS = 15.0 -> 1.0
   - backend/app/config.py runtime defaults: option_ws_stale_seconds 5.0 -> 2.0,
     option_rest_fallback_cooldown_seconds 15.0 -> 3.0, add option_ltp_cache_seconds: 1.0,
     and make atm_ltp_service read option_ltp_cache_seconds (default 1.0) instead of the constant.

C) PnL never blank: in src/state/sessionStore.ts the tick.pnl handler currently early-returns when
   state.activeTrade is null; instead seed a minimal activeTrade from the tick so PnL renders.

Constraints: do NOT introduce microservices or new infra. Reuse the existing WebSocket
(/ws/session/{id}) and the existing option_position_monitor thread. Keep REST endpoints working as
fallback. Add no new long-running threads unless strictly necessary.

After implementing: run the backend test suite, and confirm a market.snapshot frame is emitted ~1/sec
and that option ltpAgeSeconds stays < 2s. Report a diff summary.
```
