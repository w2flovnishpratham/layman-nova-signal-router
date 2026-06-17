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
export type ErrorSource = 'TRADINGVIEW' | 'DHAN' | 'NOVA_BACKEND' | 'PAPER_ENGINE' | 'RISK_ENGINE' | 'NETWORK' | 'PUBSUB' | 'STATIC_IP'
export type ErrorCategory =
  | 'AUTH'
  | 'TOKEN_EXPIRED'
  | 'STATIC_IP'
  | 'FUNDS'
  | 'MARGIN'
  | 'LTP'
  | 'MARKET_CLOSED'
  | 'PAYLOAD_INVALID'
  | 'DUPLICATE_SIGNAL'
  | 'NO_POSITION'
  | 'POSITION_MISMATCH'
  | 'RATE_LIMIT'
  | 'TIMEOUT'
  | 'ORDER_PENDING'
  | 'PARTIAL_FILL'
  | 'ORDER_UNKNOWN'
  | 'RISK_BLOCK'
  | 'CONFIG_MISSING'
  | 'UNKNOWN'
export type ErrorSeverity = 'info' | 'warning' | 'blocked' | 'critical'

export type ServerEventType =
  | 'bot.message'
  | 'system.event'
  | 'setup.state'
  | 'setup.info'
  | 'mode.update'
  | 'signal.received'
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

export interface NormalizedError {
  errorId: string
  source: ErrorSource
  category: ErrorCategory
  severity: ErrorSeverity
  userTitle: string
  userMessage: string
  technicalMessage?: string
  nextAction: string
  retryable: boolean
  moneyAtRisk: boolean | null
  orderSentToBroker: boolean | null
  actionButtons: string[]
  correlationId?: string | null
  signalId?: string | null
  rawStatusCode?: number | null
  timestamp: string
  mode?: EngineMode | string | null
  debugPack?: Record<string, unknown>
}

export interface OrderJourneyStep {
  label: string
  status: 'complete' | 'pending' | 'warning' | 'failed'
  detail?: string | null
}

export interface MarketSnapshot {
  niftySpot: number | null
  niftySpotSource?: string | null
  dayChangePct: number | null
  marketStatus: 'open' | 'closed'
  lastUpdatedAt: string
  atm?: AtmLtpSnapshot | null
  latestSignal: {
    signalId?: string | null
    action?: string | null
    side?: string | null
    optionSide?: string | null
    receivedAt?: string | null
  }
  activeOptionLtp: number | null
  activeOptionSide?: 'CE' | 'PE' | null
  sparkline: number[]
  markers: Array<{ side?: string | null; optionSide?: string | null; timestamp?: string | null }>
}

export interface AtmOptionLtp {
  ok: boolean
  side: 'CE' | 'PE'
  expiry?: string | null
  strike?: number | null
  securityId?: string | null
  tradingSymbol?: string | null
  lotSize?: number | null
  qty?: number | null
  ltp?: number | null
  ltpSource?: string | null
  ltpStatus?: string | null
  ltpReceivedAt?: string | null
  ltpAgeSeconds?: number | null
  estimatedCost?: number | null
  message?: string | null
  error?: string | null
}

export interface AtmLtpSnapshot {
  ok: boolean
  mode?: EngineMode | string | null
  symbol: 'NIFTY' | string
  marketOpen?: boolean
  niftySpot: number | null
  niftySpotSource?: string | null
  niftySpotStatus?: string | null
  atmStrike: number | null
  strikeStep: number
  lots: number
  computedAt: string
  options: Partial<Record<'CE' | 'PE', AtmOptionLtp>>
  message?: string | null
}

export interface SystemHealth {
  dhan: 'connected' | 'auth_issue' | 'unknown'
  staticIp: 'verified' | 'failed' | 'unknown'
  market: 'open' | 'closed'
  engine: 'listening' | 'paused' | 'idle'
  pubsub: 'healthy' | 'delayed' | 'unknown'
  lastSignalAt?: string | null
  walletOk?: boolean
}

export interface SessionBootstrap {
  sessionId: string
  sessionToken: string
  webhookSecret: string
  webhookUrl: string
  lotSize: number
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
  state: SetupState
  config: TradeConfig
  activeTrade: ActiveTrade | null
  events: ServerEvent[]
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
  webhookSecret: string
}
