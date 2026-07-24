import { backendHttpUrl } from '../lib/backend'

// Mirrors the owner-scoped read model in backend/app/services/signals_feed.py.
// The API deliberately exposes no raw payload and no secret — only a digest.
export interface SignalItem {
  id: string
  received_at: string | null
  provider: string
  event_id: string
  signature_ok: boolean
  replay_status: string
  processed_status: string
  error: string | null
  strategy_name: string | null
  signal_status: string | null
  summary: Record<string, unknown>
  raw_body_sha256: string
}

export interface SignalsPage {
  ok: boolean
  available: boolean
  items: SignalItem[]
  next_cursor: string | null
  counts: Record<string, number>
  counts_window_hours?: number
}

/** Persisted statuses. Not the mockup's routed/blocked/duplicate vocabulary. */
export const SIGNAL_STATUSES = ['received', 'accepted', 'queued', 'rejected'] as const
export type SignalStatus = (typeof SIGNAL_STATUSES)[number] | 'all'

export async function getSignals(params: { limit?: number; cursor?: string | null; status?: SignalStatus } = {}): Promise<SignalsPage> {
  const query = new URLSearchParams()
  if (params.limit) query.set('limit', String(params.limit))
  if (params.cursor) query.set('cursor', params.cursor)
  if (params.status && params.status !== 'all') query.set('status', params.status)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  const response = await fetch(backendHttpUrl(`/api/signals${suffix}`), {
    credentials: 'include',
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(`Could not load signals: ${response.status}`)
  }
  return response.json() as Promise<SignalsPage>
}
