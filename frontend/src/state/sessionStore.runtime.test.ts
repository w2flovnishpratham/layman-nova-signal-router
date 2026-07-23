import { beforeEach, describe, expect, it } from 'vitest'
import type { RuntimeStatus } from '../api'
import type { MarketSnapshot, ServerEvent } from '../types'
import {
  applyRestMarketSnapshot,
  applyRuntimeHydration,
  useSessionStore,
} from './sessionStore'

function runtime(overrides: Partial<RuntimeStatus['position']> = {}): RuntimeStatus {
  return {
    ok: true,
    owner_user_id: 'owner-a',
    engine: {
      state: 'RUNNING', running: true, accepting_signals: true, mode: 'paper',
      display: 'RUNNING', last_transition_at: '2026-07-21T08:00:00Z',
    },
    exit: { state: 'NONE', operation_id: null, requested_at: null },
    position: {
      has_open_position: true,
      security_id: '123', trading_symbol: 'NIFTY TEST CE', option_side: 'CE',
      strike: 24200, expiry: '2026-07-21', entry_order_id: 'PAPER-1',
      qty: 65, lots: 1, entry_price: 20, opened_at: '2026-07-21T08:00:00Z',
      unrealized_pnl: 65,
      risk: {
        armed: true, status: 'armed', source: 'server_monitor', server_managed: true,
        stop_price: 18, target_price: 24, stop_loss_percent: 10, take_profit_percent: 20,
      },
      ltp: {
        value: 21, source: 'dhan_marketfeed_ws', status: 'ready',
        received_at: '2026-07-21T08:00:02Z', age_seconds: 0.2, stale: false, message: null,
      },
      ...overrides,
    },
    pnl: { realized: 0, unrealized: 65, session: 65, available_balance: 98635, utilized_amount: 1365 },
    config: { active: {}, paper: {}, live: {} },
    account: {
      dhan_client_id_masked: null, has_dhan_access_token: false, dhan_token_saved_at: null,
      token_age_minutes: null, token_expired: null, token_estimated_expiry_at: null,
    },
    safety: { dhan_mode: 'MOCK', live_orders_enabled: false },
    selected_strategy: null, eligible_strategies: [], selection_issue: null,
  }
}

beforeEach(() => {
  useSessionStore.getState().resetSession()
})

describe('runtime REST hydration failover', () => {
  it('hydrates lifecycle, LTP, P&L, and server-managed risk from REST', () => {
    applyRuntimeHydration(runtime(), 100)
    const state = useSessionStore.getState()
    expect(state.setupState).toBe('LIVE')
    expect(state.activeTrade).toMatchObject({
      ltp: 21, pnl: 65, riskArmed: true, riskSource: 'server_monitor',
      activeExitLevels: { stopLossPrice: 18, targetPrice: 24 },
    })
    expect(state.marginUtilized).toBe(1365)
  })

  it('does not let an older REST request overwrite a newer push tick', () => {
    applyRuntimeHydration(runtime(), 100)
    useSessionStore.getState().applyServerEvent({
      id: 'tick-newer', type: 'tick.pnl', ts: '2026-07-21T08:00:03Z',
      data: { ltp: 22, pnl: 130, pnlPct: 10 },
    } as unknown as ServerEvent)
    applyRuntimeHydration(runtime({
      ltp: { value: 19, source: 'rest', status: 'ready', received_at: null, age_seconds: 0, stale: false, message: null },
      unrealized_pnl: -65,
    }), 1)
    expect(useSessionStore.getState().activeTrade).toMatchObject({ ltp: 22, pnl: 130 })
  })

  it('clears exposure and retains realized P&L when runtime is flat', () => {
    applyRuntimeHydration(runtime(), 100)
    const flat = runtime({ has_open_position: false })
    flat.pnl = { realized: -182.82, unrealized: 0, session: -182.82, available_balance: 99817.18 }
    applyRuntimeHydration(flat, Date.now() + 1)
    expect(useSessionStore.getState().activeTrade).toBeNull()
    expect(useSessionStore.getState().realizedPnl).toBe(-182.82)
  })

  it('uses REST market data unless a newer push snapshot won the request race', () => {
    const rest = { niftySpot: 24100 } as MarketSnapshot
    const push = { niftySpot: 24200 } as MarketSnapshot
    applyRestMarketSnapshot(rest, 100)
    expect(useSessionStore.getState()).toMatchObject({ marketSnapshot: rest, marketSnapshotSource: 'rest' })
    useSessionStore.getState().applyServerEvent({
      id: 'market-newer', type: 'market.snapshot', ts: '2026-07-21T08:00:03Z', data: push,
    } as unknown as ServerEvent)
    applyRestMarketSnapshot(rest, 1)
    expect(useSessionStore.getState()).toMatchObject({ marketSnapshot: push, marketSnapshotSource: 'push' })
  })
})
