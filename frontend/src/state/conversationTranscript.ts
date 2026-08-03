// Formal transcript projection: a deterministic VIEW of the conversation machine
// state. It never holds authority — answers live in state.draft, saved config on
// the catalog. IDs are stable across reconstruction (keyed by generation + type +
// field, never array index), so the same machine state always yields the same
// entries, and a mode/strategy change (which bumps generation) can never surface
// an old generation's entries.

import { applicableFields, type ConversationState } from './conversationMachine'

export type TranscriptEntryType =
  | 'nova_message'
  | 'user_answer'
  | 'typing'
  | 'active_question'
  | 'saved_setup_decision'
  | 'setup_review'
  | 'status'
  | 'error'

export interface TranscriptEntry {
  id: string
  type: TranscriptEntryType
  generation: number
  sequence: number
  fieldKey?: string
  payload: Record<string, unknown>
}

export function projectTranscript(state: ConversationState): TranscriptEntry[] {
  const entries: TranscriptEntry[] = []
  const gen = state.generation
  let seq = 0
  const push = (type: TranscriptEntryType, payload: Record<string, unknown>, fieldKey?: string) => {
    entries.push({ id: `${gen}:${type}:${fieldKey ?? seq}`, type, generation: gen, sequence: seq++, fieldKey, payload })
  }

  if (!state.mode) return entries // mode selection is interactive UI, not transcript

  if (state.phase === 'SAVED_SETUP_FOUND') {
    push('saved_setup_decision', { mode: state.mode, saved: state.saved })
    return entries
  }

  const fields = applicableFields(state.fields)
  // Answered questions (before the active one) become NOVA-question + user-answer
  // pairs, projected from the authoritative draft — never from saved values.
  for (const field of fields) {
    if (field.key === state.activeQuestionKey) break
    if (field.key in state.draft) {
      push('nova_message', { label: field.label, fieldKey: field.key }, field.key)
      push('user_answer', { label: field.label, value: state.draft[field.key] }, field.key)
    }
  }

  if (state.phase === 'ASSISTANT_TYPING') {
    push('typing', {}) // transient — only exists while typing
  } else if (state.phase === 'QUESTION_ACTIVE' && state.activeQuestionKey) {
    const field = fields.find((f) => f.key === state.activeQuestionKey)
    if (field) {
      push('nova_message', { label: field.label, fieldKey: field.key }, `q-${field.key}`)
      push('active_question', { fieldKey: field.key }, field.key)
    }
  } else if (state.phase === 'SETUP_REVIEW' || state.phase === 'ENGINE_READY') {
    push('setup_review', { draft: state.draft })
  }

  return entries
}
