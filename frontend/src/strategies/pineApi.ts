import { backendHttpUrl } from '../lib/backend'

// Wraps the existing personal-Pine workflow. No new backend was added:
// create -> validate -> submit for admin review are all pre-existing routes.
export interface PineFinding {
  code: string
  severity: 'ERROR' | 'WARNING' | 'INFO'
  title: string
  explanation: string
  line?: number | null
}

export interface PineValidation {
  status: 'PASSED' | 'PASSED_WITH_WARNINGS' | 'FAILED' | 'VALIDATOR_ERROR'
  validation_engine: string
  validator_version: string
  contract_version: string
  error_count: number
  warning_count: number
  info_count: number
  eligible_for_review: boolean
  findings: PineFinding[]
}

export interface CreatedStrategy {
  strategy: { id: string; name: string }
  version: { id: string; status: string }
}

async function call<T>(path: `/${string}`, init?: RequestInit): Promise<T> {
  const response = await fetch(backendHttpUrl(path), {
    credentials: 'include',
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok || body.ok === false) {
    throw new Error(String(body.error ?? `Request failed: ${response.status}`))
  }
  return body as T
}

export function createPineStrategy(name: string, source: string, filename?: string) {
  return call<CreatedStrategy>('/api/personal-pine-strategies', {
    method: 'POST',
    body: JSON.stringify({ name, source, filename: filename ?? null }),
  })
}

export function validatePineVersion(strategyId: string, versionId: string) {
  return call<{ validation: PineValidation }>(
    `/api/personal-pine-strategies/${strategyId}/versions/${versionId}/validate`,
    { method: 'POST' },
  )
}

/** Submits for admin review. Approval is required before the strategy can be
    selected; nothing here starts an engine. */
export function submitForReview(strategyId: string, versionId: string, promptVersionId: string) {
  return call<{ review?: { id: string; status: string } }>(
    `/api/personal-pine-strategies/${strategyId}/versions/${versionId}/submit`,
    {
      method: 'POST',
      body: JSON.stringify({
        original_version_id: versionId,
        prompt_version_id: promptVersionId,
        setup_type: 'USER_MANAGED_TRADINGVIEW',
        assumptions: [],
        reviewed_strategy: true,
        understands_static_validation: true,
        understands_performance_risk: true,
        accepts_paper_only: true,
      }),
    },
  )
}

export const ACCEPTED_EXTENSIONS = ['.pine', '.txt'] as const

export function extensionIsSupported(filename: string): boolean {
  const lower = filename.toLowerCase()
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext))
}
