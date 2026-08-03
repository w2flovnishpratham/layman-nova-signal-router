// Idempotency-key policy for POST /api/runtime/start-selected. NOVA's backend
// durably claims (user_id, idempotency_key) and permanently replays whatever
// outcome that key first recorded -- including a failure -- so reusing a key
// is only safe when the previous outcome is genuinely ambiguous (a network
// failure where we never learned whether the server processed the request).
// Reusing it after a definitive server response (success OR a clean error)
// would either replay a stale success for a new attempt, or permanently
// block retrying past an old rejection. See App.tsx's startTradingStrategy.

export interface EngineStartAttempt {
  revisionId: string
  key: string
}

/** The key to send for this call. Reuses the in-flight attempt's key only
 * when retrying the exact same configuration revision; a different revision
 * is a genuinely new operation and always gets a fresh key. */
export function attemptKeyFor(
  current: EngineStartAttempt | null,
  revisionId: string,
  makeKey: () => string = () => crypto.randomUUID(),
): EngineStartAttempt {
  if (current?.revisionId === revisionId) return current
  return { revisionId, key: makeKey() }
}

/** What to keep after a failed call. A network-level failure (no response
 * was ever received) is ambiguous -- keep the attempt so a retry reuses the
 * same key. Any other error means the server gave a definitive answer;
 * clear it so the next attempt mints a fresh key instead of being
 * permanently stuck replaying that answer. */
export function attemptAfterFailure(
  attempt: EngineStartAttempt,
  error: unknown,
): EngineStartAttempt | null {
  return error instanceof TypeError ? attempt : null
}
