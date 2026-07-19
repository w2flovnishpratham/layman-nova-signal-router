import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CatalogStrategy, EngineStrategy, RuntimeStatus, StrategyCatalog } from '../../api'
import { UnifiedStrategySetup } from './UnifiedStrategySetup'

const fields: CatalogStrategy['setup_schema']['fields'] = [
  { key: 'direction', type: 'choice', label: 'Which signals should NOVA trade?', options: ['CE', 'PE', 'BOTH'], required: true, default: 'BOTH' },
  { key: 'lots', type: 'integer', label: 'How many lots should be used?', minimum: 1, maximum: 20, required: true, default: 1 },
  { key: 'stop_loss_percent', type: 'decimal', label: 'What stop loss percentage should be applied?', minimum: 0, maximum: 100, required: true, default: 10 },
  { key: 'take_profit_percent', type: 'decimal', label: 'What take profit percentage should be applied?', minimum: 0, maximum: 1000, required: true, default: 20 },
]

function catalogStrategy(overrides: Partial<CatalogStrategy>): CatalogStrategy {
  return {
    strategy_key: 'nova-supertrend',
    strategy_instance_id: null,
    source_type: 'BUILT_IN',
    name: 'Supertrend',
    version: '1.0.0',
    description: 'NOVA built-in Supertrend strategy',
    availability: 'READY',
    disabled_reason: null,
    paper_eligible: true,
    live_eligible: false,
    selected: false,
    runtime_state: 'STOPPED',
    setup_schema: { fields },
    saved_setup: {},
    ...overrides,
  }
}

function selectedEngine(overrides: Partial<EngineStrategy> = {}): EngineStrategy {
  return {
    instance_id: 'cf4799a2-e45d-4548-992c-69b2a1649cc1',
    display_name: 'b@S_again Paper',
    strategy_code: 'b-s-again',
    strategy_version: '1.0.1',
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
    selectable: true,
    selected: true,
    blocking_reason: null,
    owner: 'self',
    ...overrides,
  }
}

function strategyCatalog(complete = false): StrategyCatalog {
  const importedKey = 'cf4799a2-e45d-4548-992c-69b2a1649cc1'
  const disabled = (key: string, name: string) => catalogStrategy({
    strategy_key: key,
    name,
    version: null,
    availability: 'COMING_SOON',
    disabled_reason: 'Missing execution adapter',
    paper_eligible: false,
    setup_schema: { fields: [] },
  })
  return {
    strategies: [
      catalogStrategy({}),
      disabled('nova-orb', 'ORB'),
      disabled('nova-vwap', 'VWAP'),
      disabled('nova-rsi', 'RSI'),
      disabled('nova-scalper', 'Scalper'),
      catalogStrategy({
        strategy_key: importedKey,
        strategy_instance_id: importedKey,
        source_type: 'IMPORTED',
        name: 'b@S_again Paper',
        version: '1.0.1',
        description: 'Imported owner-bound Pine strategy',
        selected: true,
        saved_setup: complete ? {
          paper: {
            complete: true,
            direction: 'BOTH',
            lots: 1,
            stop_loss_percent: 8,
            take_profit_percent: 16,
          },
        } : {},
      }),
    ],
    selected_strategy_key: importedKey,
    selected_strategy_instance_id: importedKey,
    setup_progress: {
      strategy_key: importedKey,
      mode: complete ? 'paper' : null,
      stage: complete ? 'REVIEW' : 'CONFIGURE',
      complete,
    },
  }
}

