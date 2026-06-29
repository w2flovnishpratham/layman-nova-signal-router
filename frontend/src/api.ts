import type { ActiveExitLevels, AtmLtpSnapshot, MarketSnapshot, SessionBootstrap, SessionSnapshot, SystemHealth } from './types'
import { backendHttpUrl } from './lib/backend'

export interface AuthUser {
  id: string
  email: string
  name: string | null
  picture_url: string | null
  is_admin: boolean
  is_dev: boolean
}

interface MeResponse {
  authenticated: boolean
  user: AuthUser | null
  auth_required: boolean
}

export interface EgressNodeOption {
  public_ip: string
  available: boolean
  selected: boolean
}

export interface EgressStatus {
  public_ip: string | null
  active: boolean
  has_proxy: boolean
  verified?: boolean
  last_verified_at?: string | null
  last_observed_ip?: string | null
  verification_error?: string | null
}

export interface EgressOptionsResponse {
  nodes: EgressNodeOption[]
  egress: EgressStatus
}

export type RazorpayPlanCode = 'live_monthly' | 'static_ip_monthly' | 'strategy_monthly' | 'bundle_monthly'

export interface RazorpayCheckoutResponse {
  ok: boolean
  provider: 'razorpay'
  checkout_url: string | null
  short_url: string | null
  subscription_id: string
  plan_code: RazorpayPlanCode
  status: string
  message: string
}

async function apiFetch(path: `/${string}`, init: RequestInit = {}): Promise<Response> {
  return fetch(backendHttpUrl(path), {
    ...init,
    credentials: 'include',
    headers: {
      ...init.headers,
    },
  })
}

export async function getCurrentUser(): Promise<AuthUser | null> {
  const response = await apiFetch('/api/me', { cache: 'no-store' })
  if (response.status === 401) return null
  if (!response.ok) {
    throw new Error(`Could not verify login: ${response.status}`)
  }
  const body = await response.json() as MeResponse
  return body.authenticated ? body.user : null
}

export function googleLoginUrl(): string {
  return backendHttpUrl('/api/auth/google/start')
}

export async function logout(): Promise<void> {
  const response = await apiFetch('/api/auth/logout', { method: 'POST' })
  if (!response.ok) {
    throw new Error(`Could not log out: ${response.status}`)
  }
}

export interface ManualEntryPayload {
  side: 'CE' | 'PE'
  lots: number
  targetProfitPct?: number
  stopLossPct?: number
  strike?: number | null
  expiry?: string | null
  securityId?: string | null
  tradingSymbol?: string | null
}

export interface ManualOrderResponse {
  ok: boolean
  message: string
  executionResult?: Record<string, unknown>
  normalizedError?: Record<string, unknown> | null
  orderJourney?: Array<Record<string, unknown>>
}

export interface ExitLevelsPayload {
  stopLossPrice: number
  targetPrice: number
}

export interface ExitLevelsResponse {
  ok: boolean
  message: string
  activeExitLevels?: ActiveExitLevels
  normalizedError?: Record<string, unknown> | null
}

export interface QuantityPayload {
  qty: number
}

export interface QuantityResponse {
  ok: boolean
  message: string
  qty?: number
  lots?: number
  normalizedError?: Record<string, unknown> | null
}

export interface OrderQuote {
  ok: boolean
  message?: string
  mode?: string
  side: 'CE' | 'PE'
  lots: number
  qty: number
  securityId?: string | null
  tradingSymbol?: string | null
  estimatedPremium?: number | null
  estimatedCost?: number | null
  estimatedMargin?: number | null
  normalizedError?: Record<string, unknown> | null
  atm?: AtmLtpSnapshot | null
}

export async function startSession(options: { restoreRecent?: boolean } = {}): Promise<SessionBootstrap> {
  const path: `/${string}` = options.restoreRecent ? '/api/session/start?restore_recent=true' : '/api/session/start'
  const response = await apiFetch(path, { method: 'POST' })
  if (!response.ok) {
    throw new Error(`Could not start session: ${response.status}`)
  }
  return response.json() as Promise<SessionBootstrap>
}

export async function getSession(sessionId: string): Promise<SessionSnapshot> {
  const response = await apiFetch(`/api/session/${sessionId}`)
  if (!response.ok) {
    throw new Error(`Could not load session: ${response.status}`)
  }
  return response.json() as Promise<SessionSnapshot>
}

