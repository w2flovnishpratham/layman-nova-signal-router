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

export interface V2PaperFanoutFlags {
  debug_enabled?: boolean
  multi_strategy_fanout?: boolean
  v2_paper_runner_debug?: boolean
}

export interface V2PaperFanoutCounts {
  paper_instances?: number
  active_paper_instances?: number
  real_order_instances?: number
  signal_only_instances?: number
  v2_paper_jobs?: number
  v2_non_paper_jobs?: number
  v2_pending_jobs?: number
  v2_locked_jobs?: number
  v2_completed_paper_jobs?: number
  v2_failed_paper_jobs?: number
  strategy_signals?: number
  normalized_option_signals?: number
  [key: string]: number | undefined
}

export interface V2PaperFanoutJob {
  id?: string
  instance_id?: string | null
  strategy_code?: string | null
  signal_id?: string | null
  execution_mode?: string | null
  status?: string | null
  attempts?: number | null
  max_attempts?: number | null
  result_status?: string | null
  error_code?: string | null
  last_error?: string | null
  dead_letter_reason?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface V2PaperOpenPositionSummary {
  has_open_position?: boolean
  strategy_code?: string | null
  symbol?: string | null
  instrument_type?: string | null
  exchange_segment?: string | null
  trading_symbol?: string | null
  option_side?: string | null
  strike?: number | string | null
  expiry?: string | null
  qty?: number | null
  entry_price?: number | null
  opened_at?: string | null
  execution_mode?: string | null
  source_signal_id?: string | null
  v2_job_id?: string | null
}

export interface V2PaperStrategyInstance {
  id?: string
  strategy_code?: string | null
  status?: string | null
  execution_mode?: string | null
  lots?: number | null
  side_preference?: string | null
  has_open_position?: boolean
  open_position_summary?: V2PaperOpenPositionSummary
  created_at?: string | null
  updated_at?: string | null
}

export interface V2PaperFanoutStatusResponse {
  ok?: boolean
  limit?: number
  flags?: V2PaperFanoutFlags
  counts?: V2PaperFanoutCounts
  jobs_by_status?: Record<string, number>
  recent_jobs?: V2PaperFanoutJob[]
  safety?: {
    read_only?: boolean
    real_orders_supported?: boolean
    startup_worker_registered?: string | boolean
    public_webhook_enabled?: string | boolean
  }
}

export interface V2PaperFanoutJobsResponse {
  ok?: boolean
  limit?: number
  count?: number
  jobs?: V2PaperFanoutJob[]
}

export interface V2PaperFanoutInstancesResponse {
  ok?: boolean
  limit?: number
  count?: number
  instances?: V2PaperStrategyInstance[]
}

export interface V2PaperFanoutBundle {
  status: V2PaperFanoutStatusResponse
  jobs: V2PaperFanoutJobsResponse
  instances: V2PaperFanoutInstancesResponse
}

export interface StrategyCatalogVersion {
  id?: string
  strategy_id?: string
  version?: string | null
  status?: string | null
  payload_version?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface StrategyCatalogItem {
  id?: string
  code?: string | null
  name?: string | null
  description?: string | null
  source_type?: string | null
  status?: string | null
  created_at?: string | null
  updated_at?: string | null
  versions?: StrategyCatalogVersion[]
}

export interface StrategyCatalogStatusResponse {
  ok?: boolean
  database_configured?: boolean
  catalog?: StrategyCatalogItem[]
}

export interface UserStrategyInstanceStatus {
  id?: string
  strategy_id?: string
  strategy_code?: string | null
  strategy_name?: string | null
  strategy_status?: string | null
  strategy_source_type?: string | null
  strategy_version_id?: string | null
  strategy_version?: string | null
  payload_version?: string | null
  version_status?: string | null
  instance_label?: string | null
  source_type?: string | null
  status?: string | null
  execution_mode?: string | null
  lots?: number | null
  side_preference?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface UserStrategyInstancesStatusResponse {
  ok?: boolean
  database_configured?: boolean
  instances?: UserStrategyInstanceStatus[]
}

export interface StrategyInstanceHistoryItem {
  event_type?: string | null
  created_at?: string | null
  summary?: string | null
  changed_fields?: string[]
  old_values?: Record<string, string | number | boolean | null>
  new_values?: Record<string, string | number | boolean | null>
  previous_status?: string | null
  new_status?: string | null
  actor_type?: string | null
}

export interface StrategyInstanceDetailsResponse {
  ok?: boolean
  instance?: UserStrategyInstanceStatus
  history?: StrategyInstanceHistoryItem[]
  safe_runtime_summary?: {
    has_open_position?: boolean
  }
}

export interface StrategyCatalogStatusBundle {
  catalog: StrategyCatalogStatusResponse
  instances: UserStrategyInstancesStatusResponse
}

export class V2PaperStatusDisabledError extends Error {
  constructor() {
    super('V2 Paper Status is disabled.')
    this.name = 'V2PaperStatusDisabledError'
  }
}

export class StrategyCatalogStatusDisabledError extends Error {
  constructor() {
    super('Strategy Catalog Status is disabled.')
    this.name = 'StrategyCatalogStatusDisabledError'
  }
}

// ---------------------------------------------------------------------------
// Phase 2F/2G: paper strategy instance lifecycle/settings helpers. These are
// the ONLY approved mutation helpers for the internal catalog panel. They call
// debug instance endpoints exclusively - never the paper-fanout runner
// endpoints, never legacy strategy routes.
// ---------------------------------------------------------------------------

export interface StrategyInstanceMutationResult {
  ok: boolean
  changed: boolean
  instance?: {
    id: string
    strategy_code?: string | null
    instance_label?: string | null
    execution_mode?: string | null
    status?: string | null
    lots?: number | null
    side_preference?: string | null
  }
  detail?: string
}

async function lifecycleMutation(path: `/${string}`, body: Record<string, unknown>): Promise<StrategyInstanceMutationResult> {
  const response = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, confirm_paper_only: true }),
  })
  const parsed = await response.json().catch(() => null) as StrategyInstanceMutationResult | { detail?: string } | null
  if (!response.ok) {
    const detail = (parsed && 'detail' in parsed && parsed.detail) || `Request failed: ${response.status}`
    throw new Error(String(detail))
  }
  return (parsed as StrategyInstanceMutationResult) ?? { ok: false, changed: false }
}

