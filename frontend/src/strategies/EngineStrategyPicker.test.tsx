import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EngineStrategy } from '../api'
import { EngineStrategyPicker } from './EngineStrategyPicker'

const api = vi.hoisted(() => ({ getEngineStrategies: vi.fn(), setEngineSelection: vi.fn() }))
vi.mock('../api', () => ({ getEngineStrategies: api.getEngineStrategies, setEngineSelection: api.setEngineSelection }))

function strategy(overrides: Partial<EngineStrategy> = {}): EngineStrategy {
  return {
    instance_id: 'inst-1', display_name: 'Bollinger + RSI', source_type: 'PERSONAL_TRADINGVIEW',
    setup_type: 'NOVA_MANAGED_TRADINGVIEW', status: 'READY', instance_status: 'ready',
    verification_mode: false, selectable: true, selected: false, blocking_reason: null, owner: 'self', ...overrides,
  }
}

const feed = (strategies: EngineStrategy[], selected_instance_id: string | null = null) => ({ strategies, selected_instance_id })

beforeEach(() => vi.clearAllMocks())
afterEach(() => cleanup())

describe('EngineStrategyPicker', () => {
  it('persists the selected strategy instance via the backend and reflects it', async () => {
    api.getEngineStrategies
      .mockResolvedValueOnce(feed([strategy()], null))
      .mockResolvedValue(feed([strategy({ selected: true })], 'inst-1'))
    api.setEngineSelection.mockResolvedValue('inst-1')
    const user = userEvent.setup()
    render(<EngineStrategyPicker onManage={vi.fn()} />)
    await screen.findByText('Bollinger + RSI')
    await user.click(screen.getByRole('button', { name: /select strategy/i }))
    expect(api.setEngineSelection).toHaveBeenCalledWith('inst-1')
    await waitFor(() => expect(screen.getByText('Currently selected')).toBeInTheDocument())
    expect(await screen.findByText('Selected')).toBeInTheDocument()
  })

  it('keeps an unverified / in-verification strategy out of the selectable list with its blocker', async () => {
    api.getEngineStrategies.mockResolvedValue(feed([
      strategy({ instance_id: 'inst-2', display_name: 'Half-set up', selectable: false, verification_mode: true, status: 'VERIFICATION_IN_PROGRESS', blocking_reason: 'VERIFICATION_IN_PROGRESS' }),
    ]))
    render(<EngineStrategyPicker onManage={vi.fn()} />)
    await screen.findByText('Half-set up')
    expect(screen.getByText('No verified strategies yet')).toBeInTheDocument()
    expect(screen.getByText(/verification in progress/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /select strategy/i })).not.toBeInTheDocument()
  })

  it('only renders strategies the owner-scoped API returned (no other users)', async () => {
    api.getEngineStrategies.mockResolvedValue(feed([strategy({ instance_id: 'mine', display_name: 'Only mine' })]))
    render(<EngineStrategyPicker onManage={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Only mine')).toBeInTheDocument())
    expect(screen.queryByText(/someone else/i)).not.toBeInTheDocument()
  })

  it('keeps a selected C2 strategy visible but disabled when the kill switch is off', async () => {
    api.getEngineStrategies.mockResolvedValue(feed([
      strategy({
        selected: true,
        selectable: false,
        status: 'C2_FEATURE_DISABLED',
        blocking_reason: 'C2_FEATURE_DISABLED',
      }),
    ], 'inst-1'))
    render(<EngineStrategyPicker onManage={vi.fn()} />)
    expect((await screen.findAllByText('Bollinger + RSI')).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/feature disabled/i).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /select strategy/i })).not.toBeInTheDocument()
    expect(api.setEngineSelection).not.toHaveBeenCalled()
  })
})
