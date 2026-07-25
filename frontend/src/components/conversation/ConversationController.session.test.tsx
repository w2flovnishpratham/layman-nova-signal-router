import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RuntimeStatus } from '../../api'
import { ConversationController } from './ConversationController'

// The conversation state lives inside the controller instance (useReducer), not a
// global store, and the authenticated /app subtree is unmounted on logout. These
// tests exercise that boundary: unmount == logout, a new mount == a new session.
// (Limitation: the full App/auth screen is not mounted here — App unmounts this
// exact subtree on logout, so an instance boundary is the faithful unit.)

// The setup conversation also asks the engine safety questions (daily loss
// cap, trade cap and entry cutoff), so a 'complete' saved setup must
// include them or Resume correctly lands on the first unanswered one.
const FIELDS = [
  { key: 'direction', type: 'choice' as const, label: 'Direction', options: ['CE', 'PE', 'BOTH'], required: true, default: 'BOTH' },
  { key: 'lots', type: 'integer' as const, label: 'Lots', minimum: 1, maximum: 10, required: true, default: 1 },
]

function runtimeFor(saved: Record<string, unknown>, instanceId = 'inst-1'): RuntimeStatus {
  return {
    strategy_catalog: {
      strategies: [{
        strategy_key: 'supertrend', strategy_instance_id: instanceId, source_type: 'BUILT_IN',
        name: 'Supertrend', version: '1', description: 'Trend follower',
        availability: 'READY', disabled_reason: null, paper_eligible: true, live_eligible: false,
        selected: true, runtime_state: 'IDLE', setup_schema: { fields: FIELDS }, saved_setup: { paper: saved },
      }],
      selected_strategy_key: 'supertrend', selected_strategy_instance_id: instanceId,
      setup_progress: { strategy_key: 'supertrend', mode: 'paper', stage: 'REVIEW', complete: true },
    },
  } as unknown as RuntimeStatus
}

const noop = async () => {}
function baseProps(over: Record<string, unknown> = {}) {
  return {
    runtime: runtimeFor({}), loading: false, error: '', onManage: () => {}, onSelect: vi.fn(noop), onSave: vi.fn(noop),
    onStart: vi.fn(noop), onUserReply: () => {}, ...over,
  }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => { vi.runOnlyPendingTimers(); vi.useRealTimers(); cleanup() })

describe('ConversationController — session isolation & logout', () => {
  it('a new session (mount) shows only the current owner\'s saved values, never a prior one', () => {
    const a = render(<ConversationController {...baseProps({ runtime: runtimeFor({ direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, entry_cutoff_ist: '15:15' }) })} />)
    expect(screen.getByText('CE')).toBeInTheDocument() // owner A summary
    a.unmount() // logout: authenticated subtree removed

    render(<ConversationController {...baseProps({ runtime: runtimeFor({ direction: 'PE', lots: 5 }, 'inst-2') })} />)
    // owner B sees only their own saved values; no leaked global conversation state
    expect(screen.getByText('PE')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.queryByText('CE')).toBeNull()
  })

  it('a save that resolves after logout cannot leak into the next session', async () => {
    let resolveA: (() => void) | undefined
    const onSaveA = vi.fn(() => new Promise<void>((res) => { resolveA = res }))
    const a = render(<ConversationController {...baseProps({ runtime: runtimeFor({ direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, entry_cutoff_ist: '15:15' }), onSave: onSaveA })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    fireEvent.click(screen.getByRole('button', { name: /save setup/i })) // A's save in flight
    a.unmount() // logout while save pending

    render(<ConversationController {...baseProps({ runtime: runtimeFor({}, 'inst-2') })} />) // owner B, fresh
    await act(async () => { resolveA?.() }) // A's stale save resolves after logout
    // B is unaffected: no Start engine fabricated, B is at its own decision/questions.
    expect(screen.queryByRole('button', { name: /start engine/i })).toBeNull()
  })

  it('an auth-expiry style save rejection keeps setup unsaved and never exposes Start engine', async () => {
    const onSave = vi.fn(async () => { throw new Error('401 Unauthorized: session expired') })
    const onStart = vi.fn(noop)
    render(<ConversationController {...baseProps({ runtime: runtimeFor({ direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, entry_cutoff_ist: '15:15' }), onSave, onStart })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /save setup/i })) })
    expect(screen.getByRole('alert')).toHaveTextContent(/session expired/i)
    expect(screen.queryByRole('button', { name: /start engine/i })).toBeNull()
    expect(onStart).not.toHaveBeenCalled() // no start on an expired save
  })

  it('navigating away during a pending save (unmount) does not throw when it resolves', async () => {
    let resolve: (() => void) | undefined
    const onSave = vi.fn(() => new Promise<void>((res) => { resolve = res }))
    const a = render(<ConversationController {...baseProps({ runtime: runtimeFor({ direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, entry_cutoff_ist: '15:15' }), onSave })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    fireEvent.click(screen.getByRole('button', { name: /save setup/i }))
    a.unmount() // navigate /app -> / (subtree unmounts)
    await act(async () => { resolve?.() }) // resolves after unmount — must be a safe no-op
    // reaching here without a thrown error is the assertion; timers already cleaned.
    expect(true).toBe(true)
  })
})
