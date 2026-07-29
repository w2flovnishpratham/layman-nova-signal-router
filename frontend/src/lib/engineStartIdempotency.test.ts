import { describe, expect, it } from 'vitest'
import { attemptAfterFailure, attemptKeyFor } from './engineStartIdempotency'

describe('attemptKeyFor', () => {
  it('mints a new key when there is no in-flight attempt', () => {
    const attempt = attemptKeyFor(null, 'rev-1', () => 'key-a')
    expect(attempt).toEqual({ revisionId: 'rev-1', key: 'key-a' })
  })

  it('reuses the same key when retrying the same revision', () => {
    const first = attemptKeyFor(null, 'rev-1', () => 'key-a')
    const retry = attemptKeyFor(first, 'rev-1', () => 'key-b')
    expect(retry).toBe(first) // same object, same key
    expect(retry.key).toBe('key-a')
  })

  it('mints a fresh key for a different revision (a genuinely new operation)', () => {
    const first = attemptKeyFor(null, 'rev-1', () => 'key-a')
    const next = attemptKeyFor(first, 'rev-2', () => 'key-b')
    expect(next).toEqual({ revisionId: 'rev-2', key: 'key-b' })
  })
})

describe('attemptAfterFailure', () => {
  const attempt = { revisionId: 'rev-1', key: 'key-a' }

  it('keeps the attempt after a network-level failure (ambiguous outcome)', () => {
    expect(attemptAfterFailure(attempt, new TypeError('Failed to fetch'))).toBe(attempt)
  })

  it('clears the attempt after a definitive server error (safe to mint a fresh key next time)', () => {
    expect(attemptAfterFailure(attempt, new Error('Live acknowledgement required'))).toBeNull()
  })

  it('clears the attempt for a non-Error rejection too', () => {
    expect(attemptAfterFailure(attempt, 'some string rejection')).toBeNull()
  })
})