export async function prepareReconfigure(): Promise<void> {
  const response = await apiFetch('/api/engine/reconfigure', { method: 'POST' })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string | { message?: string } } | null
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail : detail?.message
    throw new Error(message || `Could not reconfigure: ${response.status}`)
  }
}

export async function getEgressOptions(): Promise<EgressOptionsResponse> {
  const response = await apiFetch('/api/strategies/egress/options', { cache: 'no-store' })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: string } | null
    throw new Error(body?.error || `Could not load static IPs: ${response.status}`)
  }
  return response.json() as Promise<EgressOptionsResponse>
}

export async function selectEgressIp(publicIp: string): Promise<void> {
  const response = await apiFetch('/api/strategies/egress/select', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ public_ip: publicIp }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: string } | null
    throw new Error(body?.error || `Could not select static IP: ${response.status}`)
  }
}

export async function verifyEgressIp(): Promise<void> {
  const response = await apiFetch('/api/strategies/egress/verify', { method: 'POST' })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: string } | null
    throw new Error(body?.error || `Could not verify static IP: ${response.status}`)
  }
  const body = await response.json().catch(() => null) as { ok?: boolean, egress?: { error?: string } } | null
  if (!body?.ok) {
    throw new Error(body?.egress?.error || 'Static IP verification failed.')
  }
}

export async function createRazorpaySubscription(planCode: RazorpayPlanCode): Promise<RazorpayCheckoutResponse> {
  const response = await apiFetch('/api/payments/razorpay/create-subscription', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan_code: planCode }),
  })
  const body = await response.json().catch(() => null) as RazorpayCheckoutResponse & { error?: string } | null
  if (!response.ok) {
    throw new Error(body?.error || `Could not create Razorpay checkout: ${response.status}`)
  }
  if (!body?.ok) {
    throw new Error(body?.error || 'Could not create Razorpay checkout.')
  }
  return body
}

export async function getMarketSnapshot(): Promise<MarketSnapshot> {
  const response = await apiFetch('/api/market/nifty-snapshot', { cache: 'no-store' })
  if (!response.ok) {
    throw new Error(`Could not load market snapshot: ${response.status}`)
  }
  return response.json() as Promise<MarketSnapshot>
}

export async function getSystemHealth(): Promise<SystemHealth> {
  const response = await apiFetch('/api/system/health-strip', { cache: 'no-store' })
  if (!response.ok) {
    throw new Error(`Could not load system health: ${response.status}`)
  }
  return response.json() as Promise<SystemHealth>
}

export async function getOrderQuote(side: 'CE' | 'PE', lots: number): Promise<OrderQuote> {
  const params = new URLSearchParams({ side, lots: String(lots) })
  const response = await apiFetch(`/api/orders/quote?${params.toString()}` as `/${string}`, { cache: 'no-store' })
  if (!response.ok) {
    throw new Error(`Could not load quote: ${response.status}`)
  }
  return response.json() as Promise<OrderQuote>
}

export async function postManualEntry(payload: ManualEntryPayload): Promise<ManualOrderResponse> {
  return postManualOrder('/api/orders/manual-entry', payload)
}

export async function postManualExit(): Promise<ManualOrderResponse> {
  return postManualOrder('/api/orders/manual-exit', { reason: 'manual_panel' })
}

export async function postManualReverse(lots: number): Promise<ManualOrderResponse> {
  return postManualOrder('/api/orders/manual-reverse', { lots })
}

export async function patchActiveExitLevels(payload: ExitLevelsPayload): Promise<ExitLevelsResponse> {
  const response = await apiFetch('/api/orders/active-position/exit-levels', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as ExitLevelsResponse | null
  if (!response.ok) {
    throw new Error(body?.message || `Could not update SL/TP: ${response.status}`)
  }
  return body ?? { ok: false, message: 'Could not update SL/TP.' }
}

export async function patchActiveQuantity(payload: QuantityPayload): Promise<QuantityResponse> {
  const response = await apiFetch('/api/orders/active-position/quantity', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as QuantityResponse | null
  if (!response.ok) {
    throw new Error(body?.message || `Could not update qty: ${response.status}`)
  }
  return body ?? { ok: false, message: 'Could not update qty.' }
}

async function postManualOrder(path: `/${string}`, payload: unknown): Promise<ManualOrderResponse> {
  const response = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as ManualOrderResponse | null
  if (!response.ok) {
    throw new Error(body?.message || `Manual order failed: ${response.status}`)
  }
  return body ?? { ok: false, message: 'Manual order failed.' }
}
