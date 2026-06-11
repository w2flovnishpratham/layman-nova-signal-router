import type { AuthStatus, SessionBootstrap, SessionSnapshot } from './types'
import { backendHttpUrl } from './lib/backend'

const credentialedFetch: RequestInit = { credentials: 'include' }

export async function getAuthStatus(): Promise<AuthStatus> {
  const response = await fetch(backendHttpUrl('/api/auth/status'), credentialedFetch)
  if (!response.ok) {
    throw new Error(`Could not load auth status: ${response.status}`)
  }
  return response.json() as Promise<AuthStatus>
}

export async function startSession(): Promise<SessionBootstrap> {
  const response = await fetch(backendHttpUrl('/api/session/start'), { method: 'POST', credentials: 'include' })
  if (!response.ok) {
    throw new Error(`Could not start session: ${response.status}`)
  }
  return response.json() as Promise<SessionBootstrap>
}

export async function getSession(sessionId: string): Promise<SessionSnapshot> {
  const response = await fetch(backendHttpUrl(`/api/session/${sessionId}`), credentialedFetch)
  if (!response.ok) {
    throw new Error(`Could not load session: ${response.status}`)
  }
  return response.json() as Promise<SessionSnapshot>
}

export async function prepareReconfigure(): Promise<void> {
  const response = await fetch(backendHttpUrl('/api/engine/reconfigure'), { method: 'POST', credentials: 'include' })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string | { message?: string } } | null
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail : detail?.message
    throw new Error(message || `Could not reconfigure: ${response.status}`)
  }
}

export async function logout(): Promise<void> {
  await fetch(backendHttpUrl('/api/auth/logout'), { method: 'POST', credentials: 'include' })
}

export function googleLoginUrl(): string {
  return backendHttpUrl('/api/auth/google')
}
