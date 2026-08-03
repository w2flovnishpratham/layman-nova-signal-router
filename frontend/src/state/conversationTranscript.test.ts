import { describe, expect, it } from 'vitest'
import type { StrategySetupField } from '../api'
import { conversationReducer, initialConversationState, type ConversationState } from './conversationMachine'
import { projectTranscript } from './conversationTranscript'

const FIELDS: StrategySetupField[] = [
  { key: 'direction', type: 'choice', label: 'Direction', options: ['CE', 'PE', 'BOTH'], required: true },
  { key: 'lots', type: 'integer', label: 'Lots', minimum: 1, maximum: 10, required: true },
]

function fresh(): ConversationState {
  let s = conversationReducer(initialConversationState, { type: 'SELECT_MODE', mode: 'paper' })
  s = conversationReducer(s, { type: 'SELECT_STRATEGY', strategyKey: 'st', fields: FIELDS, saved: {} })
  return conversationReducer(s, { type: 'TYPING_DONE', generation: s.generation }) // QUESTION_ACTIVE: direction
}
function finishTyping(s: ConversationState) {
  return conversationReducer(s, { type: 'TYPING_DONE', generation: s.generation })
}

describe('transcript projection', () => {
  it('never emits a transcript for mode selection', () => {
    const s = conversationReducer(initialConversationState, { type: 'RESET' })
    expect(projectTranscript(s)).toEqual([])
  })

  it('emits a single saved_setup_decision (saved values are not user answers)', () => {
    let s = conversationReducer(initialConversationState, { type: 'SELECT_MODE', mode: 'paper' })
    s = conversationReducer(s, { type: 'SELECT_STRATEGY', strategyKey: 'st', fields: FIELDS, saved: { direction: 'CE', lots: 2 } })
    const t = projectTranscript(s)
    expect(t.filter((e) => e.type === 'saved_setup_decision')).toHaveLength(1)
    expect(t.filter((e) => e.type === 'user_answer')).toHaveLength(0) // no saved-value dump
  })

  it('has at most one typing entry and at most one active_question entry', () => {
    const typingState = conversationReducer(fresh(), { type: 'COMMIT_ANSWER', key: 'direction', value: 'CE' }) // ASSISTANT_TYPING
    const t1 = projectTranscript(typingState)
    expect(t1.filter((e) => e.type === 'typing')).toHaveLength(1)
    expect(t1.filter((e) => e.type === 'active_question')).toHaveLength(0)

    const activeState = finishTyping(typingState) // QUESTION_ACTIVE (lots)
    const t2 = projectTranscript(activeState)
    expect(t2.filter((e) => e.type === 'typing')).toHaveLength(0)
    expect(t2.filter((e) => e.type === 'active_question')).toHaveLength(1)
  })

  it('projects answered fields as nova+user pairs with stable IDs across reconstruction', () => {
    let s = fresh()
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'direction', value: 'CE' })
    s = finishTyping(s) // QUESTION_ACTIVE lots, direction answered
    const idsA = projectTranscript(s).filter((e) => e.fieldKey === 'direction').map((e) => e.id)
    const idsB = projectTranscript(s).filter((e) => e.fieldKey === 'direction').map((e) => e.id)
    expect(idsA).toEqual(idsB) // deterministic
    expect(idsA.length).toBe(2) // nova_message + user_answer
    expect(new Set(projectTranscript(s).map((e) => e.id)).size).toBe(projectTranscript(s).length) // unique
  })

  it('old-generation IDs cannot collide with new ones after a strategy/mode change', () => {
    const s1 = fresh()
    const t1 = projectTranscript(conversationReducer(s1, { type: 'COMMIT_ANSWER', key: 'direction', value: 'CE' }))
    const s2 = conversationReducer(s1, { type: 'SELECT_MODE', mode: 'live' }) // bumps generation, resets
    const t2 = projectTranscript(s2)
    const overlap = t1.filter((a) => t2.some((b) => b.id === a.id))
    expect(overlap).toHaveLength(0)
  })

  it('editing an earlier answer rebuilds only affected entries', () => {
    // Complete both, reach review, then edit the first answer.
    let s = fresh()
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'direction', value: 'CE' })
    s = finishTyping(s)
    s = conversationReducer(s, { type: 'COMMIT_ANSWER', key: 'lots', value: 3 }) // -> SETUP_REVIEW
    const beforeEdit = projectTranscript(s)
    expect(beforeEdit.some((e) => e.type === 'setup_review')).toBe(true)

    s = conversationReducer(s, { type: 'EDIT_ANSWER', key: 'direction' }) // clears direction + later
    const afterEdit = projectTranscript(s)
    // No user_answer entries remain (direction cleared, lots invalidated as a later field)
    expect(afterEdit.filter((e) => e.type === 'user_answer')).toHaveLength(0)
    expect(afterEdit.some((e) => e.type === 'setup_review')).toBe(false)
  })
})
