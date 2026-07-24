import { backendHttpUrl } from '../lib/backend'

/** Engine safety settings (runtime settings, not per-strategy setup).
    PATCH so the wizard only ever writes the keys it actually asked about. */
export async function patchRiskSettings(values: Record<string, string | number>): Promise<void> {
  const response = await fetch(backendHttpUrl('/api/setup/risk'), {
    method: 'PATCH',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(values),
  })
  if (!response.ok) {
    throw new Error(
      response.status === 409
        ? 'Settings changed in another tab. Reload and re-apply your answers.'
        : `Safety settings could not be saved: ${response.status}`,
    )
  }
}

export interface BrokerStatus {
  has_dhan_client_id: boolean
  dhan_client_id_masked: string | null
  has_dhan_access_token: boolean
}

export async function getBrokerStatus(): Promise<BrokerStatus> {
  const response = await fetch(backendHttpUrl('/api/user/credentials/status'), {
    credentials: 'include',
    cache: 'no-store',
  })
  if (!response.ok) throw new Error(`Could not read broker status: ${response.status}`)
  return response.json() as Promise<BrokerStatus>
}