export async function createPaperStrategyInstance(input: {
  catalogCode: string
  instanceLabel?: string
  lots?: number
  sidePreference?: 'BOTH' | 'CE' | 'PE'
}): Promise<StrategyInstanceMutationResult> {
  return lifecycleMutation('/api/debug/v2/instances', {
    catalog_code: input.catalogCode,
    instance_label: input.instanceLabel,
    lots: input.lots ?? 1,
    side_preference: input.sidePreference ?? 'BOTH',
  })
}

export async function pausePaperStrategyInstance(instanceId: string): Promise<StrategyInstanceMutationResult> {
  return lifecycleMutation(`/api/debug/v2/instances/${encodeURIComponent(instanceId)}/pause`, {})
}

export async function resumePaperStrategyInstance(instanceId: string): Promise<StrategyInstanceMutationResult> {
  return lifecycleMutation(`/api/debug/v2/instances/${encodeURIComponent(instanceId)}/resume`, {})
}

export async function archivePaperStrategyInstance(instanceId: string): Promise<StrategyInstanceMutationResult> {
  return lifecycleMutation(`/api/debug/v2/instances/${encodeURIComponent(instanceId)}/archive`, {})
}

export async function restorePaperStrategyInstance(instanceId: string): Promise<StrategyInstanceMutationResult> {
  return lifecycleMutation(`/api/debug/v2/instances/${encodeURIComponent(instanceId)}/restore`, {})
}

