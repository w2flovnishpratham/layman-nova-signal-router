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

export type LogoutEngineAction = 'keep_running' | 'stop_engine'

export async function logout(engineAction: LogoutEngineAction = 'keep_running'): Promise<void> {
  const response = await apiFetch('/api/auth/logout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ engine_action: engineAction }),
  })
  if (!response.ok) {
    throw new Error(`Could not log out: ${response.status}`)
  }
}

export interface RuntimeStatus {
  ok: boolean
  owner_user_id: string
  engine: {
    state: 'STARTING' | 'RUNNING' | 'STOPPING' | 'STOPPED' | string
    running: boolean
    accepting_signals: boolean
    mode: 'paper' | 'live' | null
    display: string
    last_transition_at: string | null
  }
  exit: {
    state: 'NONE' | 'EXIT_PENDING' | 'CONFIRMED_FLAT' | 'RECONCILIATION_REQUIRED' | string
    operation_id: string | null
    requested_at: string | null
  }
  position: {
    has_open_position: boolean
    position_id?: string | null
    position_version?: number
    security_id: string | null
    trading_symbol: string | null
    option_side: string | null
    strike?: number | null
    expiry?: string | null
    entry_order_id?: string | null
    qty: number
    lots: number
    entry_price: number | null
    opened_at: string | null
    unrealized_pnl: number | null
    risk?: {
      armed: boolean
      status: 'armed' | 'unarmed' | 'not_applicable' | string
      source: string | null
      server_managed: boolean
      stop_price: number | null
      target_price: number | null
      stop_loss_percent: number
      take_profit_percent: number
    }
    ltp: {
      value: number | null
      source: string | null
      status: string
      received_at: string | null
      age_seconds: number | null
      stale: boolean
      message: string | null
    }
  }
  pnl: {
    realized: number | null
    unrealized: number | null
    session: number | null
    available_balance: number | null
    utilized_amount?: number | null
  }
  config: {
    active: Record<string, unknown>
    paper: Record<string, unknown>
    live: Record<string, unknown>
  }
  account: {
    dhan_client_id_masked: string | null
    has_dhan_access_token: boolean
    dhan_token_saved_at: string | null
    token_age_minutes: number | null
    token_expired: boolean | null
    token_estimated_expiry_at: string | null
  }
  safety: {
    dhan_mode: string
    live_orders_enabled: boolean
  }
  selected_strategy: EngineStrategy | null
  selected_configuration?: {
    id: string
    strategy_instance_id: string
    strategy_version_id: string
    mode: 'paper' | 'live'
    revision: number
    status: string
    configuration: Record<string, unknown>
    risk: Record<string, unknown>
    committed_at: string | null
  } | null
  eligible_strategies: EngineStrategy[]
  selection_issue: string | null
  strategy_catalog?: StrategyCatalog
}

