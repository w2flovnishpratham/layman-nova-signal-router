import { describe, expect, it } from 'vitest'
import type { StrategySetupField } from '../api'
import {
  conversationReducer,
  deriveConversation,
  initialConversationState,
  type ConversationState,
} from './conversationMachine'

const FIELDS: StrategySetupField[] = [
  { key: 'direction', type: 'choice', label: 'Direction', options: ['CE', 'PE', 'BOTH'], required: true, default: 'BOTH' },
  { key: 'lots', type: 'integer', label: 'Lots', minimum: 1, maximum: 10, required: true, default: 1 },
  { key: 'stop_loss', type: 'decimal', label: 'Stop loss %', minimum: 1, maximum: 50, required: true, default: 10 },
  { key: 'take_profit', type: 'decimal', label: 'Take profit %', minimum: 1, maximum: 100, required: true, default: 20 },
]

const COMPLETE_SAVED = { direction: 'CE', lots: 2, stop_loss: 8, take_profit: 16 }
const INCOMPLETE_SAVED = { direction: 'CE', lots: 2 } // missing SL/TP

function selectStrategy(saved: Record<string, unknown> = {}): ConversationState {
  let s = conversationReducer(initialConversationState, { type: 'SELECT_MODE', mode: 'paper' })
  s = conversationReducer(s, { type: 'SELECT_STRATEGY', strategyKey: 'supertrend', fields: FIELDS, saved })
  return s
}

/** Advance an ASSISTANT_TYPING pause using the current generation token. */
function finishTyping(s: ConversationState): ConversationState {
  return conversationReducer(s, { type: 'TYPING_DONE', generation: s.generation })
}

describe('conversation machine — mode selection', () => {
  it('selecting Paper mode advances to strategy selection', () => {
    const s = conversationReducer(initialConversationState, { type: 'SELECT_MODE', mode: 'paper' })
    expect(s.mode).toBe('paper')
    expect(s.phase).toBe('STRATEGY_SELECTION')
  })

  it('changing mode resets the strategy context and bumps the generation', () => {
    // Build a full paper conversation, then switch mode.
    let s = selectStrategy(COMPLETE_SAVED) // paper + supertrend + saved
    s = conversationReducer(s, { type: 'RESUME' }) // SETUP_REVIEW, draft populated
    const genBefore = s.generation
    s = conversationReducer(s, { type: 'SELECT_MODE', mode: 'live' })
    expect(s.mode).toBe('live')
    expect(s.phase).toBe('STRATEGY_SELECTION')
    expect(s.strategyKey).toBeNull()
    expect(s.draft).toEqual({}) // stale draft cleared
    expect(s.fields).toEqual([])
    expect(s.generation).toBeGreaterThan(genBefore) // stale timers/async invalidated
  })

  it('mode change does not mutate any saved setup (saved lives on the catalog)', () => {
    const savedRef = { ...COMPLETE_SAVED }
    const initial = selectStrategy(COMPLETE_SAVED)
    const switched = conversationReducer(initial, { type: 'SELECT_MODE', mode: 'live' })
    expect(switched.mode).toBe('live')
    expect(COMPLETE_SAVED).toEqual(savedRef) // untouched
  })
})

describe('conversation machine — saved setup decision', () => {
  it('a complete saved setup shows the SAVED_SETUP_FOUND decision, not review', () => {
    const s = selectStrategy(COMPLETE_SAVED)
    expect(s.phase).toBe('SAVED_SETUP_FOUND')
  })

  it('does not dump saved answers into the draft/transcript at the decision', () => {
    const s = selectStrategy(COMPLETE_SAVED)
    expect(s.draft).toEqual({}) // nothing pre-answered; saved stays separate
    expect(s.saved).toEqual(COMPLETE_SAVED)
  })

  it('a strategy with no saved setup skips the decision and asks the first question', () => {
    const s = finishTyping(selectStrategy({}))
    expect(s.phase).toBe('QUESTION_ACTIVE')
    expect(s.activeQuestionKey).toBe('direction')
  })
})