export async function updateStrategyInstanceSettings(
  instanceId: string,
  input: {
    instanceLabel?: string | null
    lots?: number | null
    sidePreference?: 'BOTH' | 'CE' | 'PE' | null
  },
): Promise<StrategyInstanceMutationResult> {
  return lifecycleMutation(`/api/debug/v2/instances/${encodeURIComponent(instanceId)}/settings`, {
    instance_label: input.instanceLabel,
    lots: input.lots,
    side_preference: input.sidePreference,
  })
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

export async function getV2PaperFanoutBundle(limit = 20): Promise<V2PaperFanoutBundle> {
  const safeLimit = Math.max(1, Math.min(100, Math.trunc(limit) || 20))
  const query = `?limit=${safeLimit}`
  const [status, jobs, instances] = await Promise.all([
    readV2PaperEndpoint<V2PaperFanoutStatusResponse>(`/api/debug/v2/paper-fanout/status${query}`),
    readV2PaperEndpoint<V2PaperFanoutJobsResponse>(`/api/debug/v2/paper-fanout/jobs${query}`),
    readV2PaperEndpoint<V2PaperFanoutInstancesResponse>(`/api/debug/v2/paper-fanout/instances${query}`),
  ])
  return { status, jobs, instances }
}

export async function getStrategyCatalogStatusBundle(): Promise<StrategyCatalogStatusBundle> {
  const [catalog, instances] = await Promise.all([
    readStrategyStatusEndpoint<StrategyCatalogStatusResponse>('/api/strategies/catalog'),
    readStrategyStatusEndpoint<UserStrategyInstancesStatusResponse>('/api/strategies/instances'),
  ])
  return { catalog, instances }
}

export async function getStrategyInstanceDetails(instanceId: string): Promise<StrategyInstanceDetailsResponse> {
  return readStrategyStatusEndpoint<StrategyInstanceDetailsResponse>(
    `/api/debug/v2/instances/${encodeURIComponent(instanceId)}/details`,
  )
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

async function readV2PaperEndpoint<T>(path: `/${string}`): Promise<T> {
  const response = await apiFetch(path, { cache: 'no-store' })
  if (response.status === 403 || response.status === 404) {
    throw new V2PaperStatusDisabledError()
  }
  if (!response.ok) {
    throw new Error(`Could not load V2 paper status: ${response.status}`)
  }
  const body = await response.json().catch(() => ({})) as unknown
  return sanitizeInternalStatusPayload(body) as T
}

async function readStrategyStatusEndpoint<T>(path: `/${string}`): Promise<T> {
  const response = await apiFetch(path, { cache: 'no-store' })
  if (response.status === 403 || response.status === 404) {
    throw new StrategyCatalogStatusDisabledError()
  }
  if (!response.ok) {
    throw new Error(`Could not load strategy catalog status: ${response.status}`)
  }
  const body = await response.json().catch(() => ({})) as unknown
  return sanitizeInternalStatusPayload(body) as T
}

const INTERNAL_STATUS_SENSITIVE_KEYS = new Set([
  'access_token',
  'client_id',
  'config',
  'config_json',
  'credential_hash',
  'credentials_hash',
  'headers',
  'message_secret_hash',
  'metadata',
  'metadata_json',
  'password',
  'payload',
  'pin',
  'proxy_url',
  'raw_payload',
  'raw_response',
  'request',
  'response',
  'response_json',
  'response_text',
  'secret',
  'signal_payload',
  'token',
  'webhook_key_hash',
])

function sanitizeInternalStatusPayload(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sanitizeInternalStatusPayload)
  }
  if (value && typeof value === 'object') {
    const result: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(value)) {
      const lowered = key.toLowerCase()
      if (
        INTERNAL_STATUS_SENSITIVE_KEYS.has(lowered)
        || lowered.includes('access_token')
        || lowered.includes('credential')
        || lowered.includes('headers')
        || lowered.includes('proxy_url')
        || lowered.includes('secret')
        || lowered.includes('token')
      ) {
        continue
      }
      result[key] = sanitizeInternalStatusPayload(item)
    }
    return result
  }
  return value
}
