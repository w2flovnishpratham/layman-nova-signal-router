import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { EngineStrategy, RuntimeStatus } from '../../api'
import { TradingStrategyCard } from './TradingStrategyCard'

function strategy(overrides: Partial<EngineStrategy> = {}): EngineStrategy {
  return {
    instance_id: 'cf4799a2-e45d-4548-992c-69b2a1649cc1',
    display_name: 'b@S_again Paper',
    strategy_code: 'b-s-again',
    strategy_version: '3.1',
    source_type: 'PERSONAL_TRADINGVIEW',
    setup_type: 'NOVA_MANAGED_TRADINGVIEW',
    status: 'READY',
    instance_status: 'stopped',
    mode: 'paper',
    execution_mode: 'paper_live_data',
    paper_eligible: true,
    live_eligible: false,
    readiness: { hold_verified: true, can_activate: true },
    lots: 1,
    credential_status: 'active',
    installation_status: 'PAPER_VERIFIED',
    verification_mode: false,
    selectable: true,
    selected: true,
    blocking_reason: null,
    owner: 'self',
    ...overrides,
  }
}

function runtime(overrides: Partial<RuntimeStatus> = {}): RuntimeStatus {
  const selected = strategy()
  return {
    ok: true,
    owner_user_id: 'owner-a',
    engine: {
      state: 'STOPPED', running: false, accepting_signals: false, mode: 'paper',
      display: 'STOPPED', last_transition_at: null,
    },
    exit: { state: 'NONE', operation_id: null, requested_at: null },
    position: {
      has_open_position: false, security_id: null, trading_symbol: null, option_side: null,
      qty: 0, lots: 0, entry_price: null, opened_at: null, unrealized_pnl: null,
      ltp: {
        value: null, source: null, status: 'unavailable', received_at: null,
        age_seconds: null, stale: false, message: null,
      },
    },
    pnl: { realized: 0, unrealized: 0, session: 0, available_balance: 100000 },
    config: {
      active: { configured_lots: 1, option_sl_percent: 8, option_tp_percent: 16 },
      paper: { configured_lots: 1, option_sl_percent: 8, option_tp_percent: 16 },
      live: {},
    },
    account: {
      dhan_client_id_masked: null, has_dhan_access_token: false, dhan_token_saved_at: null,
      token_age_minutes: null, token_expired: null, token_estimated_expiry_at: null,
    },
    safety: { dhan_mode: 'MOCK', live_orders_enabled: false },
    selected_strategy: selected,
    eligible_strategies: [selected],
    selection_issue: null,
    ...overrides,
  }
}

function callbacks() {
  return {
    onManage: vi.fn(),
    onConfigure: vi.fn().mockResolvedValue(undefined),
    onSelect: vi.fn().mockResolvedValue(undefined),
    onStart: vi.fn().mockResolvedValue(undefined),
  }
}

afterEach(() => cleanup())

