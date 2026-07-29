import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RuntimeStatus } from '../../api'
import { ConversationController } from './ConversationController'

// The setup conversation also asks the engine safety questions (daily loss
// cap, trade cap and entry cutoff), so a 'complete' saved setup must
// include them or Resume correctly lands on the first unanswered one.
const FIELDS = [
  { key: 'direction', type: 'choice' as const, label: 'Direction', options: ['CE', 'PE', 'BOTH'], required: true, default: 'BOTH' },
  { key: 'lots', type: 'integer' as const, label: 'Lots', minimum: 1, maximum: 10, required: true, default: 1 },
]

function runtimeWith(saved: Record<string, unknown>): RuntimeStatus {
  return {
    owner_user_id: 'owner-1',
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
    runtime: runtimeWith({ direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, entry_cutoff_ist: '15:15' }), loading: false, error: '',
    onManage: () => {}, onSelect: vi.fn(noop), onSave: vi.fn(noop), onStart: vi.fn(noop),
    onUserReply: () => {}, ...over,
  }
}

beforeEach(() => { vi.useFakeTimers(); sessionStorage.clear() })
afterEach(() => { vi.runOnlyPendingTimers(); vi.useRealTimers(); cleanup() })

function runtimeNoMode(): RuntimeStatus {
  const r = runtimeWith({}) as unknown as { strategy_catalog: { selected_strategy_key: string | null; selected_strategy_instance_id: string | null; setup_progress: { mode: string | null } } }
  r.strategy_catalog.selected_strategy_key = null
  r.strategy_catalog.selected_strategy_instance_id = null
  r.strategy_catalog.setup_progress.mode = null
  return r as unknown as RuntimeStatus
}

function choosePaperAndStrategy() {
  fireEvent.click(screen.getByRole('button', { name: /start in paper/i }))
  fireEvent.click(screen.getByRole('button', { name: /Supertrend/i }))
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
    expect(onModeSelect).toHaveBeenCalledWith('paper', 1000000)
    expect(screen.queryByText('How should NOVA trade for you?')).toBeNull() // advanced past mode
    expect(screen.getByText('Which strategy should NOVA run?')).toBeInTheDocument()
  })

  it('does not silently replay a persisted backend mode or strategy', () => {
    const callbacks = props()
    render(<ConversationController {...callbacks} />)
    expect(screen.getByText('How should NOVA trade for you?')).toBeInTheDocument()
    expect(screen.queryByText('Which signals should NOVA trade??')).not.toBeInTheDocument()
    expect(callbacks.onSelect).not.toHaveBeenCalled()
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

describe('ConversationController Live start acknowledgement', () => {
  it('requires a fresh acknowledgement before every Live start attempt', async () => {
    const runtime = runtimeNoMode() as unknown as {
      strategy_catalog: {
        strategies: Array<{
          live_eligible: boolean
          saved_setup: Record<string, Record<string, unknown>>
        }>
      }
    }
    runtime.strategy_catalog.strategies[0].live_eligible = true
    runtime.strategy_catalog.strategies[0].saved_setup.live = {
      direction: 'CE',
      lots: 2,
      max_daily_loss: 25000,
      max_trades_per_day: 6,
      entry_cutoff_ist: '15:15',
    }
    const onStart = vi.fn(noop)
    render(
      <ConversationController
        {...props({
          runtime: runtime as unknown as RuntimeStatus,
          liveAvailable: true,
          onStart,
        })}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /configure live/i }))
    fireEvent.click(screen.getByRole('button', { name: /supertrend/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save setup/i }))
    })
    const start = screen.getByRole('button', { name: /start live/i })
    expect(start).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(start).toBeEnabled()
    await act(async () => fireEvent.click(start))
    expect(onStart).toHaveBeenCalledWith('inst-1', true)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
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
    choosePaperAndStrategy()
    expect(screen.getByText(/can't be configured right now/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save setup/i })).toBeNull()
  })

  it('halts an unavailable strategy with its reason and no Save', () => {
    const r = runtimeWith({ direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, entry_cutoff_ist: '15:15' }) as unknown as { strategy_catalog: { strategies: Array<{ availability: string; paper_eligible: boolean; disabled_reason: string }> } }
    r.strategy_catalog.strategies[0].availability = 'DISABLED'
    r.strategy_catalog.strategies[0].paper_eligible = false
    r.strategy_catalog.strategies[0].disabled_reason = 'Strategy paused by admin.'
    render(<ConversationController {...props({ runtime: r as unknown as RuntimeStatus })} />)
    choosePaperAndStrategy()
    expect(screen.getByText('Strategy paused by admin.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save setup/i })).toBeNull()
  })
})

describe('ConversationController — edit answer & async safety', () => {
  it('editing a field prefills its current value and hides Start until re-save', async () => {
    render(<ConversationController {...props()} />)
    choosePaperAndStrategy()
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700)) // review
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /save setup/i })) })
    expect(screen.getByRole('button', { name: /start paper/i })).toBeInTheDocument()
    // Edit the direction field.
    fireEvent.click(screen.getByRole('button', { name: /edit direction/i }))
    act(() => vi.advanceTimersByTime(700))
    // Its current value is prefilled (CE pressed) and Start engine is gone.
    expect(screen.getByRole('button', { name: 'CE' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('button', { name: /start paper/i })).toBeNull()
  })

  it('ignores a save that resolves after the user edited (no stale saved state)', async () => {
    let resolveSave: (() => void) | undefined
    const onSave = vi.fn(() => new Promise<void>((res) => { resolveSave = res }))
    render(<ConversationController {...props({ onSave })} />)
    choosePaperAndStrategy()
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    fireEvent.click(screen.getByRole('button', { name: /save setup/i })) // save in flight
    fireEvent.click(screen.getByRole('button', { name: /edit direction/i })) // generation bumps
    await act(async () => { resolveSave?.() }) // stale resolution
    act(() => vi.advanceTimersByTime(700))
    expect(screen.queryByRole('button', { name: /start paper/i })).toBeNull() // not exposed by stale save
  })
})

