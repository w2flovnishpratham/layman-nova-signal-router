import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RuntimeStatus } from '../api'
import { Header } from './Header'

const api = vi.hoisted(() => ({ getEgressStatus: vi.fn() }))
vi.mock('../api', () => ({ getEgressStatus: api.getEgressStatus }))

function runtime(overrides: Partial<RuntimeStatus> = {}): RuntimeStatus {
  const base: RuntimeStatus = {
    ok: true,
    owner_user_id: 'owner-1',
    engine: {
      state: 'STOPPED',
      running: false,
      accepting_signals: false,
      mode: 'paper',
      display: 'STOPPED',
      last_transition_at: '2026-07-20T00:00:00Z',
    },
    exit: { state: 'NONE', operation_id: null, requested_at: null },
    position: {
      has_open_position: false,
      security_id: null,
      trading_symbol: null,
      option_side: null,
      qty: 0,
      lots: 0,
      entry_price: null,
      opened_at: null,
      unrealized_pnl: null,
      ltp: {
        value: null,
        source: null,
        status: 'not_applicable',
        received_at: null,
        age_seconds: null,
        stale: false,
        message: null,
      },
    },
    pnl: { realized: 0, unrealized: 0, session: 0, available_balance: 100000 },
    config: {
      active: { configured_lots: 2, option_sl_percent: 8, option_tp_percent: 16 },
      paper: {},
      live: {},
    },
    account: {
      dhan_client_id_masked: '12••••78',
      has_dhan_access_token: true,
      dhan_token_saved_at: null,
      token_age_minutes: 10,
      token_expired: false,
      token_estimated_expiry_at: null,
    },
    safety: { dhan_mode: 'MOCK', live_orders_enabled: false },
    selected_strategy: null,
    eligible_strategies: [],
    selection_issue: null,
  }
  return {
    ...base,
    ...overrides,
    engine: { ...base.engine, ...overrides.engine },
    position: {
      ...base.position,
      ...overrides.position,
      ltp: { ...base.position.ltp, ...overrides.position?.ltp },
    },
    pnl: { ...base.pnl, ...overrides.pnl },
    config: { ...base.config, ...overrides.config },
    account: { ...base.account, ...overrides.account },
  }
}

function props(status = runtime()) {
  return {
    route: 'trading' as const,
    status: 'live' as const,
    clientId: undefined,
    runtime: status,
    engineLive: status.engine.running,
    engineMode: status.engine.mode,
    setupState: 'IDLE' as const,
    health: null,
    market: null,
    user: {
      id: 'owner-1',
      email: 'owner@example.com',
      name: 'Owner',
      picture_url: null,
      is_admin: false,
      is_dev: false,
      entitlements: {
        plan_code: 'free',
        plan_status: 'active',
        static_ip_enabled: false,
        strategy_access_enabled: false,
        max_strategy_count: 0,
      },
    },
    onNavigate: vi.fn(),
    onKill: vi.fn(),
    onStop: vi.fn(),
    onReconfigure: vi.fn(),
    onLogout: vi.fn(),
    onMode: vi.fn(),
    onSaveConfig: vi.fn(),
    onPaperReset: vi.fn(),
    onAccountRefresh: vi.fn(),
  }
}

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'More actions' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getEgressStatus.mockResolvedValue(null)
})
afterEach(() => cleanup())

