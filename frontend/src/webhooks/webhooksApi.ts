import { backendHttpUrl } from '../lib/backend'
import type { SignalItem } from '../signals/signalsApi'

// Mirrors backend/app/services/webhooks_overview.py.
// There is ONE webhook secret per user, and it is never returned — only
// {set, masked, source}. Endpoints are the real inbound receivers, not a
// fabricated per-strategy list.
export interface WebhookEndpoint {
  key: string
  label: string
  url: string
  method: string
  description: string
}

export interface WebhooksOverview {
  ok: boolean
  available: boolean
  endpoints: WebhookEndpoint[]
  secret: { set: boolean; masked: string | null; source: string | null }
  window_hours: number
  deliveries: {
    counts: Record<string, number>
    signature_verified: number
    last_delivery_at: string | null
  }
  recent: SignalItem[]
}

export async function getWebhooksOverview(): Promise<WebhooksOverview> {
  const response = await fetch(backendHttpUrl('/api/webhooks/overview'), {
    credentials: 'include',
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(`Could not load webhooks: ${response.status}`)
  }
  return response.json() as Promise<WebhooksOverview>
}

export async function rotateWebhookSecret(): Promise<string> {
  const response = await fetch(backendHttpUrl('/api/webhooks/secret/rotate'), {
    method: 'POST',
    credentials: 'include',
    cache: 'no-store',
  })
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok) throw new Error(String(body.error ?? `Could not rotate secret: ${response.status}`))
  if (typeof body.secret !== 'string' || !body.secret) throw new Error('The server did not return a new secret.')
  return body.secret
}

// The single admin-run broadcast chart's secret -- shared by every NOVA_SHARED
// catalog strategy, distinct from the per-user secret above.
export interface ManagedStrategySecretMeta { set: boolean; masked: string | null; source: string | null }

export async function getManagedStrategyWebhookSecret(): Promise<ManagedStrategySecretMeta> {
  const response = await fetch(backendHttpUrl('/api/admin/strategy-webhook-secret'), {
    credentials: 'include',
    cache: 'no-store',
  })
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok || body.ok === false) throw new Error(String(body.error ?? `Could not load the managed secret: ${response.status}`))
  return body.secret as ManagedStrategySecretMeta
}

export async function revealManagedStrategyWebhookSecret(): Promise<string> {
  const response = await fetch(backendHttpUrl('/api/admin/strategy-webhook-secret/reveal'), {
    method: 'POST',
    credentials: 'include',
    cache: 'no-store',
  })
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok || body.ok === false) throw new Error(String(body.error ?? `Could not reveal the managed secret: ${response.status}`))
  if (typeof body.secret !== 'string' || !body.secret) throw new Error('No managed secret is configured yet — rotate to generate one.')
  return body.secret
}

export async function rotateManagedStrategyWebhookSecret(): Promise<string> {
  const response = await fetch(backendHttpUrl('/api/admin/strategy-webhook-secret/rotate'), {
    method: 'POST',
    credentials: 'include',
    cache: 'no-store',
  })
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok || body.ok === false) throw new Error(String(body.error ?? `Could not rotate the managed secret: ${response.status}`))
  if (typeof body.secret !== 'string' || !body.secret) throw new Error('The server did not return a new secret.')
  return body.secret
}