describe('ConversationController — saved setup decision', () => {
  it('hydrates the complete canonical revision, including risk fields', () => {
    const runtime = runtimeWith({
      direction: 'BOTH',
      lots: 1,
      stop_loss_percent: 10,
      take_profit_percent: 20,
    }) as RuntimeStatus & { mode: 'paper' }
    runtime.mode = 'paper'
    runtime.engine = { mode: 'paper' } as RuntimeStatus['engine']
    runtime.selected_configuration = {
      id: 'config-1',
      strategy_instance_id: 'inst-1',
      strategy_version_id: 'version-1',
      mode: 'paper',
      revision: 7,
      status: 'active',
      configuration: {
        direction: 'BOTH',
        lots: 1,
        stop_loss_percent: 10,
        take_profit_percent: 20,
      },
      risk: {
        max_daily_loss: 25_000,
        max_trades_per_day: 6,
        entry_cutoff_ist: '15:15',
      },
      committed_at: '2026-07-28T10:00:00Z',
    }

    render(<ConversationController {...props({ runtime })} />)

    expect(screen.getByText("What's your maximum loss for one day? The engine hard-stops and squares off if it's hit.")).toBeInTheDocument()
    expect(screen.getByText('25000')).toBeInTheDocument()
    expect(screen.getByText('15:15')).toBeInTheDocument()
  })

  it('shows the saved-setup decision with Resume / Review / Start New, not answer bubbles', () => {
    const p = props()
    render(<ConversationController {...p} />)
    choosePaperAndStrategy()
    expect(screen.getByText('I found your previous Paper configuration.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start New Setup' })).toBeInTheDocument()
    // saved values appear in the summary, NOT as user-answer bubbles
    expect(screen.queryByText('Direction: CE')).toBeNull()
    // Reading saved values does not save or start anything; the only backend
    // action is the user's explicit strategy selection.
    expect(p.onSave).not.toHaveBeenCalled()
    expect(p.onStart).not.toHaveBeenCalled()
    expect(p.onSelect).toHaveBeenCalledWith('supertrend')
  })

  it('Resume on a complete saved setup goes to the review card with a Save action', () => {
    render(<ConversationController {...props()} />)
    choosePaperAndStrategy()
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    expect(screen.getByRole('button', { name: /save setup/i })).toBeInTheDocument()
  })

  it('Start New Setup returns to Step 1 (Choose Mode) and does not overwrite the saved setup', () => {
    const p = props()
    render(<ConversationController {...p} />)
    choosePaperAndStrategy()
    fireEvent.click(screen.getByRole('button', { name: 'Start New Setup' }))
    // Back at mode selection -- not a fresh run of questions for the same
    // mode/strategy, and not the saved-setup decision card either.
    expect(screen.getByText('How should NOVA trade for you?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start in paper/i })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Direction' })).toBeNull()
    expect(p.onSave).not.toHaveBeenCalled() // saved setup untouched
  })

  it('Start New Setup -> pick the same mode again does not re-loop back to the saved-setup card', () => {
    // Regression: the restore effect depends on state.mode, so it re-ran
    // whenever pickMode changed it -- including for the user's own
    // interactive click, not just the genuine mount-time restore. With a
    // matching backend selected_configuration, mounting correctly
    // auto-restores straight to the saved-setup decision card (expected --
    // this IS "refresh restores your session"). But from there, clicking
    // Start New Setup resets to mode choice, and picking the SAME mode again
    // re-triggered the exact same auto-restore, landing right back on the
    // saved-setup card the user just explicitly tried to get away from --
    // an infinite Start-New/Resume loop with no way to actually reach a
    // fresh strategy pick.
    const runtime = runtimeWith({
      direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, entry_cutoff_ist: '15:15',
    }) as RuntimeStatus & { mode: 'paper' }
    runtime.mode = 'paper'
    runtime.engine = { mode: 'paper' } as RuntimeStatus['engine']
    runtime.selected_configuration = {
      id: 'config-1', strategy_instance_id: 'inst-1', strategy_version_id: 'version-1',
      mode: 'paper', revision: 3, status: 'active',
      configuration: { direction: 'CE', lots: 2 },
      risk: { max_daily_loss: 25000, max_trades_per_day: 6, entry_cutoff_ist: '15:15' },
      committed_at: '2026-07-28T10:00:00Z',
    }

    render(<ConversationController {...props({ runtime })} />)

    // Mount auto-restores to the saved-setup card -- expected.
    expect(screen.getByText('I found your previous Paper configuration.')).toBeInTheDocument()

    // Start New Setup -> back to mode choice.
    fireEvent.click(screen.getByRole('button', { name: 'Start New Setup' }))
    expect(screen.getByText('How should NOVA trade for you?')).toBeInTheDocument()

    // Pick the SAME mode the backend already has saved. Must land on
    // strategy selection, NOT loop back to the saved-setup decision card.
    fireEvent.click(screen.getByRole('button', { name: /start in paper/i }))
    expect(screen.getByText('Which strategy should NOVA run?')).toBeInTheDocument()
    expect(screen.queryByText('I found your previous Paper configuration.')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull()
  })

  it('picking a strategy with real saved answers during Start New Setup goes to fresh questions, not the saved-setup card again', () => {
    // One level deeper than the mode-level loop above: pickStrategy always
    // passed the strategy's genuine saved_setup into the machine, so
    // re-picking the same strategy after Start New Setup immediately
    // re-triggered "I found your previous configuration" -- no matter how
    // many times the user backed out, picking the strategy they actually
    // want brings the old card right back. Start New Setup must mean fresh
    // answers for whichever strategy is picked next, not just a fresh mode.
    const p = props() // Supertrend has a real saved setup in this fixture
    render(<ConversationController {...p} />)
    choosePaperAndStrategy() // lands on the saved-setup decision card

    fireEvent.click(screen.getByRole('button', { name: 'Start New Setup' }))
    fireEvent.click(screen.getByRole('button', { name: /start in paper/i }))
    fireEvent.click(screen.getByRole('button', { name: /Supertrend/i }))
    act(() => vi.advanceTimersByTime(700))

    expect(screen.queryByText('I found your previous Paper configuration.')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull()
    expect(screen.getByRole('group', { name: 'Direction' })).toBeInTheDocument()
    expect(p.onSave).not.toHaveBeenCalled()
  })
})

