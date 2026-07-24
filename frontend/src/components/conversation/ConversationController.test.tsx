import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RuntimeStatus } from '../../api'
import { ConversationController } from './ConversationController'

// The setup conversation also asks the engine safety questions (daily loss
// cap, trade cap, cooldown after a loss), so a 'complete' saved setup must
// include them or Resume correctly lands on the first unanswered one.
const FIELDS = [
  { key: 'direction', type: 'choice' as const, label: 'Direction', options: ['CE', 'PE', 'BOTH'], required: true, default: 'BOTH' },
  { key: 'lots', type: 'integer' as const, label: 'Lots', minimum: 1, maximum: 10, required: true, default: 1 },
]

function runtimeWith(saved: Record<string, unknown>): RuntimeStatus {
  return {
    strategy_catalog: {
      strategies: [{
        strategy_key: 'supertrend', strategy_instance_id: 'inst-1', source_type: 'BUILT_IN',
        name: 'Supertrend', version: '1', description: 'Trend follower',
        availability: 'READY', disabled_reason: null, paper_eligible: true, live_eligible: false,
        selected: true, runtime_state: 'IDLE', setup_schema: { fields: FIELDS }, saved_setup: { paper: saved },
      }],
      selected_strategy_key: 'supertrend', selected_strategy_instance_id: 'inst-1',
      setup_progress: { strategy_key: 'supertrend', mode: 'paper', stage: 'REVIEW', complete: true },
    },
  } as unknown as RuntimeStatus
}

const noop = async () => {}
function props(over: Partial<Parameters<typeof ConversationController>[0]> = {}) {
  return {
    runtime: runtimeWith({ direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, cooldown_after_loss_minutes: 30 }), loading: false, error: '',
    onManage: () => {}, onSelect: vi.fn(noop), onSave: vi.fn(noop), onStart: vi.fn(noop),
    onUserReply: () => {}, ...over,
  }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => { vi.runOnlyPendingTimers(); vi.useRealTimers(); cleanup() })

function runtimeNoMode(): RuntimeStatus {
  const r = runtimeWith({}) as unknown as { strategy_catalog: { selected_strategy_key: string | null; selected_strategy_instance_id: string | null; setup_progress: { mode: string | null } } }
  r.strategy_catalog.selected_strategy_key = null
  r.strategy_catalog.selected_strategy_instance_id = null
  r.strategy_catalog.setup_progress.mode = null
  return r as unknown as RuntimeStatus
}

