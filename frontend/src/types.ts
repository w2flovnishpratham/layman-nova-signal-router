export type SetupState =
  | 'IDLE'
  | 'MODE_PICKED'
  | 'STRATEGY_PICKED'
  | 'BROKER_CONNECTED'
  | 'RISK_CONFIGURED'
  | 'EXITS_CONFIGURED'
  | 'READY_TO_LAUNCH'
  | 'LIVE'
  | 'PAUSED'
  | 'ENDED'

export type WsStatus = 'live' | 'degraded' | 'down'
export type Tone = 'up' | 'down' | 'flat'
export type EngineMode = 'paper' | 'live'
export type SetupFlowStep = 'mode' | 'strategy' | 'broker' | 'side' | 'lots' | 'exits' | 'limits' | 'confirm' | 'complete'

export type ServerEventType =
  | 'bot.message'
  | 'system.event'
  | 'setup.state'
  | 'setup.info'
  | 'mode.update'
  | 'signal.received'
  | 'strategy.signal.received'
  | 'strategy.job'
  | 'strategy.job.queued'
  | 'strategy.job.processing'
  | 'strategy.job.completed'
  | 'strategy.job.skipped'
  | 'strategy.job.failed'
  | 'paper.position.opened'
  | 'paper.position.closed'
  | 'order.placed'
  | 'order.filled'
  | 'order.rejected'
  | 'tick.pnl'
  | 'position.update'
  | 'funds.update'
  | 'trade.exit'
  | 'session.error'
  | 'session.eod'

export type SideFilter = 'CE' | 'PE' | 'BOTH'

export interface ServerEvent<TData = Record<string, unknown>> {
  id: string
  type: ServerEventType
  ts: string
  data: TData
}

export interface SessionBootstrap {
  sessionId: string
  userId?: string | null
  sessionToken: string
  webhookSecret: string | null
  webhookSecretAvailableOnce: boolean
  webhookUrl: string
  lotSize: number
}

export interface SafetyStatus {
  mode: EngineMode | null
  live_orders_enabled: boolean
  auth_required: boolean
  authenticated: boolean
  webhook_hmac_required: boolean
  webhook_replay_protection: boolean
  legacy_unsigned_webhooks_disabled: boolean
  worker_role: string
  paper_workers_enabled: boolean
  trading_workers_enabled: boolean
  trading_worker_policy_safe: boolean
  authenticated_live_workers_ready: boolean
  executor_routing_enabled: boolean
  unique_egress_required: boolean
  egress_assigned: boolean
  executor_egress_verified: boolean
  signing_relay_configured: boolean
  market_hours_valid: boolean
  single_operator_live_allowed: boolean
  public_live_launch_allowed: boolean
  reasons_live_blocked: string[]
  broker: {
    connected: boolean
    client_id_masked: string | null
    access_token_present: boolean
    token_saved_at: string | null
    token_age_minutes: number | null
    token_estimated_expiry_at: string | null
    token_expired: boolean | null
    token_warn: boolean | null
    last_validated_at: string | null
    status: string | null
  }
  webhook: {
    url: string
    secret_set: boolean
    secret_masked: string | null
    last_received_at: string | null
    last_status: string | null
    last_rejection_category: string | null
  }
  runtime: {
    emergency_stop: boolean
    global_kill_switch: boolean
    allow_entry: boolean
    allow_exit: boolean
  }
}

export interface RiskConfig {
  maxTrades: number | null
  maxLoss: number | null
  lots: number
  side: SideFilter
}

export interface ExitRules {
  mode: 'flip_only' | 'flip_tp' | 'custom'
  targetProfit: number | null
  targetPct?: number
  stopLossPct?: number
}

export interface TradeConfig {
  engineMode?: EngineMode
  paper?: {
    startingBalance: number
  }
  strategy?: 'supertrend'
  broker?: {
    clientId: string
    status: string
  }
  risk?: RiskConfig
  exits?: ExitRules
  live?: {
    confirmed: boolean
  }
}

export interface SetupDraft {
  engineMode: EngineMode | null
  paperStartingBalance: number
  clientId: string
  accessToken: string
  side: SideFilter
  lots: number
  exitMode: ExitRules['mode']
  targetPct: number
  stopLossPct: number
  maxTrades: number
  maxLoss: number
}

export interface SessionSnapshot {
  sessionId: string
  userId?: string | null
  state: SetupState
  config: TradeConfig
  activeTrade: ActiveTrade | null
  events: ServerEvent[]
}

export interface AuthUser {
  id: string
  email: string
  name?: string | null
  avatarUrl?: string | null
  planTier?: string
}

export interface AuthStatus {
  authRequired: boolean
  googleConfigured: boolean
  authenticated: boolean
  isAdmin: boolean
  loginUrl: string
  user: AuthUser | null
  csrfToken?: string | null
}

export interface StrategyCatalogItem {
  id: number
  strategyCode: string
  name: string
  description: string
  riskLevel: 'low' | 'medium' | 'high'
  market: string
  instrumentType: string
  timeframe: string
  active: boolean
  paperAllowed: boolean
  liveAllowed: boolean
  activeVersion: number | null
}

