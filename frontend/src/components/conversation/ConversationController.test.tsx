import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RuntimeStatus } from '../../api'
import { ConversationController } from './ConversationController'

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
    runtime: runtimeWith({ direction: 'CE', lots: 2 }), loading: false, error: '',
    onManage: () => {}, onSelect: vi.fn(noop), onSave: vi.fn(noop), onStart: vi.fn(noop),
    onUserReply: () => {}, ...over,
  }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => { vi.runOnlyPendingTimers(); vi.useRealTimers(); cleanup() })

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
    expect(screen.getByRole('button', { name: /save setup/i })).toBeInTheDocument()
  })

  it('saves the validated draft, then exposes an explicit Start engine (never before save)', async () => {
    const onSave = vi.fn(noop)
    const onStart = vi.fn(noop)
    render(<ConversationController {...props({ onSave, onStart })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    expect(screen.queryByRole('button', { name: /start engine/i })).toBeNull() // not before save
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /save setup/i })) })
    expect(onSave).toHaveBeenCalledWith('supertrend', { direction: 'CE', lots: 2 })
    expect(screen.getByRole('button', { name: /start engine/i })).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /start engine/i })) })
    expect(onStart).toHaveBeenCalledWith('inst-1')
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
