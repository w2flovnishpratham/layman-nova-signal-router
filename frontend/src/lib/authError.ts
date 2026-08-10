/**
 * Turns the ?auth_error=<reason> code the OAuth callback redirects with into
 * something worth reading.
 *
 * The backend deliberately sends a short, non-identifying code rather than a
 * message: the real cause is logged server-side, and the callback is a URL the
 * user can see and share. Phrasing lives here so the copy can say what to do
 * next without the server leaking why it actually failed.
 */
const MESSAGES: Record<string, string> = {
  // Nearly always a stale retry -- the state cookie lives 10 minutes, and
  // going back to re-submit an old callback lands here. Deliberately not
  // phrased as "CSRF", which is alarming and tells the user nothing they can act on.
  expired: 'That sign-in link has expired. Start again to continue.',
  provider: 'Google could not complete the sign-in. Start again to continue.',
  invalid_request: 'That sign-in link was incomplete. Start again to continue.',
  email_unverified: 'Verify your email address with Google, then sign in again.',
  server: 'Sign-in is unavailable right now. Please try again in a moment.',
}

const FALLBACK = MESSAGES.server

export function authErrorMessage(reason: string | null | undefined): string {
  if (!reason) return ''
  return MESSAGES[reason] ?? FALLBACK
}

// Held across the gap between reading the URL and rendering the sign-in screen.
// The callback redirects to "/", which renders the landing page -- App (and the
// sign-in screen inside it) only mounts once the visitor enters /app/*, and
// that hop is a pushState which drops the query string. So the reason has to be
// taken off the URL at startup and kept here until there is something to show it.
let pending = ''
let consumed = false

/**
 * Reads the reason out of the URL and strips it, so the banner does not survive
 * a refresh or get carried into a shared link. Idempotent: the first call does
 * the stripping, every later call returns the same message. Returns '' when
 * there is nothing to report.
 */
export function consumeAuthErrorFromUrl(): string {
  if (typeof window === 'undefined') return ''
  if (consumed) return pending
  consumed = true

  const params = new URLSearchParams(window.location.search)
  const reason = params.get('auth_error')
  if (!reason) return ''

  params.delete('auth_error')
  const query = params.toString()
  window.history.replaceState(
    {},
    '',
    `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`,
  )
  pending = authErrorMessage(reason)
  return pending
}

/** Test hook -- the cache above is module state and outlives a single test. */
export function resetAuthErrorForTest(): void {
  pending = ''
  consumed = false
}