describe('ConversationController — mode selection in the machine', () => {
  it('renders Paper/Live mode selection when the backend has no mode yet', () => {
    render(<ConversationController {...props({ runtime: runtimeNoMode() })} />)
    expect(screen.getByText('How should NOVA trade for you?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start in paper/i })).toBeInTheDocument()
  })

  it('selecting Paper syncs the backend and advances into strategy selection', () => {
    const onModeSelect = vi.fn()
    render(<ConversationController {...props({ runtime: runtimeNoMode(), onModeSelect })} />)
    fireEvent.click(screen.getByRole('button', { name: /start in paper/i }))
    expect(onModeSelect).toHaveBeenCalledWith('paper', 100000)
    expect(screen.queryByText('How should NOVA trade for you?')).toBeNull() // advanced past mode
    expect(screen.getByText('Which strategy should NOVA run?')).toBeInTheDocument()
  })

  it('Live is disabled and cannot advance when unavailable', () => {
    const onModeSelect = vi.fn()
    render(<ConversationController {...props({ runtime: runtimeNoMode(), onModeSelect, liveAvailable: false })} />)
    const live = screen.getByRole('button', { name: /live unavailable/i })
    expect(live).toBeDisabled()
    fireEvent.click(live)
    expect(onModeSelect).not.toHaveBeenCalled()
    expect(screen.getByText('How should NOVA trade for you?')).toBeInTheDocument() // did not advance
  })
})

describe('ConversationController — error recovery', () => {
  it('shows a catalog error with a working Retry and no strategies', () => {
    const onRetry = vi.fn()
    render(<ConversationController {...props({ error: 'Catalog failed to load.', onRetry })} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Catalog failed to load.')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('blocks setup (no Save/Start) when the selected strategy has no schema', () => {
    const r = runtimeWith({}) as unknown as { strategy_catalog: { strategies: Array<{ setup_schema: { fields: unknown[] } }> } }
    r.strategy_catalog.strategies[0].setup_schema = { fields: [] } // missing schema
    render(<ConversationController {...props({ runtime: r as unknown as RuntimeStatus })} />)
    expect(screen.getByText(/can't be configured right now/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save setup/i })).toBeNull()
  })

  it('halts an unavailable strategy with its reason and no Save', () => {
    const r = runtimeWith({ direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, cooldown_after_loss_minutes: 30 }) as unknown as { strategy_catalog: { strategies: Array<{ availability: string; paper_eligible: boolean; disabled_reason: string }> } }
    r.strategy_catalog.strategies[0].availability = 'DISABLED'
    r.strategy_catalog.strategies[0].paper_eligible = false
    r.strategy_catalog.strategies[0].disabled_reason = 'Strategy paused by admin.'
    render(<ConversationController {...props({ runtime: r as unknown as RuntimeStatus })} />)
    expect(screen.getByText('Strategy paused by admin.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save setup/i })).toBeNull()
  })
})

describe('ConversationController — edit answer & async safety', () => {
  it('editing a field prefills its current value and hides Start until re-save', async () => {
    render(<ConversationController {...props()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700)) // review
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /save setup/i })) })
    expect(screen.getByRole('button', { name: /start engine/i })).toBeInTheDocument()
    // Edit the direction field.
    fireEvent.click(screen.getByRole('button', { name: /edit direction/i }))
    act(() => vi.advanceTimersByTime(700))
    // Its current value is prefilled (CE pressed) and Start engine is gone.
    expect(screen.getByRole('button', { name: 'CE' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('button', { name: /start engine/i })).toBeNull()
  })

  it('ignores a save that resolves after the user edited (no stale saved state)', async () => {
    let resolveSave: (() => void) | undefined
    const onSave = vi.fn(() => new Promise<void>((res) => { resolveSave = res }))
    render(<ConversationController {...props({ onSave })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    fireEvent.click(screen.getByRole('button', { name: /save setup/i })) // save in flight
    fireEvent.click(screen.getByRole('button', { name: /edit direction/i })) // generation bumps
    await act(async () => { resolveSave?.() }) // stale resolution
    act(() => vi.advanceTimersByTime(700))
    expect(screen.queryByRole('button', { name: /start engine/i })).toBeNull() // not exposed by stale save
  })
})

describe('ConversationController — saved setup decision', () => {
  it('shows the saved-setup decision with Resume / Review / Start New, not answer bubbles', () => {
    const p = props()
    render(<ConversationController {...p} />)
    expect(screen.getByText('I found your previous Paper configuration.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start New Setup' })).toBeInTheDocument()
    // saved values appear in the summary, NOT as user-answer bubbles
    expect(screen.queryByText('Direction: CE')).toBeNull()
    // hydration must not call any backend API
    expect(p.onSave).not.toHaveBeenCalled()
    expect(p.onStart).not.toHaveBeenCalled()
    expect(p.onSelect).not.toHaveBeenCalled()
  })

  it('Resume on a complete saved setup goes to the review card with a Save action', () => {
    render(<ConversationController {...props()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    expect(screen.getByRole('button', { name: /save setup/i })).toBeInTheDocument()
  })

  it('Start New asks the first fresh question and does not overwrite the saved setup', () => {
    const p = props()
    render(<ConversationController {...p} />)
    fireEvent.click(screen.getByRole('button', { name: 'Start New Setup' }))
    act(() => vi.advanceTimersByTime(700))
    // first question is Direction, rendered as choice pills
    expect(screen.getByRole('group', { name: 'Direction' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'CE' })).toBeInTheDocument()
    expect(p.onSave).not.toHaveBeenCalled() // saved setup untouched
  })
})

describe('ConversationController — sequential questions & save/start', () => {
  it('asks one question at a time and reaches review after all answers', () => {
    render(<ConversationController {...props({ runtime: runtimeWith({}) })} />) // no saved -> straight to questions
    act(() => vi.advanceTimersByTime(700))
    // Direction active
    fireEvent.click(screen.getByRole('button', { name: 'CE' }))
    act(() => vi.advanceTimersByTime(700))
    // Lots active (numeric)
    const input = screen.getByLabelText('Lots') as HTMLInputElement
    fireEvent.change(input, { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    act(() => vi.advanceTimersByTime(700))
    // The engine safety questions follow the strategy schema.
    for (const [label, value] of [['max_daily_loss', '25000'], ['max_trades_per_day', '6'], ['cooldown_after_loss_minutes', '30']] as const) {
      const numeric = document.getElementById(`q-${label}`) as HTMLInputElement
      expect(numeric).not.toBeNull()
      fireEvent.change(numeric, { target: { value } })
      fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
      act(() => vi.advanceTimersByTime(700))
    }
    expect(screen.getByRole('button', { name: /save setup/i })).toBeInTheDocument()
  })

  it('saves the validated draft, then exposes an explicit Start engine (never before save)', async () => {
    const onSave = vi.fn(noop)
    const onStart = vi.fn(noop)
    const onSaveRisk = vi.fn(noop)
    render(<ConversationController {...props({ onSave, onSaveRisk, onStart })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    expect(screen.queryByRole('button', { name: /start engine/i })).toBeNull() // not before save
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /save setup/i })) })
    expect(onSave).toHaveBeenCalledWith('supertrend', { direction: 'CE', lots: 2 })
    // Safety settings are runtime settings, saved through their own endpoint.
    expect(onSaveRisk).toHaveBeenCalledWith({ max_daily_loss: 25000, max_trades_per_day: 6, cooldown_after_loss_minutes: 30 })
    expect(screen.getByRole('button', { name: /start engine/i })).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /start engine/i })) })
    expect(onStart).toHaveBeenCalledWith('inst-1')
  })

  it('a rapid double-click on Save produces exactly one save request', async () => {
    const onSave = vi.fn(() => new Promise<void>(() => {})) // stays pending
    render(<ConversationController {...props({ onSave })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    const btn = screen.getByRole('button', { name: /save setup/i })
    fireEvent.click(btn)
    fireEvent.click(btn) // second click before the disabled state re-renders
    await act(async () => { await Promise.resolve() }) // the risk save resolves first
    expect(onSave).toHaveBeenCalledTimes(1)
  })

  it('a failed save keeps the prior setup and does not expose Start engine', async () => {
    const onSave = vi.fn(async () => { throw new Error('save rejected') })
    render(<ConversationController {...props({ onSave })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /save setup/i })) })
    expect(screen.getByRole('alert')).toHaveTextContent('save rejected')
    expect(screen.queryByRole('button', { name: /start engine/i })).toBeNull()
  })
})