describe('TradingStrategyCard', () => {
  it('hydrates the selected stopped strategy and server Paper settings without legacy choices', () => {
    const actions = callbacks()
    render(<TradingStrategyCard runtime={runtime()} loading={false} error="" {...actions} />)
    expect(screen.getByText('b@S_again Paper')).toBeInTheDocument()
    expect(screen.getByText('Ready for Paper')).toBeInTheDocument()
    expect(screen.getByText('STOPPED')).toBeInTheDocument()
    expect(screen.getByText('8%')).toBeInTheDocument()
    expect(screen.getByText('16%')).toBeInTheDocument()
    expect(screen.queryByText('Supertrend')).not.toBeInTheDocument()
    for (const legacy of ['ORB', 'VWAP', 'RSI', 'Scalper']) {
      expect(screen.queryByText(legacy)).not.toBeInTheDocument()
    }
    expect(actions.onStart).not.toHaveBeenCalled()
  })

  it('starts only after an explicit click and uses the selected instance id', async () => {
    const actions = callbacks()
    const user = userEvent.setup()
    render(<TradingStrategyCard runtime={runtime()} loading={false} error="" {...actions} />)
    expect(actions.onStart).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /start paper engine/i }))
    expect(actions.onStart).toHaveBeenCalledTimes(1)
    expect(actions.onStart).toHaveBeenCalledWith('cf4799a2-e45d-4548-992c-69b2a1649cc1')
  })

  it('manages and configures the selected instance, then hydrates on rerender', async () => {
    const actions = callbacks()
    const user = userEvent.setup()
    const view = render(<TradingStrategyCard runtime={runtime()} loading={false} error="" {...actions} />)
    await user.click(screen.getByRole('button', { name: /manage strategy/i }))
    expect(actions.onManage).toHaveBeenCalledWith('cf4799a2-e45d-4548-992c-69b2a1649cc1')
    await user.click(screen.getByRole('button', { name: /configure paper settings/i }))
    await user.clear(screen.getByLabelText('Paper lots'))
    await user.type(screen.getByLabelText('Paper lots'), '2')
    await user.click(screen.getByRole('button', { name: /save paper settings/i }))
    await waitFor(() => expect(actions.onConfigure).toHaveBeenCalledWith(
      'cf4799a2-e45d-4548-992c-69b2a1649cc1', 2, 8, 16,
    ))
    view.rerender(<TradingStrategyCard runtime={runtime()} loading={false} error="" {...actions} />)
    expect(screen.getByText('b@S_again Paper')).toBeInTheDocument()
  })

  it('shows only backend-provided eligible alternatives and persists the selected id', async () => {
    const actions = callbacks()
    const user = userEvent.setup()
    const alternative = strategy({
      instance_id: 'owner-alt',
      display_name: 'Owner alternative',
      strategy_code: 'owner-alt',
      selected: false,
    })
    render(<TradingStrategyCard
      runtime={runtime({ eligible_strategies: [strategy(), alternative] })}
      loading={false}
      error=""
      {...actions}
    />)
    await user.click(screen.getByRole('button', { name: /change strategy/i }))
    expect(screen.getByText('Owner alternative')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Owner alternative.*Select/i }))
    expect(actions.onSelect).toHaveBeenCalledWith('owner-alt')
    expect(screen.queryByText('Supertrend')).not.toBeInTheDocument()
  })

  it('renders loading, empty, unauthorized, and lost-readiness states safely', () => {
    const actions = callbacks()
    const view = render(<TradingStrategyCard runtime={null} loading error="" {...actions} />)
    expect(screen.getByText(/loading your selected strategy/i)).toBeInTheDocument()
    view.rerender(<TradingStrategyCard runtime={null} loading={false} error="Not authenticated" {...actions} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Not authenticated')
    view.rerender(<TradingStrategyCard
      runtime={runtime({ selected_strategy: null, eligible_strategies: [], selection_issue: 'STRATEGY_NOT_SELECTED' })}
      loading={false}
      error=""
      {...actions}
    />)
    expect(screen.getByText('No Paper-ready strategies available.')).toBeInTheDocument()
    view.rerender(<TradingStrategyCard
      runtime={runtime({
        selected_strategy: strategy({
          selectable: false,
          paper_eligible: false,
          blocking_reason: 'INSTALLATION_SUSPENDED',
        }),
        eligible_strategies: [],
        selection_issue: 'INSTALLATION_SUSPENDED',
      })}
      loading={false}
      error=""
      {...actions}
    />)
    expect(screen.getAllByText(/suspended/i).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /start paper engine/i })).toBeDisabled()
  })

  it('blocks switching and configuration when runtime is running or a position is open', async () => {
    const actions = callbacks()
    const alternative = strategy({ instance_id: 'alt', display_name: 'Alternative', selected: false })
    render(<TradingStrategyCard
      runtime={runtime({
        engine: {
          state: 'RUNNING', running: true, accepting_signals: true, mode: 'paper',
          display: 'RUNNING', last_transition_at: null,
        },
        eligible_strategies: [strategy(), alternative],
      })}
      loading={false}
      error=""
      {...actions}
    />)
    expect(screen.getByText('RUNNING')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /change strategy/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /configure paper settings/i })).toBeDisabled()
    expect(actions.onSelect).not.toHaveBeenCalled()
  })

  it('does not expose Pine or credential secret material', () => {
    const actions = callbacks()
    const { container } = render(<TradingStrategyCard runtime={runtime()} loading={false} error="" {...actions} />)
    expect(container).not.toHaveTextContent('nwk_')
    expect(container).not.toHaveTextContent('//@version')
    expect(container).not.toHaveTextContent('source_sha256')
    expect(container).toHaveTextContent('Credential active')
  })
})
