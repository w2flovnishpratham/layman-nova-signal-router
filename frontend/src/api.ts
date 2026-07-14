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

// ---------------------------------------------------------------------------
// Strategy instances (Phase 1 control plane)
// ---------------------------------------------------------------------------

export interface StrategyInstance {
  id: string
  strategy_id: string
  strategy_version_id: string
  strategy_code: string | null
  strategy_display_name: string | null
  source_journey: 'NOVA_SHARED' | 'NOVA_HOSTED_PERSONAL' | 'PERSONAL_TRADINGVIEW'
  label: string
  status: string
  status_reason: string | null
  execution_mode: string
  current_lots: number
  created_at: string | null
  updated_at: string | null
  archived_at: string | null
  webhook_credential?: InstanceWebhookCredential | null
  credential_status?: 'active' | 'revoked' | 'missing'
  readiness?: {
    paper_mode: boolean
    valid_lots: boolean
    active_credential: boolean
    connection_tested: boolean
    can_activate: boolean
  }
  lot_size?: number
  estimated_quantity?: number
  last_signal_time?: string | null
  last_execution_status?: string | null
  has_open_position?: boolean
}

export interface InstanceWebhookCredential {
  id: string
  strategy_instance_id: string
  token_prefix: string
  created_at: string | null
  last_used_at: string | null
  revoked_at: string | null
  /** Present only in the generate/rotate response — shown exactly once. */
  token?: string
}

interface InstanceResponse {
  ok: boolean
  instance?: StrategyInstance
  error?: string
}

