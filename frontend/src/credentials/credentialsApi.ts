import { backendHttpUrl } from '../lib/backend'

// Mirrors backend/app/services/credentials_overview.py.
// Read-only and already masked server-side: no endpoint here returns a secret.
export type DhanConnectionStatus =
  | 'NOT_CONFIGURED'
  | 'CONNECTED'
  | 'INVALID_CREDENTIALS'
  | 'TOKEN_EXPIRED'
  | 'BROKER_UNAVAILABLE'
  | 'DISCONNECTED'
  | 'ERROR'

export interface CredentialsOverview {
  ok: boolean
  broker: {
    key: string
    name: string
    connected: boolean
    // Not a secret: the owner's own plaintext Client ID, for autofill.
    client_id: string | null
    client_id_masked: string | null
    has_client_id: boolean
    has_access_token: boolean
    has_webhook_secret: boolean
    token_saved_at: string | null
    token_expires_at: string | null
    expiry_known: boolean
    last_updated_at: string | null
    connection_status: DhanConnectionStatus
    last_verified_at: string | null
    last_verification_error: string | null
    last_wallet_snapshot_at: string | null
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

/** Field-level partial update: only non-empty fields are sent, so a blank
 * Access Token field never clears the stored token. */
export async function saveCredentials(fields: { clientId?: string; accessToken?: string }): Promise<void> {
  const payload: Record<string, string> = {}
  if (fields.clientId?.trim()) payload.dhan_client_id = fields.clientId.trim()
  if (fields.accessToken?.trim()) payload.dhan_access_token = fields.accessToken.trim()
  if (Object.keys(payload).length === 0) return
  const response = await fetch(backendHttpUrl('/api/user/credentials'), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as Record<string, unknown>
    throw new Error(String(body.detail ?? `Save failed: ${response.status}`))
  }
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
