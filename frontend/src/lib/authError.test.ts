import { describe, expect, it, beforeEach } from 'vitest'
import { authErrorMessage, consumeAuthErrorFromUrl, resetAuthErrorForTest } from './authError'

function setUrl(url: string) {
  window.history.replaceState({}, '', url)
}

beforeEach(() => resetAuthErrorForTest())

describe('authErrorMessage', () => {
  it('has copy for every reason the OAuth callback can redirect with', () => {
    // These are the exact codes backend/app/auth/google.py sends.
    for (const reason of ['expired', 'provider', 'invalid_request', 'email_unverified', 'server']) {
      expect(authErrorMessage(reason)).not.toBe('')
    }
  })

  it('never shows the user the word CSRF', () => {
    // The state-mismatch case is nearly always a stale retry, and the raw
    // backend message used to render as {"detail": "Invalid OAuth state
    // (possible CSRF)."} on a bare API page.
    expect(authErrorMessage('expired')).not.toMatch(/csrf/i)
  })

  it('falls back to a usable message for an unknown reason', () => {
    expect(authErrorMessage('something_new')).toBe(authErrorMessage('server'))
  })

  it('reports nothing when there is no reason', () => {
    expect(authErrorMessage(null)).toBe('')
    expect(authErrorMessage('')).toBe('')
  })
})

describe('consumeAuthErrorFromUrl', () => {
  beforeEach(() => setUrl('/'))

  it('reads the reason out of the query string', () => {
    setUrl('/?auth_error=expired')
    expect(consumeAuthErrorFromUrl()).toBe(authErrorMessage('expired'))
  })

  it('strips the param so a refresh does not resurrect the banner', () => {
    setUrl('/?auth_error=expired')
    consumeAuthErrorFromUrl()

    expect(window.location.search).toBe('')

    // A real reload restarts the module, dropping the in-memory message. With
    // the param gone from the URL there is nothing left to report -- the banner
    // is shown once, for the attempt that actually failed.
    resetAuthErrorForTest()
    expect(consumeAuthErrorFromUrl()).toBe('')
  })

  it('leaves unrelated query params alone', () => {
    setUrl('/?tab=reports&auth_error=provider')
    consumeAuthErrorFromUrl()

    expect(window.location.search).toBe('?tab=reports')
  })

  it('reports nothing on a normal visit', () => {
    setUrl('/')
    expect(consumeAuthErrorFromUrl()).toBe('')
  })

  it('survives the landing page -> /app hop that drops the query string', () => {
    // The real flow: the callback redirects to "/", which renders the landing
    // page. main.tsx reads the reason there, but the sign-in screen only mounts
    // after the visitor enters /app/*, and that hop is a pushState carrying no
    // query string. Reading it a second time must still return the message.
    setUrl('/?auth_error=expired')
    expect(consumeAuthErrorFromUrl()).toBe(authErrorMessage('expired'))

    window.history.pushState({}, '', '/app/trading')

    expect(consumeAuthErrorFromUrl()).toBe(authErrorMessage('expired'))
  })
})
