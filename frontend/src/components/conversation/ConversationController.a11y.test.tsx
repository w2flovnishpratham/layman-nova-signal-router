import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RuntimeStatus } from '../../api'
import { ConversationController } from './ConversationController'

// Real timers + userEvent: keyboard-only operation of the real controller.
// The setup conversation also asks the engine safety questions (daily loss
// cap, trade cap, cooldown after a loss), so a 'complete' saved setup must
// include them or Resume correctly lands on the first unanswered one.
const FIELDS = [
  { key: 'direction', type: 'choice' as const, label: 'Direction', options: ['CE', 'PE', 'BOTH'], required: true, default: 'BOTH' },
  { key: 'lots', type: 'integer' as const, label: 'Lots', minimum: 1, maximum: 10, required: true, default: 1 },
]

function runtime({ saved = {}, mode = 'paper', selected = true }: { saved?: Record<string, unknown>; mode?: string | null; selected?: boolean } = {}): RuntimeStatus {
  return {
    strategy_catalog: {
      strategies: [{
        strategy_key: 'supertrend', strategy_instance_id: 'inst-1', source_type: 'BUILT_IN',
        name: 'Supertrend', version: '1', description: 'Trend follower',
        availability: 'READY', disabled_reason: null, paper_eligible: true, live_eligible: false,
        selected: true, runtime_state: 'IDLE', setup_schema: { fields: FIELDS }, saved_setup: { paper: saved },
      }],
      selected_strategy_key: selected ? 'supertrend' : null,
      selected_strategy_instance_id: selected ? 'inst-1' : null,
      setup_progress: { strategy_key: 'supertrend', mode, stage: 'REVIEW', complete: true },
    },
  } as unknown as RuntimeStatus
}

const noop = async () => {}
function props(over: Record<string, unknown> = {}) {
  return { runtime: runtime(), loading: false, error: '', onManage: () => {}, onSelect: vi.fn(noop), onSave: vi.fn(noop), onStart: vi.fn(noop), onUserReply: () => {}, ...over }
}

afterEach(() => cleanup())

describe('ConversationController — keyboard-only operation', () => {
  it('selects Paper mode with the keyboard and advances', async () => {
    const user = userEvent.setup()
    const onModeSelect = vi.fn()
    render(<ConversationController {...props({ runtime: runtime({ mode: null, selected: false }), onModeSelect })} />)
    const paper = screen.getByRole('button', { name: /start in paper/i })
    paper.focus()
    await user.keyboard('{Enter}')
    expect(onModeSelect).toHaveBeenCalledWith('paper', 100000)
    expect(await screen.findByText('Which strategy should NOVA run?')).toBeInTheDocument()
  })

  it('cannot keyboard-activate unavailable Live', async () => {
    const user = userEvent.setup()
    const onModeSelect = vi.fn()
    render(<ConversationController {...props({ runtime: runtime({ mode: null, selected: false }), onModeSelect, liveAvailable: false })} />)
    const live = screen.getByRole('button', { name: /live unavailable/i })
    expect(live).toBeDisabled() // disabled buttons are skipped by tab and ignore Enter
    live.focus()
    await user.keyboard('{Enter}')
    expect(onModeSelect).not.toHaveBeenCalled()
  })

  it('operates the saved-setup decision and choice question by keyboard', async () => {
    const user = userEvent.setup()
    render(<ConversationController {...props({ runtime: runtime({ saved: { direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, cooldown_after_loss_minutes: 30 } }) })} />)
    const startNew = screen.getByRole('button', { name: 'Start New Setup' })
    startNew.focus()
    await user.keyboard('{Enter}')
    // Choice question appears after the typing pause; pick PE by keyboard.
    const pe = await screen.findByRole('button', { name: 'PE' })
    pe.focus()
    await user.keyboard('{Enter}')
    // one user answer recorded for direction
    expect(await screen.findByText('Direction: PE')).toBeInTheDocument()
  })

  it('enters a numeric answer and reaches Save, then keyboard Save→Start', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn(noop)
    const onStart = vi.fn(noop)
    render(<ConversationController {...props({ runtime: runtime({ saved: { direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, cooldown_after_loss_minutes: 30 } }), onSave, onStart })} />)
    ;(screen.getByRole('button', { name: 'Resume' })).focus()
    await user.keyboard('{Enter}')
    const save = await screen.findByRole('button', { name: /save setup/i })
    save.focus()
    await user.keyboard('{Enter}')
    const start = await screen.findByRole('button', { name: /start engine/i })
    start.focus()
    await user.keyboard('{Enter}')
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onStart).toHaveBeenCalledTimes(1)
  })

  it('associates a validation error with the field and does not advance on an invalid value', async () => {
    const user = userEvent.setup()
    render(<ConversationController {...props({ runtime: runtime({ saved: {}, selected: true }) })} />) // fresh questions
    // direction first (choice) -> pick CE to reach lots (numeric)
    const ce = await screen.findByRole('button', { name: 'CE' })
    await user.click(ce)
    const input = await screen.findByLabelText('Lots')
    await user.type(input, '99') // out of range (max 10)
    await user.keyboard('{Enter}')
    const err = screen.getByRole('alert')
    expect(err).toHaveTextContent(/between 1 and 10/i)
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAttribute('aria-describedby', err.id)
    expect(screen.queryByRole('button', { name: /save setup/i })).toBeNull() // did not advance
  })
})

describe('ConversationController — ARIA live region', () => {
  it('exposes exactly one polite live region announcing the latest prompt, not the transcript', () => {
    render(<ConversationController {...props({ runtime: runtime({ saved: { direction: 'CE', lots: 2, max_daily_loss: 25000, max_trades_per_day: 6, cooldown_after_loss_minutes: 30 } }) })} />)
    const statuses = screen.getAllByRole('status').filter((el) => el.getAttribute('aria-live') === 'polite')
    // one dedicated conversation live region (others may exist for unrelated widgets)
    const live = statuses.find((el) => /NOVA/.test(el.textContent ?? ''))
    expect(live).toBeTruthy()
    expect(within(live!).queryByText('Direction: CE')).toBeNull() // not the whole transcript
  })
})
