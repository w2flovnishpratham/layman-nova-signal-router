const defaultBackendUrl = import.meta.env.PROD ? 'https://layman-api.manyacare.com' : ''
const configuredBackendUrl = import.meta.env.VITE_BACKEND_URL?.trim().replace(/[;/\s]+$/, '')
const backendUrl = configuredBackendUrl || defaultBackendUrl
const mutatingMethods = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
let csrfToken: string | null = null
let csrfBootstrap: Promise<void> | null = null

export function backendHttpUrl(path: `/${string}`): string {
  return backendUrl ? `${backendUrl}${path}` : path
}

export function backendWsUrl(path: `/${string}`): string {
  if (!backendUrl) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}${path}`
  }

  const websocketUrl = new URL(backendUrl)
  websocketUrl.protocol = websocketUrl.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${websocketUrl.origin}${path}`
}

export function setCsrfToken(token: string | null | undefined): void {
  csrfToken = token?.trim() || null
}

export async function apiFetch(path: `/${string}`, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || 'GET').toUpperCase()
  if (mutatingMethods.has(method) && !csrfToken) {
    await bootstrapCsrfToken()
  }

  const headers = new Headers(init.headers)
  if (mutatingMethods.has(method) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }

  const response = await fetch(backendHttpUrl(path), {
    ...init,
    method,
    headers,
    credentials: 'include',
  })
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent('nova:session-expired'))
  }
  return response
}

async function bootstrapCsrfToken(): Promise<void> {
  if (!csrfBootstrap) {
    csrfBootstrap = fetch(backendHttpUrl('/api/auth/status'), {
      method: 'GET',
      credentials: 'include',
      cache: 'no-store',
    })
      .then(async (response) => {
        if (!response.ok) return
        const body = await response.json() as { csrfToken?: string | null }
        setCsrfToken(body.csrfToken)
      })
      .finally(() => {
        csrfBootstrap = null
      })
  }
  await csrfBootstrap
}