describe('ConversationController — sequential questions & save/start', () => {
  it('asks one question at a time and reaches review after all answers', () => {
    render(<ConversationController {...props({ runtime: runtimeWith({}) })} />) // no saved -> straight to questions
    choosePaperAndStrategy()
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
    for (const [label, value] of [['max_daily_loss', '25000'], ['max_trades_per_day', '6']] as const) {
      const numeric = document.getElementById(`q-${label}`) as HTMLInputElement
      expect(numeric).not.toBeNull()
      fireEvent.change(numeric, { target: { value } })
      fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
      act(() => vi.advanceTimersByTime(700))
    }
    fireEvent.click(screen.getByRole('button', { name: '15:15' }))
    act(() => vi.advanceTimersByTime(700))
    expect(screen.getByRole('button', { name: /save setup/i })).toBeInTheDocument()
  })

  it('saves the validated draft, then exposes an explicit Start engine (never before save)', async () => {
    const onSave = vi.fn(noop)
    const onStart = vi.fn(noop)
    render(<ConversationController {...props({ onSave, onStart })} />)
    choosePaperAndStrategy()
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    expect(screen.queryByRole('button', { name: /start paper/i })).toBeNull() // not before save
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /save setup/i })) })
    // Both halves travel in one call, so they commit as one revision.
    expect(onSave).toHaveBeenCalledWith(
      'supertrend',
      { direction: 'CE', lots: 2 },
      { max_daily_loss: 25000, max_trades_per_day: 6, entry_cutoff_ist: '15:15' },
    )
    expect(screen.getByRole('button', { name: /start paper/i })).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /start paper/i })) })
    expect(onStart).toHaveBeenCalledWith('inst-1', false)
  })

  it('a rapid double-click on Save produces exactly one save request', async () => {
    const onSave = vi.fn(() => new Promise<void>(() => {})) // stays pending
    render(<ConversationController {...props({ onSave })} />)
    choosePaperAndStrategy()
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    const btn = screen.getByRole('button', { name: /save setup/i })
    fireEvent.click(btn)
    fireEvent.click(btn) // second click before the disabled state re-renders
    expect(onSave).toHaveBeenCalledTimes(1)
  })

  it('a failed save keeps the prior setup and does not expose Start engine', async () => {
    const onSave = vi.fn(async () => { throw new Error('save rejected') })
    render(<ConversationController {...props({ onSave })} />)
    choosePaperAndStrategy()
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    act(() => vi.advanceTimersByTime(700))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /save setup/i })) })
    expect(screen.getByRole('alert')).toHaveTextContent('save rejected')
    expect(screen.queryByRole('button', { name: /start paper/i })).toBeNull()
  })
})