async function instanceCall(path: `/${string}`, method: string, payload?: unknown): Promise<StrategyInstance> {
  const response = await apiFetch(path, {
    method,
    headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as InstanceResponse | null
  if (!response.ok || !body?.ok || !body.instance) {
    throw new Error(body?.error || `Strategy instance request failed: ${response.status}`)
  }
  return body.instance
}

export async function listStrategyInstances(includeArchived = false): Promise<StrategyInstance[]> {
  const suffix = includeArchived ? '?include_archived=true' : ''
  const response = await apiFetch(`/api/strategy-instances${suffix}` as `/${string}`, { cache: 'no-store' })
  const body = await response.json().catch(() => null) as { ok?: boolean; instances?: StrategyInstance[]; error?: string } | null
  if (!response.ok || !body?.ok || !body.instances) {
    throw new Error(body?.error || `Could not load strategy instances: ${response.status}`)
  }
  return body.instances
}

export async function getStrategyInstance(id: string): Promise<StrategyInstance> {
  return instanceCall(`/api/strategy-instances/${id}` as `/${string}`, 'GET')
}

export interface CreateStrategyInstancePayload {
  strategy_code: string
  source_journey?: StrategyInstance['source_journey']
  label?: string
  lots?: number
  execution_mode?: string
}

export async function createStrategyInstance(payload: CreateStrategyInstancePayload): Promise<StrategyInstance> {
  return instanceCall('/api/strategy-instances', 'POST', payload)
}

export async function cloneStrategyInstance(id: string, label?: string): Promise<StrategyInstance> {
  return instanceCall(`/api/strategy-instances/${id}/clone` as `/${string}`, 'POST', { label: label ?? null })
}

export async function updateStrategyInstanceLots(id: string, lots: number): Promise<StrategyInstance> {
  return instanceCall(`/api/strategy-instances/${id}/lots` as `/${string}`, 'POST', { lots })
}

export async function activateStrategyInstance(id: string): Promise<StrategyInstance> {
  return instanceCall(`/api/strategy-instances/${id}/activate` as `/${string}`, 'POST')
}

export async function pauseStrategyInstance(id: string, reason?: string): Promise<StrategyInstance> {
  return instanceCall(`/api/strategy-instances/${id}/pause` as `/${string}`, 'POST', { reason: reason ?? null })
}

export async function resumeStrategyInstance(id: string): Promise<StrategyInstance> {
  return instanceCall(`/api/strategy-instances/${id}/resume` as `/${string}`, 'POST')
}

export async function stopStrategyInstance(id: string, reason?: string): Promise<StrategyInstance> {
  return instanceCall(`/api/strategy-instances/${id}/stop` as `/${string}`, 'POST', { reason: reason ?? null })
}

export async function archiveStrategyInstance(id: string): Promise<StrategyInstance> {
  return instanceCall(`/api/strategy-instances/${id}/archive` as `/${string}`, 'POST')
}

async function credentialCall(path: `/${string}`, method: string): Promise<InstanceWebhookCredential> {
  const response = await apiFetch(path, { method })
  const body = await response.json().catch(() => null) as { ok?: boolean; credential?: InstanceWebhookCredential; error?: string } | null
  if (!response.ok || !body?.ok || !body.credential) {
    throw new Error(body?.error || `Webhook credential request failed: ${response.status}`)
  }
  return body.credential
}

export async function generateInstanceWebhookCredential(id: string): Promise<InstanceWebhookCredential> {
  return credentialCall(`/api/strategy-instances/${id}/webhook-credential` as `/${string}`, 'POST')
}

export async function rotateInstanceWebhookCredential(id: string): Promise<InstanceWebhookCredential> {
  return credentialCall(`/api/strategy-instances/${id}/webhook-credential/rotate` as `/${string}`, 'POST')
}

export async function revokeInstanceWebhookCredential(id: string): Promise<InstanceWebhookCredential> {
  return credentialCall(`/api/strategy-instances/${id}/webhook-credential` as `/${string}`, 'DELETE')
}

export interface WebhookExecution {
  signal_id: string
  strategy_instance_id: string
  action: 'BUY_CE' | 'BUY_PE' | 'EXIT' | 'HOLD' | null
  signal_time: string | null
  received_at: string | null
  status: string
  reason: string | null
  execution_mode: string | null
  job_status: string | null
  job_created_at: string | null
  job_completed_at: string | null
  result: {
    status?: string
    reason?: string
    execution_status?: string
    execution_reason?: string
    order_id?: string
    contract?: {
      trading_symbol?: string
      option_side?: string
      strike?: number
      expiry?: string
      qty?: number
    }
  } | null
}

export async function listInstanceWebhookExecutions(
  id: string,
  limit = 10,
  offset = 0,
): Promise<{ executions: WebhookExecution[]; limit: number; offset: number }> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  const response = await apiFetch(
    `/api/strategy-instances/${id}/webhook-executions?${params}` as `/${string}`,
    { cache: 'no-store' },
  )
  const body = await response.json().catch(() => null) as {
    ok?: boolean
    executions?: WebhookExecution[]
    limit?: number
    offset?: number
    error?: string
  } | null
  if (!response.ok || !body?.ok || !body.executions) {
    throw new Error(body?.error || `Could not load webhook history: ${response.status}`)
  }
  return { executions: body.executions, limit: body.limit ?? limit, offset: body.offset ?? offset }
}

export async function testInstanceWebhookConnection(id: string): Promise<{ status: string; signal_id: string }> {
  const response = await apiFetch(`/api/strategy-instances/${id}/webhook-test` as `/${string}`, {
    method: 'POST',
  })
  const body = await response.json().catch(() => null) as {
    ok?: boolean
    status?: string
    signal_id?: string
    error?: string
  } | null
  if (!response.ok || !body?.ok || !body.status || !body.signal_id) {
    throw new Error(body?.error || `Connection test failed: ${response.status}`)
  }
  return { status: body.status, signal_id: body.signal_id }
}

export interface HostedRuntime {
  id: string
  strategy_instance_id: string
  ir_version_id: string
  paper_only: true
  status: 'READY' | 'ACTIVE' | 'PAUSED' | 'ERROR' | 'STOPPED'
  last_evaluated_candle: string | null
  last_emitted_action: 'BUY_CE' | 'BUY_PE' | 'EXIT' | null
  last_signal_id: string | null
  warmup_status: string
  consecutive_error_count: number
  safe_error_code: string | null
  paused_reason: string | null
}

async function hostedCall<T>(path: `/${string}`, method = 'GET', payload?: unknown): Promise<T> {
  const response = await apiFetch(path, {
    method,
    cache: 'no-store',
    headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as ({ ok?: boolean; error?: string } & T) | null
  if (!response.ok || !body?.ok) throw new Error(body?.error || `Hosted strategy request failed: ${response.status}`)
  return body
}

export const getHostedStrategyConfig = async () =>
  hostedCall<{ runtime_enabled: boolean; paper_execution_enabled: boolean; paper_only: true }>('/api/hosted-strategies/config')
export const getHostedRuntime = async (id: string) =>
  (await hostedCall<{ runtime: HostedRuntime }>(`/api/strategy-instances/${id}/hosted-runtime` as `/${string}`)).runtime
export const activateHostedRuntime = async (id: string) =>
  (await hostedCall<{ runtime: HostedRuntime }>(`/api/strategy-instances/${id}/hosted-activate` as `/${string}`, 'POST')).runtime
export const pauseHostedRuntime = async (id: string) =>
  (await hostedCall<{ runtime: HostedRuntime }>(`/api/strategy-instances/${id}/hosted-pause` as `/${string}`, 'POST')).runtime
export const resumeHostedRuntime = async (id: string) =>
  (await hostedCall<{ runtime: HostedRuntime }>(`/api/strategy-instances/${id}/hosted-resume` as `/${string}`, 'POST')).runtime
export const stopHostedRuntime = async (id: string) =>
  (await hostedCall<{ runtime: HostedRuntime }>(`/api/strategy-instances/${id}/hosted-stop` as `/${string}`, 'POST')).runtime
export const listHostedEvaluations = async (id: string, limit = 20, offset = 0) =>
  hostedCall<{ items: Array<{ candle_close_timestamp: string; status: string; action: string | null; signal_id: string | null; duration_ms: number; safe_error_code: string | null }>; total: number }>(`/api/strategy-instances/${id}/hosted-evaluations?limit=${limit}&offset=${offset}` as `/${string}`)
export const replayHostedRuntime = async (id: string) =>
  hostedCall<{ timeline: Array<{ candle_close_timestamp: string; action: string; rule: string | null }>; fingerprint: string }>(`/api/strategy-instances/${id}/hosted-replay` as `/${string}`, 'POST')

// ---------------------------------------------------------------------------
// Personal Pine import, static validation and review (Phase 4A)
// ---------------------------------------------------------------------------

export interface PineFinding {
  code: string
  severity: 'ERROR' | 'WARNING' | 'INFO'
  title: string
  explanation: string
  remediation: string
  blocks_review: boolean
  line: number | null
  column: number | null
  excerpt: string | null
}

export interface PineValidation {
  id: string
  status: string
  validator_version: string
  contract_version: number
  source_sha256: string
  error_count: number
  warning_count: number
  info_count: number
  eligible_for_review: boolean
  findings: PineFinding[]
}

export interface PineVersion {
  id: string
  strategy_id: string
  version: string
  status: string
  source_sha256: string
  pine_contract_version: number
  changelog: string | null
  created_at: string | null
  approved_at: string | null
  validation: PineValidation | null
}

export interface PineStrategy {
  id: string
  name: string
  description: string | null
  status: string
  version_count: number | null
  latest_version: PineVersion | null
}

async function pineCall<T>(path: `/${string}`, method = 'GET', payload?: unknown): Promise<T> {
  const response = await apiFetch(path, {
    method,
    cache: 'no-store',
    headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as ({ ok?: boolean; error?: string } & T) | null
  if (!response.ok || !body?.ok) throw new Error(body?.error || `Personal Pine request failed: ${response.status}`)
  return body
}

export const listPineStrategies = async () =>
  (await pineCall<{ strategies: PineStrategy[] }>('/api/personal-pine-strategies')).strategies
export const getPineStrategy = async (id: string) =>
  pineCall<{ strategy: PineStrategy; versions: PineVersion[] }>(`/api/personal-pine-strategies/${id}` as `/${string}`)
export const createPineStrategy = async (payload: { name: string; source: string; filename: string; description?: string }) =>
  pineCall<{ strategy: PineStrategy; version: PineVersion }>('/api/personal-pine-strategies', 'POST', payload)
export const createPineVersion = async (strategyId: string, payload: { source: string; filename: string; changelog?: string }) =>
  pineCall<{ version: PineVersion; reused: boolean }>(`/api/personal-pine-strategies/${strategyId}/versions` as `/${string}`, 'POST', payload)
export const validatePineVersion = async (strategyId: string, versionId: string) =>
  pineCall<{ version: PineVersion; report: PineValidation; reused: boolean }>(`/api/personal-pine-strategies/${strategyId}/versions/${versionId}/validate` as `/${string}`, 'POST')
export const submitPineVersion = async (strategyId: string, versionId: string) =>
  pineCall<{ version: PineVersion }>(`/api/personal-pine-strategies/${strategyId}/versions/${versionId}/submit` as `/${string}`, 'POST')
export const getPineSource = async (strategyId: string, versionId: string) =>
  pineCall<{ source: string; filename: string; source_sha256: string; approved: boolean }>(`/api/personal-pine-strategies/${strategyId}/versions/${versionId}/source` as `/${string}`)
export const linkPineVersion = async (instanceId: string, strategyId: string, versionId: string) =>
  pineCall<{ link: unknown }>(`/api/personal-strategies/${instanceId}/link-version` as `/${string}`, 'POST', { strategy_id: strategyId, version_id: versionId })

export interface PineReview {
  strategy: PineStrategy
  version: PineVersion
  source?: string
  filename?: string
  history?: Array<{ id: string; decision: string; note: string | null; reviewed_at: string | null }>
}
export const listPineReviews = async () =>
  (await pineCall<{ reviews: PineReview[] }>('/api/admin/pine-reviews')).reviews
export const getPineReview = async (id: string) =>
  (await pineCall<{ review: PineReview }>(`/api/admin/pine-reviews/${id}` as `/${string}`)).review
export const decidePineReview = async (id: string, action: 'start' | 'approve' | 'request-changes' | 'reject', note: string, acknowledgeWarnings = false) =>
  pineCall<{ version: PineVersion }>(`/api/admin/pine-reviews/${id}/${action}` as `/${string}`, 'POST', { note, acknowledge_warnings: acknowledgeWarnings })

export interface PineConversionConfig {
  manual_package_enabled: boolean
  ai_enabled: boolean
  provider: string | null
  model: string | null
  prompt_version: string
  contract_version: number
  daily_limit: number
}

export interface PineConversion {
  id: string
  strategy_id: string
  input_version_id: string
  status: string
  provider: string
  model: string
  prompt_version: string
  consent_at: string
  candidate_version_id: string | null
  conversion_summary: string | null
  assumptions: string[]
  unsupported_features: string[]
  warnings: string[]
  action_mapping: Record<string, string>
  safe_error_code: string | null
  original_source?: string
  candidate_source?: string | null
  validation?: PineValidation | null
}

export const getPineConversionConfig = () => pineCall<PineConversionConfig>('/api/pine-conversions/config')
export const generatePineConversionPackage = (strategyId: string, versionId: string) =>
  pineCall<{ package: string; filename: string; package_sha256: string }>(`/api/personal-pine-strategies/${strategyId}/versions/${versionId}/conversion-package` as `/${string}`, 'POST')
export const createPineConversion = (strategyId: string, versionId: string) =>
  pineCall<{ conversion: PineConversion; reused: boolean }>(`/api/personal-pine-strategies/${strategyId}/versions/${versionId}/convert` as `/${string}`, 'POST', { consent: true })
export const getPineConversion = (id: string) => pineCall<{ conversion: PineConversion }>(`/api/pine-conversions/${id}` as `/${string}`)
export const acceptPineConversion = (id: string) => pineCall<{ conversion: PineConversion }>(`/api/pine-conversions/${id}/accept` as `/${string}`, 'POST')
export const rejectPineConversion = (id: string) => pineCall<{ conversion: PineConversion }>(`/api/pine-conversions/${id}/reject` as `/${string}`, 'POST', {})
export const retryPineConversion = (id: string) => pineCall<{ conversion: PineConversion }>(`/api/pine-conversions/${id}/retry` as `/${string}`, 'POST', { consent: true })
