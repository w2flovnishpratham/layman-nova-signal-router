import { backendHttpUrl } from '../lib/backend'

// Mirrors backend/app/services/credentials_overview.py.
// Read-only and already masked server-side: no endpoint here returns a secret.
export interface CredentialsOverview {
  ok: boolean
  broker: {
    key: string
    name: string
    connected: boolean
    client_id_masked: string | null
    has_client_id: boolean
    has_access_token: boolean
    has_api_secret: boolean
    has_webhook_secret: boolean
    token_saved_at: string | null
    token_expires_at: string | null
    expiry_known: boolean
    last_updated_at: string | null
  }
  static_ip: { available: boolean; status: string; reason?: string; verified_at?: string | null; ip?: string | null }
  eligibility: { paper: boolean; paper_reason: string | null; live: boolean; live_blockers: string[] }
  mode: { dhan_mode: string; live_orders_enabled: boolean }
  supported_brokers: string[]
}

export async function getCredentialsOverview(): Promise<CredentialsOverview> {
  const response = await fetch(backendHttpUrl('/api/credentials/overview'), {
    credentials: 'include',
    cache: 'no-store',
  })
  if (!response.ok) throw new Error(`Could not load credentials: ${response.status}`)
  return response.json() as Promise<CredentialsOverview>
}

/** Re-runs the broker token check. Returns the server's own verdict. */
export async function verifyBroker(): Promise<{ success: boolean; message: string }> {
  const response = await fetch(backendHttpUrl('/api/broker/dhan/test'), {
    method: 'POST',
    credentials: 'include',
  })
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok) throw new Error(String(body.message ?? `Verification failed: ${response.status}`))
  return { success: Boolean(body.success), message: String(body.message ?? '') }
}

export async function disconnectBroker(): Promise<void> {
  const response = await fetch(backendHttpUrl('/api/user/credentials'), {
    method: 'DELETE',
    credentials: 'include',
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as Record<string, unknown>
    throw new Error(String(body.detail ?? `Disconnect failed: ${response.status}`))
  }
}
