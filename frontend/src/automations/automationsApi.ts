import { backendHttpUrl } from '../lib/backend'

// Mirrors backend/app/services/automations_overview.py.
// No automation table exists: the editable rules are the engine's own runtime
// settings, and the protected rules are code paths with nothing to write.
export interface EditableRule {
  key: string
  label: string
  value: number | string
  unit: string
  minimum: number | null
  maximum: number | null
  zero_means: string
  basis: string
  effect: string
  requires_restart: boolean
  affects_open_position: boolean
}

export interface ProtectedRule {
  key: string
  label: string
  description: string
  effect: string
  why_protected: string
}

export interface AutomationsOverview {
  ok: boolean
  editable: EditableRule[]
  protected: ProtectedRule[]
  storage: string
}

export async function getAutomations(): Promise<AutomationsOverview> {
  const response = await fetch(backendHttpUrl('/api/automations'), { credentials: 'include', cache: 'no-store' })
  if (!response.ok) throw new Error(`Could not load automations: ${response.status}`)
  return response.json() as Promise<AutomationsOverview>
}

export async function saveAutomations(values: Record<string, number | string>): Promise<AutomationsOverview> {
  const response = await fetch(backendHttpUrl('/api/automations'), {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(values),
  })
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok || body.ok === false) throw new Error(String(body.error ?? `Could not save: ${response.status}`))
  return body as unknown as AutomationsOverview
}