function runtime(complete = false, overrides: Partial<RuntimeStatus> = {}): RuntimeStatus {
  const selected = selectedEngine()
  return {
    ok: true,
    owner_user_id: 'owner-a',
    engine: { state: 'STOPPED', running: false, accepting_signals: false, mode: 'paper', display: 'STOPPED', last_transition_at: null },
    exit: { state: 'NONE', operation_id: null, requested_at: null },
    position: {
      has_open_position: false, security_id: null, trading_symbol: null, option_side: null,
      qty: 0, lots: 0, entry_price: null, opened_at: null, unrealized_pnl: null,
      ltp: { value: null, source: null, status: 'unavailable', received_at: null, age_seconds: null, stale: false, message: null },
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
    strategy_catalog: strategyCatalog(complete),
    ...overrides,
  }
}

function callbacks() {
  return {
    onManage: vi.fn(),
    onSelect: vi.fn().mockResolvedValue(undefined),
    onSave: vi.fn().mockResolvedValue(undefined),
    onStart: vi.fn().mockResolvedValue(undefined),
    onUserReply: vi.fn(),
  }
}

afterEach(() => cleanup())

describe('UnifiedStrategySetup', () => {
  it('renders backend-provided NOVA and My Strategies groups with all availability states', () => {
    const actions = callbacks()
    render(<UnifiedStrategySetup runtime={runtime()} loading={false} error="" {...actions} />)
    expect(screen.getByText('NOVA Strategies')).toBeInTheDocument()
    expect(screen.getByText('My Strategies')).toBeInTheDocument()
    for (const name of ['Supertrend', 'ORB', 'VWAP', 'RSI', 'Scalper', 'b@S_again Paper']) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
    expect(screen.getAllByText(/missing execution adapter/i)).toHaveLength(4)
    expect(screen.getByText(/live unavailable on this environment/i)).toBeInTheDocument()
    expect(actions.onStart).not.toHaveBeenCalled()
  })

  it('uses one schema-driven conversation for an imported strategy and saves atomically', async () => {
    const actions = callbacks()
    const user = userEvent.setup()
    render(<UnifiedStrategySetup runtime={runtime()} loading={false} error="" {...actions} />)
    await user.click(screen.getByRole('button', { name: /b@S_again Paper/i }))
    expect(actions.onSelect).toHaveBeenCalledWith('cf4799a2-e45d-4548-992c-69b2a1649cc1')
    expect(screen.getByText('Which signals should NOVA trade?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /continue/i }))
    expect(screen.getByText('How many lots should be used?')).toBeInTheDocument()
    await user.clear(screen.getByLabelText('How many lots should be used?'))
    await user.type(screen.getByLabelText('How many lots should be used?'), '2')
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.click(screen.getByRole('button', { name: /save and review/i }))
    await waitFor(() => expect(actions.onSave).toHaveBeenCalledWith(
      'cf4799a2-e45d-4548-992c-69b2a1649cc1',
      expect.objectContaining({ direction: 'BOTH', lots: '2', stop_loss_percent: 10, take_profit_percent: 20 }),
    ))
    expect(await screen.findByText('b@S_again Paper')).toBeInTheDocument()
    expect(actions.onStart).not.toHaveBeenCalled()
  })

  it('shows controlled client validation and does not save an invalid lot count', async () => {
    const actions = callbacks()
    const user = userEvent.setup()
    render(<UnifiedStrategySetup runtime={runtime()} loading={false} error="" {...actions} />)
    await user.click(screen.getByRole('button', { name: /supertrend/i }))
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.clear(screen.getByLabelText('How many lots should be used?'))
    await user.type(screen.getByLabelText('How many lots should be used?'), '21')
    await user.click(screen.getByRole('button', { name: /continue/i }))
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a value from 1 to 20')
    expect(actions.onSave).not.toHaveBeenCalled()
  })

  it('rehydrates completed setup directly into the existing review card', () => {
    const actions = callbacks()
    render(<UnifiedStrategySetup runtime={runtime(true)} loading={false} error="" {...actions} />)
    expect(screen.getByText('Selected strategy')).toBeInTheDocument()
    expect(screen.getByText('b@S_again Paper')).toBeInTheDocument()
    expect(screen.queryByText('NOVA Strategies')).not.toBeInTheDocument()
  })

  it('starts only after explicit review click', async () => {
    const actions = callbacks()
    const user = userEvent.setup()
    render(<UnifiedStrategySetup runtime={runtime(true)} loading={false} error="" {...actions} />)
    expect(actions.onStart).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /start paper engine/i }))
    expect(actions.onStart).toHaveBeenCalledWith('cf4799a2-e45d-4548-992c-69b2a1649cc1')
  })

  it('Change Strategy returns to catalog when stopped and flat', async () => {
    const actions = callbacks()
    const user = userEvent.setup()
    render(<UnifiedStrategySetup runtime={runtime(true)} loading={false} error="" {...actions} />)
    await user.click(screen.getByRole('button', { name: /change strategy/i }))
    expect(screen.getByText('NOVA Strategies')).toBeInTheDocument()
    expect(screen.getByText('My Strategies')).toBeInTheDocument()
  })

  it('blocks unsafe change while running or holding a position', () => {
    const actions = callbacks()
    render(<UnifiedStrategySetup
      runtime={runtime(true, {
        engine: { state: 'RUNNING', running: true, accepting_signals: true, mode: 'paper', display: 'RUNNING', last_transition_at: null },
      })}
      loading={false}
      error=""
      {...actions}
    />)
    expect(screen.getByRole('button', { name: /change strategy/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /configure paper settings/i })).toBeDisabled()
  })

  it('does not expose credentials or Pine source in catalog or review DOM', () => {
    const actions = callbacks()
    const { container } = render(<UnifiedStrategySetup runtime={runtime()} loading={false} error="" {...actions} />)
    expect(container).not.toHaveTextContent('nwk_')
    expect(container).not.toHaveTextContent('//@version')
    expect(container).not.toHaveTextContent('source_sha256')
    expect(container).not.toHaveTextContent('access_token')
  })
})
