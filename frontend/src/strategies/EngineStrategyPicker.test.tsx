import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EngineStrategy } from '../api'
import { EngineStrategyPicker } from './EngineStrategyPicker'

const api = vi.hoisted(() => ({ getEngineStrategies: vi.fn() }))
vi.mock('../api', () => ({ getEngineStrategies: api.getEngineStrategies }))

function strategy(overrides: Partial<EngineStrategy> = {}): EngineStrategy {
  return {
    instance_id: 'inst-1', display_name: 'Bollinger + RSI', source_type: 'PERSONAL_TRADINGVIEW',
    setup_type: 'NOVA_MANAGED_TRADINGVIEW', status: 'READY', instance_status: 'ready',
    selectable: true, blocking_reason: null, owner: 'self', ...overrides,
  }
}

beforeEach(() => vi.clearAllMocks())
afterEach(() => cleanup())

describe('EngineStrategyPicker', () => {
  it('lists a verified strategy as selectable and reports its instance id on select', async () => {
    api.getEngineStrategies.mockResolvedValue([strategy()])
    const onManage = vi.fn()
    const user = userEvent.setup()
    render(<EngineStrategyPicker onManage={onManage} />)
    await screen.findByText('Bollinger + RSI')
    expect(screen.getByText('READY')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /select & manage/i }))
    expect(onManage).toHaveBeenCalledWith('inst-1')
  })

  it('keeps an unverified strategy out of the selectable list and shows the blocker', async () => {
    api.getEngineStrategies.mockResolvedValue([
      strategy({ instance_id: 'inst-2', display_name: 'Half-set up', selectable: false, status: 'PAPER_EXIT_NOT_VERIFIED', blocking_reason: 'PAPER_EXIT_NOT_VERIFIED' }),
    ])
    render(<EngineStrategyPicker onManage={vi.fn()} />)
    await screen.findByText('Half-set up')
    expect(screen.getByText('No verified strategies yet')).toBeInTheDocument()
    expect(screen.getByText(/waiting for a confirmed paper exit/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /select & manage/i })).not.toBeInTheDocument()
  })

  it('only renders strategies the owner-scoped API returned (no other users)', async () => {
    api.getEngineStrategies.mockResolvedValue([strategy({ instance_id: 'mine', display_name: 'Only mine' })])
    render(<EngineStrategyPicker onManage={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Only mine')).toBeInTheDocument())
    expect(screen.queryByText(/someone else/i)).not.toBeInTheDocument()
  })
})