describe('Header runtime reliability controls', () => {
  it('keeps setup inside Trading instead of exposing a duplicate top-level route', () => {
    render(<Header {...props()} />)
    expect(screen.getByRole('button', { name: 'Trading' })).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('button', { name: 'Setup' })).not.toBeInTheDocument()
  })

  it('renders the backend lifecycle display', async () => {
    const user = userEvent.setup()
    render(<Header {...props()} />)
    await openMenu(user)
    expect(screen.getByText('STOPPED')).toBeInTheDocument()
  })

  it('renders only the server-masked Dhan client id', async () => {
    const user = userEvent.setup()
    render(<Header {...props()} />)
    await openMenu(user)
    expect(screen.getByText('12••••78')).toBeInTheDocument()
  })

  it('shows an open-position stopped warning from backend state', async () => {
    const user = userEvent.setup()
    const status = runtime({
      engine: { display: 'POSITION OPEN — ENGINE STOPPED' } as RuntimeStatus['engine'],
      position: { has_open_position: true, trading_symbol: 'NIFTY CE', qty: 65 } as RuntimeStatus['position'],
    })
    render(<Header {...props(status)} />)
    await openMenu(user)
    expect(screen.getByText('POSITION OPEN — ENGINE STOPPED')).toBeInTheDocument()
    expect(screen.getByText(/NIFTY CE · Qty 65/)).toBeInTheDocument()
  })

  it('labels stale LTP without inventing a fresh value', async () => {
    const user = userEvent.setup()
    const status = runtime({
      position: { has_open_position: true, ltp: { value: 101.5, status: 'stale', stale: true } } as RuntimeStatus['position'],
    })
    render(<Header {...props(status)} />)
    await openMenu(user)
    expect(screen.getByText('LTP 101.5 · stale')).toBeInTheDocument()
  })

  it('renders flat as no active position instead of an LTP error', async () => {
    const user = userEvent.setup()
    render(<Header {...props()} />)
    await openMenu(user)
    expect(screen.getByText('Flat — no active option position')).toBeInTheDocument()
    expect(screen.getByText('No active LTP')).toBeInTheDocument()
    expect(screen.queryByText('Live option quote unavailable')).not.toBeInTheDocument()
  })

  it('shows source, timestamp, and age only for an open quote failure', async () => {
    const user = userEvent.setup()
    const status = runtime({
      position: {
        has_open_position: true,
        ltp: {
          value: null,
          status: 'ltp_error',
          stale: false,
          source: 'dhan_marketfeed_ws',
          received_at: '2026-07-21T08:00:00Z',
          age_seconds: 17,
        },
      } as RuntimeStatus['position'],
    })
    render(<Header {...props(status)} />)
    await openMenu(user)
    expect(screen.getByText('Live option quote unavailable')).toBeInTheDocument()
    expect(screen.getByText(/dhan_marketfeed_ws.*17s old/)).toBeInTheDocument()
  })

  it('does not expose stopped configuration while square-off is still stopping', async () => {
    const user = userEvent.setup()
    const status = runtime({
      engine: {
        state: 'STOPPING',
        running: false,
        display: 'EXIT PENDING — ENGINE STOPPING',
      } as RuntimeStatus['engine'],
      position: { has_open_position: true, qty: 65 } as RuntimeStatus['position'],
    })
    render(<Header {...props(status)} />)
    await openMenu(user)
    expect(screen.getByText('EXIT PENDING — ENGINE STOPPING')).toBeInTheDocument()
    expect(screen.queryByLabelText('Runtime lots')).not.toBeInTheDocument()
  })

  it('shows Stop Engine only while running', async () => {
    const user = userEvent.setup()
    const status = runtime({ engine: { state: 'RUNNING', running: true, display: 'RUNNING' } as RuntimeStatus['engine'] })
    render(<Header {...props(status)} />)
    await openMenu(user)
    expect(screen.getByRole('button', { name: 'Stop Engine' })).toBeInTheDocument()
  })

  it('normal Stop Engine calls the non-square-off action', async () => {
    const user = userEvent.setup()
    const callbacks = props(runtime({ engine: { running: true, state: 'RUNNING' } as RuntimeStatus['engine'] }))
    render(<Header {...callbacks} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Stop Engine' }))
    expect(callbacks.onStop).toHaveBeenCalledOnce()
    expect(callbacks.onKill).not.toHaveBeenCalled()
  })

  it('opens the Stop & Square Off confirmation', async () => {
    const user = userEvent.setup()
    render(<Header {...props(runtime({ engine: { running: true, state: 'RUNNING' } as RuntimeStatus['engine'] }))} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Stop & Square Off' }))
    expect(screen.getByRole('dialog', { name: /stop routing and square off/i })).toBeInTheDocument()
  })

  it('requires the hold gesture before square-off callback', async () => {
    vi.useFakeTimers()
    const callbacks = props(runtime({ engine: { running: true, state: 'RUNNING' } as RuntimeStatus['engine'] }))
    render(<Header {...callbacks} />)
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(screen.getByRole('button', { name: 'Stop & Square Off' }))
    fireEvent.pointerDown(screen.getByRole('button', { name: /hold to stop/i }))
    expect(callbacks.onKill).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(801))
    expect(callbacks.onKill).toHaveBeenCalledOnce()
    vi.useRealTimers()
  })

  it('opens a reconfigure confirmation', async () => {
    const user = userEvent.setup()
    render(<Header {...props()} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Re-Configure' }))
    expect(screen.getByRole('dialog', { name: /stop engine and reconfigure/i })).toBeInTheDocument()
  })

  it('confirms reconfigure without auto-starting anything client-side', async () => {
    const user = userEvent.setup()
    const callbacks = props()
    render(<Header {...callbacks} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Re-Configure' }))
    await user.click(screen.getByRole('button', { name: 'Stop and continue' }))
    expect(callbacks.onReconfigure).toHaveBeenCalledOnce()
    expect(callbacks.onMode).not.toHaveBeenCalled()
  })

  it('opens logout choices instead of immediately logging out', async () => {
    const user = userEvent.setup()
    const callbacks = props()
    render(<Header {...callbacks} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Log Out' }))
    expect(callbacks.onLogout).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: /log out of nova/i })).toBeInTheDocument()
  })

  it('supports keep-running logout', async () => {
    const user = userEvent.setup()
    const callbacks = props()
    render(<Header {...callbacks} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Log Out' }))
    await user.click(screen.getByRole('button', { name: /keep engine running/i }))
    expect(callbacks.onLogout).toHaveBeenCalledWith('keep_running')
  })

  it('supports stop-engine logout', async () => {
    const user = userEvent.setup()
    const callbacks = props()
    render(<Header {...callbacks} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Log Out' }))
    await user.click(screen.getByRole('button', { name: /stop engine and log out/i }))
    expect(callbacks.onLogout).toHaveBeenCalledWith('stop_engine')
  })

  it('supports cancelling logout with no mutation', async () => {
    const user = userEvent.setup()
    const callbacks = props()
    render(<Header {...callbacks} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Log Out' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(callbacks.onLogout).not.toHaveBeenCalled()
  })

  it('shows a visible Paper mode control while stopped', async () => {
    const user = userEvent.setup()
    render(<Header {...props()} />)
    await openMenu(user)
    expect(screen.getByRole('button', { name: 'Paper' })).toBeInTheDocument()
  })

  it('shows a visible Live mode control while stopped', async () => {
    const user = userEvent.setup()
    render(<Header {...props()} />)
    await openMenu(user)
    expect(screen.getByRole('button', { name: 'Live' })).toBeInTheDocument()
  })

  it('switches to Paper only through the server callback', async () => {
    const user = userEvent.setup()
    const callbacks = props(runtime({ engine: { mode: 'live' } as RuntimeStatus['engine'] }))
    render(<Header {...callbacks} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Paper' }))
    expect(callbacks.onMode).toHaveBeenCalledWith('paper')
  })

  it('switches to Live only through the server callback', async () => {
    const user = userEvent.setup()
    const callbacks = props()
    render(<Header {...callbacks} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Live' }))
    expect(callbacks.onMode).toHaveBeenCalledWith('live')
  })

  it('disables mode changes while a position is open', async () => {
    const user = userEvent.setup()
    const status = runtime({ position: { has_open_position: true } as RuntimeStatus['position'] })
    render(<Header {...props(status)} />)
    await openMenu(user)
    expect(screen.getByRole('button', { name: 'Live' })).toBeDisabled()
  })

  it('hydrates lot, SL, and TP inputs from active server config', async () => {
    const user = userEvent.setup()
    render(<Header {...props()} />)
    await openMenu(user)
    expect(screen.getByLabelText('Runtime lots')).toHaveValue(2)
    expect(screen.getByLabelText('Runtime stop loss percent')).toHaveValue(8)
    expect(screen.getByLabelText('Runtime target profit percent')).toHaveValue(16)
  })

  it('saves lot, SL, and TP atomically for the active mode', async () => {
    const user = userEvent.setup()
    const callbacks = props()
    render(<Header {...callbacks} />)
    await openMenu(user)
    await user.clear(screen.getByLabelText('Runtime lots'))
    await user.type(screen.getByLabelText('Runtime lots'), '5')
    await user.click(screen.getByRole('button', { name: 'Save PAPER settings' }))
    expect(callbacks.onSaveConfig).toHaveBeenCalledWith('paper', 5, 8, 16)
  })

  it('shows an explicit Paper reset only in Paper mode', async () => {
    const user = userEvent.setup()
    render(<Header {...props()} />)
    await openMenu(user)
    expect(screen.getByRole('button', { name: 'Reset Paper session' })).toBeInTheDocument()
  })

  it('does not show Paper reset in Live mode', async () => {
    const user = userEvent.setup()
    render(<Header {...props(runtime({ engine: { mode: 'live' } as RuntimeStatus['engine'] }))} />)
    await openMenu(user)
    expect(screen.queryByRole('button', { name: 'Reset Paper session' })).not.toBeInTheDocument()
  })

  it('calls the explicit Paper reset callback', async () => {
    const user = userEvent.setup()
    const callbacks = props()
    render(<Header {...callbacks} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Reset Paper session' }))
    expect(callbacks.onPaperReset).toHaveBeenCalledOnce()
  })

  it('refreshes broker account state without changing lifecycle locally', async () => {
    const user = userEvent.setup()
    const callbacks = props()
    render(<Header {...callbacks} />)
    await openMenu(user)
    await user.click(screen.getByRole('button', { name: 'Refresh Dhan account' }))
    expect(callbacks.onAccountRefresh).toHaveBeenCalledOnce()
    expect(callbacks.onStop).not.toHaveBeenCalled()
  })
})
