import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StrategySetupField } from '../api'
import { useConversation } from './useConversation'

const FIELDS: StrategySetupField[] = [
  { key: 'direction', type: 'choice', label: 'Direction', options: ['CE', 'PE', 'BOTH'], required: true },
  { key: 'lots', type: 'integer', label: 'Lots', minimum: 1, maximum: 10, required: true },
]

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  vi.runOnlyPendingTimers()
  vi.useRealTimers()
})

describe('useConversation timer lifecycle', () => {
  it('advances from typing to the active question after the managed timer fires', () => {
    const { result } = renderHook(() => useConversation({ typingMs: 500 }))
    act(() => result.current.selectMode('paper'))
    act(() => result.current.selectStrategy('supertrend', FIELDS, {}))
    expect(result.current.state.phase).toBe('ASSISTANT_TYPING')
    act(() => vi.advanceTimersByTime(500))
    expect(result.current.state.phase).toBe('QUESTION_ACTIVE')
    expect(result.current.state.activeQuestionKey).toBe('direction')
  })

  it('reduced motion resolves the typing pause immediately (no timer)', () => {
    const { result } = renderHook(() => useConversation({ reducedMotion: true }))
    act(() => result.current.selectMode('paper'))
    act(() => result.current.selectStrategy('supertrend', FIELDS, {}))
    expect(result.current.state.phase).toBe('QUESTION_ACTIVE')
  })

  it('a superseding answer cancels the prior typing timer — no double advance', () => {
    const { result } = renderHook(() => useConversation({ typingMs: 500 }))
    act(() => result.current.selectMode('paper'))
    act(() => result.current.selectStrategy('supertrend', FIELDS, {}))
    act(() => vi.advanceTimersByTime(500)) // -> QUESTION_ACTIVE (direction)
    act(() => result.current.commitAnswer('direction', 'CE')) // -> ASSISTANT_TYPING (lots), new generation
    const genAfterCommit = result.current.state.generation
    act(() => vi.advanceTimersByTime(500))
    expect(result.current.state.phase).toBe('QUESTION_ACTIVE')
    expect(result.current.state.activeQuestionKey).toBe('lots')
    expect(result.current.state.generation).toBe(genAfterCommit) // exactly one advance, no extra
  })

  it('cancels the pending typing timer on unmount', () => {
    const clearSpy = vi.spyOn(globalThis, 'clearTimeout')
    const { result, unmount } = renderHook(() => useConversation({ typingMs: 500 }))
    act(() => result.current.selectMode('paper'))
    act(() => result.current.selectStrategy('supertrend', FIELDS, {}))
    expect(result.current.state.phase).toBe('ASSISTANT_TYPING')
    unmount()
    expect(clearSpy).toHaveBeenCalled()
    // Advancing time after unmount must not throw or dispatch.
    act(() => vi.advanceTimersByTime(1000))
    clearSpy.mockRestore()
  })
})
