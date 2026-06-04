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
