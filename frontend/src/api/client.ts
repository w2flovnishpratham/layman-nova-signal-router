import axios, { AxiosError } from 'axios'
import { toast } from 'sonner'

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '')
const baseURL = configuredBaseUrl ? `${configuredBaseUrl}/api` : '/api'

// FE-C6 — 10s timeout. Anything slower is almost always indicative of backend
// trouble; better to fail fast than hang the UI for minutes (the browser default).
const REQUEST_TIMEOUT_MS = 10_000

const api = axios.create({
  baseURL,
  timeout: REQUEST_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
})

// FE-C7 — 401 interceptor.
// If the backend returns 401 on any call, surface a single toast and steer
// the user to /app/setup to re-connect Dhan (token most likely expired).
// We avoid redirect-spam by only redirecting once per ~30s window.
let _redirected401 = false

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status

    if (status === 401) {
      if (!_redirected401) {
        _redirected401 = true
        toast.error('Session expired — please re-connect Dhan', {
          description: 'Your Dhan access token is no longer valid. Generate a new one and reconnect.',
          duration: 6000,
        })
        // Reset the redirect-once guard so subsequent intentional sign-in
        // attempts can still surface 401 errors normally.
        window.setTimeout(() => { _redirected401 = false }, 30_000)

        // Only redirect if we aren't already on a setup-related route.
        const path = window.location.pathname
        if (!path.startsWith('/app/setup') && !path.startsWith('/setup')) {
          window.location.href = '/app/setup'
        }
      }
    }

    return Promise.reject(error)
  },
)

export default api
