// Explicit NOVA setup-conversation state machine.
//
// This is the deterministic core the chatbot UI projects from. It is a pure
// reducer over authoritative data — the selected strategy's setup_schema fields
// and the per-mode saved_setup values — plus user actions. It never reads the
// rendered transcript and never mutates the saved setup, so the same inputs
// always yield the same state (a hard refresh restores it deterministically via
// deriveConversation()).
//
// Timers, scrolling and rendering live in the presentation layer; the only
// timing concept here is `generation`, an operation token that lets the UI
// reject a stale ASSISTANT_TYPING completion from an older conversation.

import type { StrategySetupField } from '../api'
import type { EngineMode } from '../types'

export type ConversationPhase =
  | 'WELCOME'
  | 'MODE_SELECTION'
  | 'SAVED_SETUP_FOUND'
  | 'STRATEGY_SELECTION'
  | 'ASSISTANT_TYPING'
  | 'QUESTION_ACTIVE'
  | 'SETUP_REVIEW'
  | 'ENGINE_READY'

export type SetupValues = Record<string, unknown>

export interface ConversationState {
  phase: ConversationPhase
  mode: EngineMode | null
  strategyKey: string | null
  /** Schema for the selected strategy (ordered). Authoritative field list. */
  fields: StrategySetupField[]
  /** Authoritative saved values for (strategy, mode). NEVER mutated by the machine. */
  saved: SetupValues
  /** In-progress answers for the active conversation. */
  draft: SetupValues
  /** The single interactive question, or null when none is active. */
  activeQuestionKey: string | null
  /** Where the current draft came from — resuming saved values or a fresh setup. */
  origin: 'resume' | 'start_new' | null
  /** Operation token; bumped on every transition that starts a typing pause so a
      stale TYPING_DONE from an older conversation cannot advance a newer one. */
  generation: number
}

export type ConversationAction =
  | { type: 'SELECT_MODE'; mode: EngineMode }
  | { type: 'SELECT_STRATEGY'; strategyKey: string; fields: StrategySetupField[]; saved?: SetupValues }
  | { type: 'RESUME' }
  | { type: 'REVIEW' }
  | { type: 'START_NEW' }
  | { type: 'COMMIT_ANSWER'; key: string; value: unknown }
  | { type: 'EDIT_ANSWER'; key: string }
  | { type: 'TYPING_DONE'; generation: number }
  | { type: 'START_ENGINE' }
  | { type: 'RESET' }

export const initialConversationState: ConversationState = {
  phase: 'WELCOME',
  mode: null,
  strategyKey: null,
  fields: [],
  saved: {},
  draft: {},
  activeQuestionKey: null,
  origin: null,
  generation: 0,
}

// --- Pure derivations -------------------------------------------------------

/** Applicable fields for the current schema. Dependencies aren't encoded in the
    schema today, so every field applies; this is the single extension point if
    conditional applicability is added later. */
export function applicableFields(fields: StrategySetupField[]): StrategySetupField[] {
  return fields
}

export function isValidValue(field: StrategySetupField, value: unknown): boolean {
  if (value === undefined || value === null || value === '') return false
  if (field.type === 'choice') return field.options.includes(String(value))
  const num = Number(value)
  if (!Number.isFinite(num)) return false
  if (field.type === 'integer' && !Number.isInteger(num)) return false
  return num >= field.minimum && num <= field.maximum
}

export function isAnswered(field: StrategySetupField, values: SetupValues): boolean {
  return isValidValue(field, values[field.key])
}

/** The first applicable field that is not yet validly answered, or null. */
export function firstUnansweredField(fields: StrategySetupField[], values: SetupValues): string | null {
  for (const field of applicableFields(fields)) {
    if (field.required && !isAnswered(field, values)) return field.key
  }
  // Optional fields still worth asking once, but they don't block completion.
  for (const field of applicableFields(fields)) {
    if (!field.required && !isAnswered(field, values)) return field.key
  }
  return null
}

/** A setup is complete when every required applicable field is validly answered. */
export function isSetupComplete(fields: StrategySetupField[], values: SetupValues): boolean {
  return applicableFields(fields).every((field) => !field.required || isAnswered(field, values))
}

/** Whether a usable saved configuration exists (at least one validly saved field). */
export function hasSavedSetup(fields: StrategySetupField[], saved: SetupValues): boolean {
  return applicableFields(fields).some((field) => isAnswered(field, saved))
}

/** Only the saved values that are actually valid for the current schema — lets a
    Resume preserve compatible values and drop stale/invalid ones (Phase 4). */
export function compatibleSavedValues(fields: StrategySetupField[], saved: SetupValues): SetupValues {
  const out: SetupValues = {}
  for (const field of applicableFields(fields)) {
    if (isAnswered(field, saved)) out[field.key] = saved[field.key]
  }
  return out
}

// --- Reducer ----------------------------------------------------------------

function beginQuestioning(state: ConversationState, draft: SetupValues, origin: 'resume' | 'start_new'): ConversationState {
  const next = firstUnansweredField(state.fields, draft)
  if (next === null) {
    return { ...state, draft, origin, activeQuestionKey: null, phase: 'SETUP_REVIEW', generation: state.generation + 1 }
  }
  return { ...state, draft, origin, activeQuestionKey: next, phase: 'ASSISTANT_TYPING', generation: state.generation + 1 }
}

