import { useCallback, useEffect, useReducer, useRef } from 'react'
import type { StrategySetupField } from '../api'
import type { EngineMode } from '../types'
import {
  conversationReducer,
  initialConversationState,
  type ConversationAction,
  type ConversationState,
  type SetupValues,
} from './conversationMachine'

const DEFAULT_TYPING_MS = 650

/**
 * Controller hook around the pure conversation machine. It owns the single
 * managed typing timer (Phase 8): whenever the machine enters ASSISTANT_TYPING
 * it schedules exactly one TYPING_DONE for the CURRENT generation, and any state
 * change (a new answer, edit, reset, or unmount) cancels that timer first. A
 * stale conversation's timer therefore can never advance a newer one — and the
 * machine's generation guard rejects it even if a timer somehow escaped.
 *
 * Reduced-motion users skip the pause (immediate transition) without changing
 * any state semantics.
 */
export function useConversation(options?: {
  typingMs?: number
  reducedMotion?: boolean
  initialState?: ConversationState
}) {
  const [state, dispatch] = useReducer(
    conversationReducer,
    options?.initialState ?? initialConversationState,
  )
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const typingMs = options?.reducedMotion ? 0 : options?.typingMs ?? DEFAULT_TYPING_MS

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => {
    clearTimer()
    if (state.phase !== 'ASSISTANT_TYPING') return
    const generation = state.generation
    if (typingMs <= 0) {
      dispatch({ type: 'TYPING_DONE', generation })
      return
    }
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      dispatch({ type: 'TYPING_DONE', generation })
    }, typingMs)
    return clearTimer
  }, [state.phase, state.generation, typingMs, clearTimer])

  // Cancel any pending timer when the component using the conversation unmounts.
  useEffect(() => clearTimer, [clearTimer])

  // Thin action creators so callers don't hand-build action objects.
  const selectMode = useCallback((mode: EngineMode) => dispatch({ type: 'SELECT_MODE', mode }), [])
  const selectStrategy = useCallback(
    (strategyKey: string, fields: StrategySetupField[], saved?: SetupValues) =>
      dispatch({ type: 'SELECT_STRATEGY', strategyKey, fields, saved }),
    [],
  )
  const resume = useCallback(() => dispatch({ type: 'RESUME' }), [])
  const review = useCallback(() => dispatch({ type: 'REVIEW' }), [])
  const startNew = useCallback(() => dispatch({ type: 'START_NEW' }), [])
  const commitAnswer = useCallback((key: string, value: unknown) => dispatch({ type: 'COMMIT_ANSWER', key, value }), [])
  const editAnswer = useCallback((key: string) => dispatch({ type: 'EDIT_ANSWER', key }), [])
  const startEngine = useCallback(() => dispatch({ type: 'START_ENGINE' }), [])
  const reset = useCallback(() => dispatch({ type: 'RESET' }), [])

  const send = useCallback((action: ConversationAction) => dispatch(action), [])

  return { state, send, selectMode, selectStrategy, resume, review, startNew, commitAnswer, editAnswer, startEngine, reset }
}