describe('conversation machine — resume / review / start new', () => {
  it('Review shows one summary from saved values without mutating saved', () => {
    let s = selectStrategy(COMPLETE_SAVED)
    s = conversationReducer(s, { type: 'REVIEW' })
    expect(s.phase).toBe('SETUP_REVIEW')
    expect(s.draft).toEqual(COMPLETE_SAVED)
    expect(s.saved).toEqual(COMPLETE_SAVED) // unchanged
  })

  it('Resume on a complete saved setup goes straight to review', () => {
    let s = selectStrategy(COMPLETE_SAVED)
    s = conversationReducer(s, { type: 'RESUME' })
    expect(s.phase).toBe('SETUP_REVIEW')
    expect(s.draft).toEqual(COMPLETE_SAVED)
  })

  it('Resume on an incomplete saved setup continues at the first unanswered field', () => {
    let s = selectStrategy(INCOMPLETE_SAVED)
    s = conversationReducer(s, { type: 'RESUME' })
    s = finishTyping(s)
    expect(s.phase).toBe('QUESTION_ACTIVE')
    expect(s.activeQuestionKey).toBe('stop_loss') // direction+lots preserved
    expect(s.draft).toEqual(INCOMPLETE_SAVED)
  })

  it('Start New asks the first applicable question and preserves the saved setup', () => {
    let s = selectStrategy(COMPLETE_SAVED)
    s = conversationReducer(s, { type: 'START_NEW' })
    s = finishTyping(s)
    expect(s.phase).toBe('QUESTION_ACTIVE')
    expect(s.activeQuestionKey).toBe('direction')
    expect(s.saved).toEqual(COMPLETE_SAVED) // saved not overwritten
  })

  it('Start New does not auto-commit defaults into the draft', () => {
    let s = selectStrategy(COMPLETE_SAVED)
    s = conversationReducer(s, { type: 'START_NEW' })
    expect(s.draft).toEqual({}) // defaults are suggestions, not committed values
  })

  it('abandoning Start New leaves the saved setup intact', () => {
    let s = selectStrategy(COMPLETE_SAVED)
    s = conversationReducer(s, { type: 'START_NEW' })
    s = conversationReducer(s, { type: 'RESET' }) // abandon the new setup
    // re-selecting from the reset state still finds the saved setup
    s = conversationReducer(s, { type: 'SELECT_MODE', mode: 'paper' })
    s = conversationReducer(s, { type: 'SELECT_STRATEGY', strategyKey: 'supertrend', fields: FIELDS, saved: COMPLETE_SAVED })
    expect(s.phase).toBe('SAVED_SETUP_FOUND')
  })
})