export function conversationReducer(state: ConversationState, action: ConversationAction): ConversationState {
  switch (action.type) {
    case 'SELECT_MODE':
      // Mode controls which saved setup, eligibility and schema apply, so a
      // (re)selection resets the strategy context and bumps the generation —
      // cancelling any pending typing/async from the previous mode. Saved setups
      // live on the catalog per mode and are re-supplied on the next
      // SELECT_STRATEGY, so nothing authoritative is mutated here.
      return {
        ...state,
        mode: action.mode,
        phase: 'STRATEGY_SELECTION',
        strategyKey: null,
        fields: [],
        saved: {},
        draft: {},
        activeQuestionKey: null,
        origin: null,
        generation: state.generation + 1,
      }

    case 'SELECT_STRATEGY': {
      const saved = action.saved ?? {}
      const base: ConversationState = {
        ...state,
        strategyKey: action.strategyKey,
        fields: action.fields,
        saved,
        draft: {},
        origin: null,
        activeQuestionKey: null,
      }
      if (hasSavedSetup(action.fields, saved)) {
        return { ...base, phase: 'SAVED_SETUP_FOUND', generation: state.generation + 1 }
      }
      // No saved setup: go straight into a fresh sequential conversation.
      return beginQuestioning(base, {}, 'start_new')
    }

    case 'RESUME': {
      // Preserve only values still valid under the current schema.
      const draft = compatibleSavedValues(state.fields, state.saved)
      if (isSetupComplete(state.fields, draft)) {
        return { ...state, draft, origin: 'resume', activeQuestionKey: null, phase: 'SETUP_REVIEW', generation: state.generation + 1 }
      }
      return beginQuestioning(state, draft, 'resume')
    }

    case 'REVIEW':
      // Show the saved configuration summary read-only; saved is never mutated.
      return {
        ...state,
        draft: compatibleSavedValues(state.fields, state.saved),
        origin: 'resume',
        activeQuestionKey: null,
        phase: 'SETUP_REVIEW',
        generation: state.generation + 1,
      }

    case 'START_NEW':
      // A fresh draft. Saved values stay intact until an explicit, validated save.
      // Defaults are surfaced by the UI as suggestions but are NOT committed here.
      return beginQuestioning(state, {}, 'start_new')

    case 'COMMIT_ANSWER': {
      if (state.activeQuestionKey !== action.key) return state // only the active question is interactive
      const field = state.fields.find((f) => f.key === action.key)
      if (!field || !isValidValue(field, action.value)) return state // reject invalid; stay put
      const draft = { ...state.draft, [action.key]: action.value }
      const next = firstUnansweredField(state.fields, draft)
      if (next === null) {
        return { ...state, draft, activeQuestionKey: null, phase: 'SETUP_REVIEW', generation: state.generation + 1 }
      }
      return { ...state, draft, activeQuestionKey: next, phase: 'ASSISTANT_TYPING', generation: state.generation + 1 }
    }

    case 'EDIT_ANSWER': {
      const idx = state.fields.findIndex((f) => f.key === action.key)
      if (idx < 0) return state
      // Smallest safe invalidation: keep the edited field's current value (so the
      // UI can prefill it) plus all earlier independent answers, and clear every
      // LATER field — dependencies aren't encoded in the schema, so later answers
      // may depend on this one. The edited field becomes the active question and
      // is overwritten when the user re-commits it.
      const draft: SetupValues = {}
      state.fields.slice(0, idx + 1).forEach((f) => {
        if (f.key in state.draft) draft[f.key] = state.draft[f.key]
      })
      return { ...state, draft, activeQuestionKey: action.key, phase: 'ASSISTANT_TYPING', generation: state.generation + 1 }
    }

    case 'TYPING_DONE':
      if (state.phase !== 'ASSISTANT_TYPING' || action.generation !== state.generation) return state // stale
      return { ...state, phase: 'QUESTION_ACTIVE' }

    case 'START_ENGINE':
      if (state.phase !== 'SETUP_REVIEW') return state
      if (!isSetupComplete(state.fields, state.draft)) return state
      return { ...state, phase: 'ENGINE_READY' }

    case 'RESET':
      return { ...initialConversationState, generation: state.generation + 1 }

    default:
      return state
  }
}

/** Deterministically derive the conversation state from authoritative inputs —
    used to restore the same state after a refresh without replaying a transcript. */
export function deriveConversation(input: {
  mode: EngineMode | null
  strategyKey: string | null
  fields: StrategySetupField[]
  saved: SetupValues
}): ConversationState {
  let state: ConversationState = { ...initialConversationState }
  if (input.mode) state = conversationReducer(state, { type: 'SELECT_MODE', mode: input.mode })
  if (input.strategyKey) {
    state = conversationReducer(state, {
      type: 'SELECT_STRATEGY',
      strategyKey: input.strategyKey,
      fields: input.fields,
      saved: input.saved,
    })
  }
  return state
}
