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

export type RazorpayPlanCode = 'premium_monthly'

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

export interface PaymentEntitlementStatus {
  valid: boolean
  reason: string
  status: string | null
  source: string | null
  plan_code: string | null
  live_orders_enabled: boolean
  static_ip_enabled: boolean
  strategy_access_enabled: boolean
  max_strategy_count: number | null
}

export interface UserCredentialStatus {
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

export interface NiftyCandle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface NiftyCandleSeries {
  symbol: string
  interval: string
  source: string
  timezone?: string
  trading_date?: string
  session_start?: string
  session_end?: string
  status: 'ready' | 'unavailable' | string
  market_state: 'open' | 'closed' | string
  token_status?: string
  updated_at?: string | null
  candle_count?: number
  candles: NiftyCandle[]
  reason?: string | null
  message?: string | null
  stale?: boolean
  note?: string | null
}

export interface NiftyTradeMarker {
  time: number
  side: 'BUY' | 'SELL'
  option_side?: 'CE' | 'PE' | null
  label: string
  price?: number | null
  approximate?: boolean
  mode?: string | null
  source?: string
}

export async function getNiftyCandles(): Promise<NiftyCandleSeries> {
  const response = await apiFetch('/api/market/nifty/candles?interval=5m', { cache: 'no-store' })
  if (!response.ok) {
    throw new Error(`Could not load NIFTY candles: ${response.status}`)
  }
  return response.json() as Promise<NiftyCandleSeries>
}

export interface NiftyMarkerResponse {
  symbol: string
  trading_date: string
  mode?: string | null
  markers: NiftyTradeMarker[]
}

export async function getNiftyMarkers(mode?: 'paper' | 'live'): Promise<NiftyMarkerResponse> {
  const path: `/${string}` = mode ? `/api/market/nifty/markers?mode=${mode}` : '/api/market/nifty/markers'
  const response = await apiFetch(path, { cache: 'no-store' })
  if (!response.ok) {
    throw new Error(`Could not load chart markers: ${response.status}`)
  }
  return response.json() as Promise<NiftyMarkerResponse>
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

// Lightweight read of just the current user's assigned static-IP status, used
// to show the IP in the account menu at any time after payment.
export async function getEgressStatus(): Promise<EgressStatus | null> {
  const response = await apiFetch('/api/strategies/egress/status', { cache: 'no-store' })
  if (!response.ok) return null
  return response.json().catch(() => null) as Promise<EgressStatus | null>
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

export async function getUserCredentialStatus(): Promise<UserCredentialStatus | null> {
  const response = await apiFetch('/api/user/credentials/status', { cache: 'no-store' })
  if (!response.ok) return null
  return response.json().catch(() => null) as Promise<UserCredentialStatus | null>
}

export async function saveUserCredentials(payload: {
  dhan_client_id?: string
  dhan_access_token?: string
}): Promise<UserCredentialStatus> {
  const response = await apiFetch('/api/user/credentials', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail || `Could not save credentials: ${response.status}`)
  }
  return response.json() as Promise<UserCredentialStatus>
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

export async function getPaymentEntitlementStatus(): Promise<PaymentEntitlementStatus> {
  const response = await apiFetch('/api/payments/entitlements/status', { cache: 'no-store' })
  const body = await response.json().catch(() => null) as { ok?: boolean; entitlement?: PaymentEntitlementStatus; error?: string } | null
  if (!response.ok || !body?.ok || !body.entitlement) {
    throw new Error(body?.error || `Could not load payment status: ${response.status}`)
  }
  return body.entitlement
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

function newIdempotencyKey(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }
  } catch {
    // fall through to the timestamp-based key
  }
  return `nova-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

async function postManualOrder(path: `/${string}`, payload: unknown): Promise<ManualOrderResponse> {
  // Live manual orders require a unique Idempotency-Key so the backend can
  // dedupe a retried request instead of placing a second real order.
  const response = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': newIdempotencyKey() },
    body: JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as ManualOrderResponse | null
  if (!response.ok) {
    throw new Error(body?.message || `Manual order failed: ${response.status}`)
  }
  return body ?? { ok: false, message: 'Manual order failed.' }
}