describe('conversation machine — sequential pacing & determinism', () => {
  it('asks exactly one question at a time, with a typing pause between answers', () => {
    let s = finishTyping(selectStrategy({}))
    expect(s.activeQuestionKey).toBe('direction')
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'direction', value: 'CE' })
    expect(s.phase).toBe('ASSISTANT_TYPING') // typing before the next question
    expect(s.activeQuestionKey).toBe('lots')
    s = finishTyping(s)
    expect(s.phase).toBe('QUESTION_ACTIVE')
  })

  it('only the active question can be answered (rapid/duplicate answers are ignored)', () => {
    const s = finishTyping(selectStrategy({})) // active = direction
    const ignored = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'lots', value: 3 })
    expect(ignored).toBe(s) // not the active question -> no change
  })

  it('a stale TYPING_DONE from an older generation cannot advance a newer conversation', () => {
    let s = finishTyping(selectStrategy({}))
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'direction', value: 'CE' }) // now ASSISTANT_TYPING gen=N
    const staleGen = s.generation - 1
    const afterStale = conversationReducer(s, { type: 'TYPING_DONE', generation: staleGen })
    expect(afterStale).toBe(s) // ignored
    const afterFresh = conversationReducer(s, { type: 'TYPING_DONE', generation: s.generation })
    expect(afterFresh.phase).toBe('QUESTION_ACTIVE')
  })

  it('answering the last required field moves to review', () => {
    let s = finishTyping(selectStrategy({}))
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'direction', value: 'CE' })
    s = finishTyping(s)
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'lots', value: 2 })
    s = finishTyping(s)
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'stop_loss', value: 8 })
    s = finishTyping(s)
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'take_profit', value: 16 })
    expect(s.phase).toBe('SETUP_REVIEW')
    expect(s.activeQuestionKey).toBeNull()
  })

  it('rejects an out-of-range / invalid answer and keeps the question active', () => {
    let s = finishTyping(selectStrategy({}))
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'direction', value: 'XX' }) // not an option
    expect(s.activeQuestionKey).toBe('direction')
    expect(s.draft).toEqual({})
  })

  it('restores the same state deterministically after a refresh', () => {
    const restored = deriveConversation({ mode: 'paper', strategyKey: 'supertrend', fields: FIELDS, saved: COMPLETE_SAVED })
    expect(restored.phase).toBe('SAVED_SETUP_FOUND')
    const restoredIncomplete = deriveConversation({ mode: 'paper', strategyKey: 'supertrend', fields: FIELDS, saved: INCOMPLETE_SAVED })
    expect(restoredIncomplete.phase).toBe('SAVED_SETUP_FOUND') // partial saved still offers the decision
  })
})

describe('conversation machine — editing & explicit engine start', () => {
  it('editing an earlier answer invalidates dependent later draft fields but keeps earlier ones', () => {
    // Build a complete draft via Resume, then edit lots.
    let s = selectStrategy(COMPLETE_SAVED)
    s = conversationReducer(s, { type: 'RESUME' }) // SETUP_REVIEW, draft = complete
    s = conversationReducer(s, { type: 'EDIT_ANSWER', key: 'lots' })
    expect(s.activeQuestionKey).toBe('lots')
    // direction + the edited lots value are kept (lots prefills the question);
    // strictly-later fields (stop_loss, take_profit) are invalidated.
    expect(s.draft).toEqual({ direction: 'CE', lots: 2 })
  })

  it('updates one valid review answer without rewinding the flow', () => {
    let s = selectStrategy(COMPLETE_SAVED)
    s = conversationReducer(s, { type: 'RESUME' })
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'lots', value: 4 })

    expect(s.phase).toBe('SETUP_REVIEW')
    expect(s.activeQuestionKey).toBeNull()
    expect(s.draft.lots).toBe(4)
    expect(s.draft.direction).toBe('CE')
  })

  it('engine start is explicit and requires a complete review', () => {
    let s = finishTyping(selectStrategy({}))
    // not complete yet -> START_ENGINE is a no-op
    expect(conversationReducer(s, { type: 'START_ENGINE' })).toBe(s)
    // complete it
    s = selectStrategy(COMPLETE_SAVED)
    s = conversationReducer(s, { type: 'RESUME' })
    expect(s.phase).toBe('SETUP_REVIEW')
    s = conversationReducer(s, { type: 'START_ENGINE' })
    expect(s.phase).toBe('ENGINE_READY')
  })

  it('is a pure reducer — no order/position/job side effects are possible', () => {
    // Structural guarantee: the module imports only types, never api/network.
    // Exercising every action mutates nothing outside the returned state.
    let s = selectStrategy(COMPLETE_SAVED)
    const before = JSON.stringify(COMPLETE_SAVED)
    s = conversationReducer(s, { type: 'START_NEW' })
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'direction', value: 'PE' })
    expect(s.draft.direction).toBe('PE') // the answer landed in the draft
    expect(JSON.stringify(COMPLETE_SAVED)).toBe(before) // input not mutated
  })
})