export interface StrategySubscription {
  id: number
  strategyId: number
  strategyCode: string
  strategyName: string
  strategyVersion: number
  mode: 'paper' | 'live'
  status: 'active' | 'paused'
  risk: {
    maxQty: number | null
    maxDailyLoss: number | null
    maxTradesPerDay: number | null
    allowedOptionSide: SideFilter
  }
  lastSignalAt: string | null
  signalsToday: number
  paperTradesToday: number
  skippedToday: number
  createdAt: string
  updatedAt: string
}

export interface StrategySignalJob {
  id: number
  subscriptionId: number
  strategySignalId: number
  strategyCode: string | null
  strategyName: string | null
  mode: 'paper' | 'live'
  status: string
  reasonCode: string | null
  reasonMessage: string | null
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  attemptCount: number
  signal: {
    signalId: string
    symbol: string
    action: string
    optionType: 'CE' | 'PE' | null
    strike: number | null
    expiry: string | null
    price: number | null
    receivedAt: string
  } | null
  paperOrder: {
    id: number
    qty: number
    fillPrice: number
    status: string
    createdAt: string
  } | null
  liveOrder: {
    id: number
    executorNodeId: number
    status: string
    correlationId: string
    dryRun: boolean
    dhanOrderId: string | null
    createdAt: string
  } | null
}

export interface LiveReadiness {
  strategyCode: string
  ready: boolean
  reasonCode: string | null
  reasonMessage: string | null
  brokerConnected: boolean
  riskSaved: boolean
  executorAssigned: boolean
  executorVerified: boolean
  executorCode: string | null
  reservedIp: string | null
  whitelistStatus: string
  approvalActive: boolean
  dryRunOnly: boolean
  liveOrdersEnabled: boolean
}

export interface ExecutorAssignment {
  id: number
  userId: string
  userEmail: string | null
  executorNodeId: number
  status: string
  lastVerifiedEgressIp: string | null
  verifiedAt: string | null
}

export interface ExecutorNodeView {
  id: number
  executorCode: string
  provider: string
  dropletName: string
  region: string
  reservedIp: string
  status: string
  lastHealthStatus: string | null
  lastEgressIp: string | null
  lastSeenAt: string | null
  assignment: ExecutorAssignment | null
}

export interface PilotUser {
  id: string
  email: string
  name: string | null
}

export interface LivePilotApproval {
  id: number
  userId: string
  strategyId: number
  strategyVersionId: number
  status: string
  maxQty: number
  maxTradesPerDay: number
  maxDailyLoss: number
  allowedOptionSide: SideFilter
  expiresAt: string
}

export interface PaperPosition {
  id: number
  subscriptionId: number
  strategyId: number
  strategyVersionId: number
  symbol: string
  optionType: 'CE' | 'PE'
  strike: number
  expiry: string
  side: 'LONG'
  quantity: number
  avgEntryPrice: number
  status: 'open' | 'closed' | 'cancelled'
  openedAt: string
  closedAt: string | null
  realizedPnl: number
  sourceSignalId: number
  lastSignalJobId: number
}

export interface PaperLedgerEntry {
  id: number
  subscriptionId: number
  strategyId: number
  strategySignalId: number
  userSignalJobId: number
  paperPositionId: number | null
  entryType: 'order_fill' | 'position_close' | 'pnl_adjustment' | 'risk_block'
  amount: number
  quantity: number | null
  price: number | null
  realizedPnl: number
  description: string
  createdAt: string
}

export interface WorkerQueueJob {
  id: number
  jobType: string
  dedupeKey: string | null
  payload: Record<string, number>
  status: 'queued' | 'processing' | 'succeeded' | 'failed' | 'dead' | 'cancelled'
  priority: number
  attemptCount: number
  maxAttempts: number
  availableAt: string
  lockedBy: string | null
  lockedUntil: string | null
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  lastError: string | null
}

export interface WorkerHeartbeat {
  workerId: string
  role: string
  hostname: string
  pid: number
  status: string
  startedAt: string
  lastSeenAt: string
  metadata: Record<string, unknown>
}

export interface SrSuggestion {
  available: boolean
  accepted?: boolean
  source?: string
  stopLossPrice?: number
  targetPrice?: number
  message?: string
  basis?: Record<string, unknown>
}

export interface ActiveExitLevels {
  source?: string
  stopLossPrice?: number
  targetPrice?: number
  acceptedAt?: string
}

export interface ActiveTrade {
  mode?: EngineMode
  symbol: string
  strike: number
  optType: 'CE' | 'PE'
  qty: number
  avgPrice: number
  ltp: number
  pnl: number
  pnlPct?: number
  expiry?: string
  exitOn?: string
  securityId?: string
  verifyUrl?: string
  ltpDirection?: Tone
  orderId: string
  exchOrderId?: string
  sourceLtp?: number
  simulatedCharges?: number
  slippagePercent?: number
  srSuggestion?: SrSuggestion
  activeExitLevels?: ActiveExitLevels | null
  correlationId: string
  status: 'OPEN' | 'CLOSED'
}

export interface ClientCommand<TData = unknown> {
  type: string
  data: TData
}

export type LocalMessageType = 'client.summary' | 'client.message'
export type RenderableMessageType = ServerEventType | LocalMessageType

export interface RenderableMessage<TData = Record<string, unknown>> {
  id: string
  type: RenderableMessageType
  ts: string
  data: TData
}

export interface SetupInfo {
  sessionId: string
  webhookUrl: string
  webhookSecret: string | null
  webhookSecretMasked?: string | null
  webhookSecretAvailableOnce?: boolean
}
