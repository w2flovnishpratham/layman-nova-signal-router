import { backendHttpUrl } from '../lib/backend'

/** Broker connection status. Read-only and already masked by the server —
    stored secrets are never returned by this endpoint. */
export interface BrokerStatus {
  broker_name: string
  has_dhan_client_id: boolean
  dhan_client_id_masked: string | null
  has_dhan_access_token: boolean
  has_dhan_api_secret: boolean
  has_webhook_secret: boolean
  mode: string
  dhan_token_saved_at: string | null
  updated_at: string | null
}

export async function getBrokerStatus(): Promise<BrokerStatus> {
  const response = await fetch(backendHttpUrl('/api/user/credentials/status'), {
    credentials: 'include',
    cache: 'no-store',
  })
  if (!response.ok) throw new Error(`Could not read broker status: ${response.status}`)
  return response.json() as Promise<BrokerStatus>
}