async function runtimeCall(path: `/${string}`, method = 'GET', payload?: unknown): Promise<RuntimeStatus> {
  const response = await apiFetch(path, {
    method,
    cache: 'no-store',
    headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as (RuntimeStatus & { detail?: string | RuntimeStatus; message?: string }) | null
  if (!response.ok) {
    const detail = body?.detail
    const message = typeof detail === 'string'
      ? detail
      : (detail as { message?: string } | undefined)?.message
    throw new Error(message || body?.message || `Runtime request failed: ${response.status}`)
  }
  if (!body) throw new Error('Runtime response was empty.')
  return body
}

export const getRuntimeStatus = () => runtimeCall('/api/runtime/status')
export const startSelectedEngine = (
  strategyInstanceId: string,
  configurationRevisionId: string,
  configurationRevision: number,
  mode: 'paper' | 'live',
) => runtimeCall('/api/runtime/start-selected', 'POST', {
  strategy_instance_id: strategyInstanceId,
  configuration_revision_id: configurationRevisionId,
  configuration_revision: configurationRevision,
  mode,
})
export const stopRuntimeEngine = () => runtimeCall('/api/runtime/stop', 'POST')
export const squareOffRuntime = () => runtimeCall('/api/runtime/square-off', 'POST')
export const switchRuntimeMode = (mode: 'paper' | 'live') =>
  runtimeCall('/api/runtime/mode', 'PUT', { mode })
export const saveRuntimeConfig = (
  mode: 'paper' | 'live',
  lots: number,
  stopLossPercent: number,
  targetProfitPercent: number,
) => runtimeCall('/api/runtime/config', 'PUT', {
  mode,
  lots,
  stop_loss_percent: stopLossPercent,
  target_profit_percent: targetProfitPercent,
})
export const resetPaperRuntime = (startingBalance?: number) =>
  runtimeCall('/api/runtime/paper/reset', 'POST', {
    starting_balance: startingBalance ?? null,
  })

export type StrategySetupField =
  | {
      key: string
      type: 'choice'
      label: string
      options: string[]
      required: boolean
      default?: string
    }
  | {
      key: string
      type: 'integer' | 'decimal'
      label: string
      minimum: number
      maximum: number
      required: boolean
      default?: number
    }

export interface CatalogStrategy {
  strategy_key: string
  strategy_instance_id: string | null
  source_type: 'BUILT_IN' | 'IMPORTED'
  name: string
  version: string | null
  description: string
  availability: 'READY' | 'COMING_SOON' | 'DISABLED' | 'UNAVAILABLE'
  disabled_reason: string | null
  paper_eligible: boolean
  live_eligible: boolean
  selected: boolean
  runtime_state: string
  setup_schema: { fields: StrategySetupField[] }
  saved_setup: Record<string, Record<string, unknown>>
  readiness?: Record<string, boolean> | null
  pine_exit_behavior?: { status: string; message: string } | null
}

export interface StrategyCatalog {
  strategies: CatalogStrategy[]
  selected_strategy_key: string | null
  selected_strategy_instance_id: string | null
  setup_progress: {
    strategy_key: string | null
    mode: 'paper' | 'live' | null
    stage: 'CHOOSE_STRATEGY' | 'CONFIGURE' | 'REVIEW'
    complete: boolean
  }
}

async function catalogCall<T>(path: `/${string}`, method = 'GET', payload?: unknown): Promise<T> {
  const response = await apiFetch(path, {
    method,
    cache: 'no-store',
    headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as (T & { error?: string }) | null
  if (!response.ok || !body) throw new Error(body?.error || `Strategy catalog request failed: ${response.status}`)
  return body
}

export const getStrategyCatalog = () =>
  catalogCall<StrategyCatalog & { ok: true }>('/api/strategies/catalog')

export const selectCatalogStrategy = (strategyKey: string) =>
  catalogCall<{ ok: true; selected_instance_id: string; selected_strategy_key: string }>(
    '/api/strategies/catalog/selection',
    'PUT',
    { strategy_key: strategyKey },
  )

export const saveCatalogStrategySetup = (
  strategyKey: string,
  mode: 'paper' | 'live',
  values: Record<string, string | number>,
) =>
  catalogCall<{ ok: true; strategy_instance_id: string; values: Record<string, unknown> }>(
    '/api/strategies/catalog/setup',
    'PUT',
    { strategy_key: strategyKey, mode, values },
  )

export async function refreshRuntimeAccount(): Promise<Record<string, unknown>> {
  const response = await apiFetch('/api/runtime/account/refresh', { method: 'POST' })
  const body = await response.json().catch(() => null) as { detail?: Record<string, unknown> } & Record<string, unknown> | null
  if (!response.ok) return body?.detail ?? body ?? { ok: false, status: 'UNAVAILABLE' }
  return body ?? { ok: false, status: 'UNAVAILABLE' }
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
  operationState?: 'POSITION_OPEN' | 'POSITION_CLOSED' | 'PAPER_ORDER_ACCEPTED' | 'RECONCILIATION_REQUIRED' | 'REJECTED_NO_FRESH_QUOTE' | 'REJECTED' | 'FAILED' | string
  position?: {
    has_open_position?: boolean
    position_id?: string | null
    position_version?: number
    entry_order_id?: string | null
    entry_price?: number | null
    qty?: number
    security_id?: string | null
    trading_symbol?: string | null
    option_side?: string | null
    strike?: number | null
    expiry?: string | null
    broker_sl_price?: number | null
    broker_tp_price?: number | null
  } | null
  portfolio?: {
    available_balance?: number | null
    utilized_amount?: number | null
    session_pnl?: number | null
  } | null
  idempotentReplay?: boolean
  executionResult?: Record<string, unknown>
  normalizedError?: Record<string, unknown> | null
  orderJourney?: Array<Record<string, unknown>>
}

export interface ExitLevelsPayload {
  stopLossPrice: number
  targetPrice: number
  expectedPositionVersion?: number
}

export interface ExitLevelsResponse {
  ok: boolean
  message: string
  activeExitLevels?: ActiveExitLevels
  positionVersion?: number
  normalizedError?: Record<string, unknown> | null
}

export interface QuantityResponse {
  ok: boolean
  message: string
  qty?: number
  lots?: number
  normalizedError?: Record<string, unknown> | null
}

export interface PositionAdjustmentResponse extends QuantityResponse {
  operationState?: string
  positionVersion?: number
  fillPrice?: number
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
  quoteStatus?: string | null
  quoteSource?: string | null
  retryAfterSeconds?: number | null
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
  id?: string
  time: number
  side: 'BUY' | 'SELL'
  option_side?: 'CE' | 'PE' | null
  label: string
  price?: number | null
  approximate?: boolean
  mode?: string | null
  source?: string
  contract?: string | null
  execution_price?: number | null
  pnl?: number | null
  exit_kind?: 'SL' | 'TARGET' | 'REVERSAL' | 'EOD' | 'EXIT' | string | null
}

export async function getNiftyCandles(signal?: AbortSignal): Promise<NiftyCandleSeries> {
  const response = await apiFetch('/api/market/nifty/candles?interval=5m', { cache: 'no-store', signal })
  if (!response.ok) {
    throw new Error(`Could not load NIFTY candles: ${response.status}`)
  }
  return response.json() as Promise<NiftyCandleSeries>
}

export interface MarketSentiment {
  available: boolean
  bullish_percent: number | null
  bearish_percent: number | null
  updated_at: string | null
  cached?: boolean
  stale?: boolean
}

/** NOVA intelligence buy/sell sentiment, proxied+cached by the backend. */
export async function getMarketSentiment(): Promise<MarketSentiment> {
  const response = await apiFetch('/api/market/sentiment', { cache: 'no-store' })
  if (!response.ok) throw new Error(`Could not load sentiment: ${response.status}`)
  return response.json() as Promise<MarketSentiment>
}

export interface NiftyMarkerResponse {
  symbol: string
  trading_date: string
  mode?: string | null
  markers: NiftyTradeMarker[]
}

export async function getNiftyMarkers(mode?: 'paper' | 'live', signal?: AbortSignal): Promise<NiftyMarkerResponse> {
  const path: `/${string}` = mode ? `/api/market/nifty/markers?mode=${mode}` : '/api/market/nifty/markers'
  const response = await apiFetch(path, { cache: 'no-store', signal })
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

export async function getOrderQuotes(lots: number): Promise<{ CE: OrderQuote; PE: OrderQuote }> {
  const params = new URLSearchParams({ lots: String(lots) })
  const response = await apiFetch(`/api/orders/quotes?${params.toString()}` as `/${string}`, { cache: 'no-store' })
  const body = await response.json().catch(() => null) as { quotes?: { CE?: OrderQuote; PE?: OrderQuote } } | null
  if (!response.ok || !body?.quotes?.CE || !body.quotes.PE) {
    throw new Error(`Could not load quotes: ${response.status}`)
  }
  return { CE: body.quotes.CE, PE: body.quotes.PE }
}

export async function postManualEntry(payload: ManualEntryPayload, operationId = newIdempotencyKey()): Promise<ManualOrderResponse> {
  return postManualOrder('/api/orders/manual-entry', payload, operationId)
}

export async function postManualExit(operationId = newIdempotencyKey()): Promise<ManualOrderResponse> {
  return postManualOrder('/api/orders/manual-exit', { reason: 'manual_panel' }, operationId)
}

export async function postManualReverse(lots: number, operationId = newIdempotencyKey()): Promise<ManualOrderResponse> {
  return postManualOrder('/api/orders/manual-reverse', { lots }, operationId)
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

export async function postActiveAddLots(
  payload: { lots: number; expectedPositionVersion: number },
  operationId = newIdempotencyKey(),
): Promise<PositionAdjustmentResponse> {
  return postPositionAdjustment('/api/orders/active-position/add-lots', payload, operationId)
}

export async function postActivePartialExit(
  payload: { lots?: number; percentage?: 25 | 50 | 75; expectedPositionVersion: number },
  operationId = newIdempotencyKey(),
): Promise<PositionAdjustmentResponse> {
  return postPositionAdjustment('/api/orders/active-position/partial-exit', payload, operationId)
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

async function postManualOrder(path: `/${string}`, payload: unknown, operationId: string): Promise<ManualOrderResponse> {
  // Live manual orders require a unique Idempotency-Key so the backend can
  // dedupe a retried request instead of placing a second real order.
  const response = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': operationId },
    body: JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as ManualOrderResponse | null
  if (!response.ok) {
    throw new Error(body?.message || `Manual order failed: ${response.status}`)
  }
  return body ?? { ok: false, message: 'Manual order failed.' }
}

async function postPositionAdjustment(
  path: `/${string}`,
  payload: unknown,
  operationId: string,
): Promise<PositionAdjustmentResponse> {
  const response = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': operationId },
    body: JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as PositionAdjustmentResponse | null
  if (!response.ok) {
    throw new Error(body?.message || `Position operation failed: ${response.status}`)
  }
  return body ?? { ok: false, message: 'Position operation failed.' }
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
    paper_mode?: boolean
    valid_lots?: boolean
    active_credential?: boolean
    /** Lighter gate — only present for NOVA catalog personal-webhook instances. */
    connection_tested?: boolean
    /** Server-observed gates for approved personal-Pine instances. */
    feature_enabled?: boolean
    evaluation_available?: boolean
    c1_approval?: boolean
    compile_success?: boolean
    installation_active?: boolean
    strategy_instance?: boolean
    owner_bound?: boolean
    credential_active?: boolean
    current_credential_binding?: boolean
    approved_version?: boolean
    installation_confirmed?: boolean
    hold_verified?: boolean
    paper_entry_verified?: boolean
    paper_exit_verified?: boolean
    candidate_integrity?: boolean
    source_integrity?: boolean
    strategy_layer_integrity?: boolean
    installation_not_suspended?: boolean
    paper_safe_mode?: boolean
    can_activate: boolean
  }
  /** Safe code for the first failing activation gate, e.g. PAPER_EXIT_NOT_VERIFIED. */
  blocking_code?: string | null
  setup_type?: TradingViewSetupType | null
  requires_managed_setup?: boolean
  installation_status?: string | null
  /** Controlled paper-only verification is running (produces entry/exit evidence). */
  verification_mode?: boolean
  verification_started_at?: string | null
  verification_completed_at?: string | null
  lot_size?: number
  estimated_quantity?: number
  last_signal_time?: string | null
  last_execution_status?: string | null
  webhook_auth_status?: {
    last_failed_at: string | null
    reason: string | null
  }
  has_open_position?: boolean
  selected_for_engine?: boolean
  engine_running?: boolean
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
    cache: method === 'GET' ? 'no-store' : undefined,
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
export interface PineUserAcceptance {
  original_version_id: string
  prompt_version_id: string
  setup_type: TradingViewSetupType
  assumptions: string[]
  reviewed_strategy: true
  understands_static_validation: true
  understands_performance_risk: true
  accepts_paper_only: true
}
export const submitPineVersion = async (strategyId: string, versionId: string, acceptance: PineUserAcceptance) =>
  pineCall<{ version: PineVersion }>(`/api/personal-pine-strategies/${strategyId}/versions/${versionId}/submit` as `/${string}`, 'POST', acceptance)
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
  acceptance?: { prompt_version_id: string; setup_type: TradingViewSetupType; original_version_id: string; validation_report_sha256: string; accepted_at: string; assumptions: string[] } | null
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
  prompt_status: 'DEPLOYED' | 'QUALIFICATION'
  transport_version: string | null
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

export interface AdminPineDiffLine {
  kind: 'added' | 'removed' | 'unchanged'
  text: string
}

export interface AdminPineAnalysis {
  analyzer_version: string
  registry_version: string
  registry_sha256: string
  source_sha256: string
  matched_capabilities: string[]
  unsupported_capabilities: string[]
  warnings: string[]
  blockers: string[]
  admin_review_points: string[]
  effective_capability_level: string
  confidence: string
}

export interface AdminPineConversion {
  id: string
  strategy_id: string
  strategy_name: string
  input_version_id: string
  candidate_version_id: string | null
  source_sha256: string
  candidate_sha256: string | null
  strategy_layer_sha256: string | null
  submitted_at: string | null
  analysis_status: string
  conversion_status: string
  provider: string
  model: string
  provider_mode: string | null
  validation_status: string
  review_status: string
  safe_error_code: string | null
  analysis: AdminPineAnalysis
  provenance: Record<string, unknown>
  validation: PineValidation | null
  conversion_summary: string | null
  warnings: string[]
  unsupported_features: string[]
  action_mapping: Record<string, string>
  original_source?: string
  strategy_layer?: string | null
  final_candidate?: string | null
  transport_source?: string | null
  diff?: AdminPineDiffLine[]
  approval_integrity?: boolean | null
}

export interface AdminPineSubmissionPayload {
  strategy_name: string
  source: string
  original_filename: string
  internal_notes?: string
  options: {
    requested_setup_type: 'USER_MANAGED_TRADINGVIEW' | 'NOVA_MANAGED_TRADINGVIEW'
    intended_symbol: string
    intended_timeframe: string
  }
}

export const listAdminPineConversions = async () =>
  (await pineCall<{ conversions: AdminPineConversion[] }>('/api/admin/pine-conversions')).conversions
export const getAdminPineConversion = async (id: string) =>
  (await pineCall<{ conversion: AdminPineConversion }>(`/api/admin/pine-conversions/${id}` as `/${string}`)).conversion
export const submitAdminPineConversion = async (payload: AdminPineSubmissionPayload) =>
  (await pineCall<{ conversion: AdminPineConversion }>('/api/admin/pine-conversions', 'POST', payload)).conversion
export const runAdminPineConversion = async (id: string) =>
  (await pineCall<{ conversion: AdminPineConversion }>(`/api/admin/pine-conversions/${id}/convert` as `/${string}`, 'POST')).conversion
export const getAdminPineManualPackage = async (id: string) =>
  pineCall<{ package: string; filename: string; package_sha256: string; source_sha256: string }>(
    `/api/admin/pine-conversions/${id}/manual-package` as `/${string}`,
    'POST',
  )
export const submitAdminPineManualResponse = async (id: string, responseJson: string) =>
  (await pineCall<{ conversion: AdminPineConversion }>(
    `/api/admin/pine-conversions/${id}/manual-response` as `/${string}`,
    'POST',
    { response_json: responseJson },
  )).conversion
export const approveAdminPineConversion = async (id: string, reason?: string) =>
  (await pineCall<{ conversion: AdminPineConversion }>(
    `/api/admin/pine-conversions/${id}/approve` as `/${string}`,
    'POST',
    { reason: reason || null },
  )).conversion
export const rejectAdminPineConversion = async (id: string, reason: string) =>
  (await pineCall<{ conversion: AdminPineConversion }>(
    `/api/admin/pine-conversions/${id}/reject` as `/${string}`,
    'POST',
    { reason },
  )).conversion

export interface C2Config {
  enabled: boolean
  webhook_path: string
  live_eligibility: false
  browser_automation: false
}

export interface C2CompileEvidence {
  id: string
  conversion_id: string
  candidate_version_id: string
  result: 'SUCCESS' | 'FAILURE'
  source_sha256: string
  strategy_layer_sha256: string
  candidate_sha256: string
  prompt_version: string
  prompt_sha256: string
  transport_version: string
  transport_sha256: string
  compiler_error_summary: string | null
  setup_notes: string | null
  compiled_at: string
}

export interface C2SetupPackage {
  strategy_name: string
  instance_label: string | null
  mode: 'MANAGED' | 'SELF'
  approved_pine: string
  candidate_sha256: string
  webhook_url: string
  alert_message: string
  credential_display: 'one_time' | 'placeholder'
  expected_hold_behavior: string
  instructions: string
}

export interface C2Installation {
  id: string
  owner_user_id: string
  conversion_id: string
  compile_evidence_id: string
  strategy_id: string
  strategy_name: string
  strategy_version_id: string
  strategy_version: string
  candidate_sha256: string
  source_sha256: string
  mode: 'MANAGED' | 'SELF'
  status: string
  strategy_instance_id: string
  instance_label: string
  instance_status: string
  execution_mode: 'signal_only' | 'paper_live_data'
  credential_status: 'ACTIVE' | 'REVOKED' | 'NOT_GENERATED'
  credential: {
    id: string
    token_prefix: string
    created_at: string
    last_verified_at: string | null
  } | null
  hold_status: 'VERIFIED' | 'AWAITING_HOLD'
  hold_verified_at: string | null
  paper_eligible: boolean
  paper_eligible_at: string | null
  live_eligible: false
  gates: Record<string, boolean>
  blocking_reasons: string[]
  suspended_at: string | null
  created_at: string
  updated_at: string
  setup_package?: C2SetupPackage
  compile?: C2CompileEvidence
}

export interface C2IssuedCredential {
  id: string
  strategy_instance_id: string
  token_prefix: string
  token: string
  created_at: string
  setup_package: C2SetupPackage
}

export interface AdminUserSummary {
  id: string
  email: string
  name: string | null
  is_admin: boolean
}

async function c2Call<T>(path: `/${string}`, method = 'GET', payload?: unknown): Promise<T> {
  const response = await apiFetch(path, {
    method,
    cache: 'no-store',
    headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as ({ ok?: boolean; error?: string } & T) | null
  if (!response.ok || !body?.ok) throw new Error(body?.error || `C2 request failed: ${response.status}`)
  return body
}

export const getC2Config = () =>
  c2Call<C2Config>('/api/strategy-installations/config')

export const getAdminC2Conversion = (conversionId: string) =>
  c2Call<{
    enabled: boolean
    compile: C2CompileEvidence | null
    candidate: {
      conversion_id: string
      strategy_name: string
      candidate_version_id: string
      version: string
      pine: string
      source_sha256: string
      strategy_layer_sha256: string
      candidate_sha256: string
      prompt_version: string
      prompt_sha256: string
      transport_version: string
      transport_sha256: string
    }
  }>(`/api/admin/pine-conversions/${conversionId}/c2` as `/${string}`)

export const recordC2CompileSuccess = (conversionId: string, setupNotes?: string) =>
  c2Call<{ compile: C2CompileEvidence }>(
    `/api/admin/pine-conversions/${conversionId}/compile-success` as `/${string}`,
    'POST',
    { setup_notes: setupNotes?.trim() || null },
  )

export const recordC2CompileFailure = (conversionId: string, compilerErrorSummary: string) =>
  c2Call<{ compile: C2CompileEvidence }>(
    `/api/admin/pine-conversions/${conversionId}/compile-failure` as `/${string}`,
    'POST',
    { compiler_error_summary: compilerErrorSummary },
  )

export async function downloadC2ApprovedPine(conversionId: string) {
  const response = await apiFetch(`/api/admin/pine-conversions/${conversionId}/approved-pine` as `/${string}`)
  if (!response.ok) throw new Error(`Could not download approved Pine: ${response.status}`)
  const disposition = response.headers.get('content-disposition') ?? ''
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? 'approved-candidate.pine'
  return { blob: await response.blob(), filename }
}

export async function listAdminUsers(): Promise<AdminUserSummary[]> {
  const response = await apiFetch('/api/admin/users', { cache: 'no-store' })
  const body = await response.json().catch(() => null) as { users?: AdminUserSummary[]; error?: string } | null
  if (!response.ok || !body?.users) throw new Error(body?.error || 'Could not load installation owners.')
  return body.users
}

export const createC2Installation = (payload: {
  conversion_id: string
  owner_user_id: string
  mode: 'MANAGED' | 'SELF'
  instance_label: string
}) => c2Call<{ installation: C2Installation }>('/api/admin/strategy-installations', 'POST', payload)

export const listAdminC2Installations = () =>
  c2Call<{ installations: C2Installation[] }>('/api/admin/strategy-installations')

export const generateAdminC2Credential = (installationId: string) =>
  c2Call<{ credential: C2IssuedCredential }>(
    `/api/admin/strategy-installations/${installationId}/credential` as `/${string}`,
    'POST',
  )

export const rotateAdminC2Credential = (installationId: string) =>
  c2Call<{ credential: C2IssuedCredential }>(
    `/api/admin/strategy-installations/${installationId}/credential/rotate` as `/${string}`,
    'POST',
  )

export const revokeAdminC2Credential = (installationId: string) =>
  c2Call<{ installation: C2Installation }>(
    `/api/admin/strategy-installations/${installationId}/credential/revoke` as `/${string}`,
    'POST',
  )

export const suspendAdminC2Installation = (installationId: string, reason: string) =>
  c2Call<{ installation: C2Installation }>(
    `/api/admin/strategy-installations/${installationId}/suspend` as `/${string}`,
    'POST',
    { reason },
  )

export const listMyC2Installations = async () =>
  (await c2Call<{ installations: C2Installation[] }>('/api/strategies/my-installations')).installations

export const getMyC2Installation = async (installationId: string) =>
  (await c2Call<{ installation: C2Installation }>(
    `/api/strategies/my-installations/${installationId}` as `/${string}`,
  )).installation

export const generateSelfC2Credential = async (installationId: string) =>
  (await c2Call<{ credential: C2IssuedCredential }>(
    `/api/strategies/my-installations/${installationId}/self-credential` as `/${string}`,
    'POST',
  )).credential

export const rotateSelfC2Credential = async (installationId: string) =>
  (await c2Call<{ credential: C2IssuedCredential }>(
    `/api/strategies/my-installations/${installationId}/credential/rotate` as `/${string}`,
    'POST',
  )).credential

export const revokeSelfC2Credential = async (installationId: string) =>
  (await c2Call<{ installation: C2Installation }>(
    `/api/strategies/my-installations/${installationId}/credential/revoke` as `/${string}`,
    'POST',
  )).installation

export const getPineConversionConfig = () => pineCall<PineConversionConfig>('/api/pine-conversions/config')
export const generatePineConversionPackage = (strategyId: string, versionId: string) =>
  pineCall<{ package: string; filename: string; package_sha256: string }>(`/api/personal-pine-strategies/${strategyId}/versions/${versionId}/conversion-package` as `/${string}`, 'POST')
export const createPineConversion = (strategyId: string, versionId: string) =>
  pineCall<{ conversion: PineConversion; reused: boolean }>(`/api/personal-pine-strategies/${strategyId}/versions/${versionId}/convert` as `/${string}`, 'POST', { consent: true })
export const getPineConversion = (id: string) => pineCall<{ conversion: PineConversion }>(`/api/pine-conversions/${id}` as `/${string}`)
export const acceptPineConversion = (id: string) => pineCall<{ conversion: PineConversion }>(`/api/pine-conversions/${id}/accept` as `/${string}`, 'POST')
export const rejectPineConversion = (id: string) => pineCall<{ conversion: PineConversion }>(`/api/pine-conversions/${id}/reject` as `/${string}`, 'POST', {})
export const retryPineConversion = (id: string) => pineCall<{ conversion: PineConversion }>(`/api/pine-conversions/${id}/retry` as `/${string}`, 'POST', { consent: true })

export type TradingViewSetupType = 'USER_MANAGED_TRADINGVIEW' | 'NOVA_MANAGED_TRADINGVIEW'
export interface TradingViewSetup {
  id: string
  user_id: string
  strategy_instance_id: string
  approved_version_id: string
  setup_type: TradingViewSetupType
  approved_source_sha256?: string | null
  requested_timeframe?: string | null
  status: string
  ready_for_paper: boolean
  blocking_step: string | null
  who_acts_next: string
  blocking_reason: string | null
  user_reported_compiled_at: string | null
  hold_verified_at: string | null
  paper_entry_verified_at: string | null
  paper_exit_verified_at: string | null
  gates: Record<string, boolean>
  updated_at: string | null
  credential_status?: string
  installation_metadata?: Record<string, string> | null
  admin_notes?: string | null
}

async function setupCall(path: `/${string}`, method = 'GET', payload?: unknown): Promise<TradingViewSetup> {
  const response = await apiFetch(path, {
    method, cache: 'no-store',
    headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null) as { ok?: boolean; setup?: TradingViewSetup; error?: string } | null
  if (!response.ok || !body?.ok || !body.setup) throw new Error(body?.error || `TradingView setup request failed: ${response.status}`)
  return body.setup
}

export const createTradingViewSetup = (instanceId: string, setup_type: TradingViewSetupType) =>
  setupCall(`/api/strategy-instances/${instanceId}/tradingview-setup` as `/${string}`, 'POST', { setup_type })
export const getTradingViewSetup = (instanceId: string) =>
  setupCall(`/api/strategy-instances/${instanceId}/tradingview-setup` as `/${string}`)
export const reportTradingViewCompiled = (instanceId: string) =>
  setupCall(`/api/strategy-instances/${instanceId}/tradingview-setup/compiled` as `/${string}`, 'POST', { compiled: true })

export async function listManagedTradingViewSetups(): Promise<TradingViewSetup[]> {
  const response = await apiFetch('/api/admin/managed-tradingview-setups', { cache: 'no-store' })
  const body = await response.json().catch(() => null) as { ok?: boolean; setups?: TradingViewSetup[]; error?: string } | null
  if (!response.ok || !body?.ok || !body.setups) throw new Error(body?.error || 'Could not load managed setup queue.')
  return body.setups
}

export const recordManagedTradingViewInstallation = (setupId: string, payload: {
  installed_version_hash: string; workspace_reference: string; alert_reference: string
  symbol: string; timeframe: string; installed_at: string
}) => setupCall(`/api/admin/managed-tradingview-setups/${setupId}/installation` as `/${string}`, 'POST', payload)

export const updateManagedTradingViewState = (setupId: string, status: 'SETUP_PENDING' | 'INSTALLATION_IN_PROGRESS' | 'ALERT_TEST_PENDING' | 'PAPER_VERIFICATION_PENDING' | 'BLOCKED' | 'RETIRED', reason?: string) =>
  setupCall(`/api/admin/managed-tradingview-setups/${setupId}/state` as `/${string}`, 'POST', { status, reason: reason ?? null })

/** Admin-only: provision the private credential for a NOVA-managed setup.
 * The plaintext token is returned exactly once and must not be persisted. */
export const generateManagedTradingViewCredential = (setupId: string, rotate = false) =>
  credentialCall(`/api/admin/managed-tradingview-setups/${setupId}/credential${rotate ? '?rotate=true' : ''}` as `/${string}`, 'POST')

export interface EngineStrategy {
  instance_id: string
  display_name: string
  strategy_code: string | null
  strategy_version: string | null
  source_type: 'NOVA_SHARED' | 'NOVA_HOSTED_PERSONAL' | 'PERSONAL_TRADINGVIEW'
  setup_type: TradingViewSetupType | null
  status: string
  instance_status: string
  mode: 'paper' | 'live'
  execution_mode: string
  paper_eligible: boolean
  live_eligible: false
  readiness: Record<string, boolean> | null
  lots: number
  credential_status: 'active' | 'revoked' | 'missing' | 'not_required'
  installation_status: string | null
  verification_mode?: boolean
  selectable: boolean
  selected?: boolean
  blocking_reason: string | null
  owner: 'self'
}

export async function getEngineStrategies(): Promise<{ strategies: EngineStrategy[]; selected_instance_id: string | null }> {
  const response = await apiFetch('/api/engine/strategies', { cache: 'no-store' })
  const body = await response.json().catch(() => null) as { ok?: boolean; strategies?: EngineStrategy[]; selected_instance_id?: string | null; error?: string } | null
  if (!response.ok || !body?.ok || !body.strategies) throw new Error(body?.error || 'Could not load engine strategies.')
  return { strategies: body.strategies, selected_instance_id: body.selected_instance_id ?? null }
}

export async function getEngineSelection(): Promise<{ selected_instance_id: string | null; selected: EngineStrategy | null }> {
  const response = await apiFetch('/api/engine/selection', { cache: 'no-store' })
  const body = await response.json().catch(() => null) as { ok?: boolean; selected_instance_id?: string | null; selected?: EngineStrategy | null; error?: string } | null
  if (!response.ok || !body?.ok) throw new Error(body?.error || 'Could not load engine selection.')
  return { selected_instance_id: body.selected_instance_id ?? null, selected: body.selected ?? null }
}

export async function setEngineSelection(strategy_instance_id: string): Promise<string> {
  const response = await apiFetch('/api/engine/selection', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ strategy_instance_id }),
  })
  const body = await response.json().catch(() => null) as { ok?: boolean; selected_instance_id?: string; error?: string } | null
  if (!response.ok || !body?.ok || !body.selected_instance_id) throw new Error(body?.error || 'Could not select strategy.')
  return body.selected_instance_id
}

export const startInstanceVerification = (instanceId: string) =>
  instanceCall(`/api/strategy-instances/${instanceId}/verification-mode` as `/${string}`, 'POST')

export const startManagedTradingViewVerification = (setupId: string) =>
  instanceCall(`/api/admin/managed-tradingview-setups/${setupId}/verification-mode` as `/${string}`, 'POST')
