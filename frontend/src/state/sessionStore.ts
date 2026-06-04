import { create } from 'zustand'
import { exitModeLabel } from '../lib/format'
import { contractsForLots, DEFAULT_NIFTY_LOT_SIZE } from '../lib/trading'
import type {
  ActiveTrade,
  EngineMode,
  RenderableMessage,
  ServerEvent,
  SessionBootstrap,
  SessionSnapshot,
  SetupDraft,
  SetupFlowStep,
  SetupState,
  TradeConfig,
  WsStatus,
} from '../types'

interface SessionStore {
  session: SessionBootstrap | null
  setupFlowStep: SetupFlowStep
  setupDraft: SetupDraft
  setupState: SetupState
  engineMode: EngineMode | null
  config: TradeConfig
  messages: RenderableMessage[]
  activeTrade: ActiveTrade | null
  wallet: number | null
  paperBalance: number | null
  marginUtilized: number | null
  sessionPnl: number
  realizedPnl: number
  unrealizedPnl: number
  tradesToday: number
  wsStatus: WsStatus
  bootError: string
  lastSetupError: string
  typing: boolean
  theme: string
  seenEvents: Set<string>
  seenSummaries: Set<string>
  setTheme: (theme: string) => void
  setWsStatus: (status: WsStatus) => void
  setBootError: (message: string) => void
  markCommandSent: () => void
  resetSession: () => void
  addUserMessage: (text: string) => void
  updateSetupDraft: (patch: Partial<SetupDraft>) => void
  setSetupFlowStep: (step: SetupFlowStep) => void
  loadBootstrap: (bootstrap: SessionBootstrap) => void
  loadSnapshot: (snapshot: SessionSnapshot) => void
  applyServerEvent: (event: ServerEvent) => void
}

const initialTheme = localStorage.getItem('nova-theme') || 'dark'
const initialMotion = localStorage.getItem('nova-motion') || 'full'
const defaultDraft: SetupDraft = {
  engineMode: null,
  paperStartingBalance: 100000,
  clientId: '',
  accessToken: '',
  side: 'BOTH',
  lots: 1,
  exitMode: 'flip_only',
  targetPct: 5,
  stopLossPct: 10,
  maxTrades: 5,
  maxLoss: 3000,
}

export const useSessionStore = create<SessionStore>((set, get) => ({
  session: null,
  setupFlowStep: 'mode',
  setupDraft: defaultDraft,
  setupState: 'IDLE',
  engineMode: null,
  config: {},
  messages: [],
  activeTrade: null,
  wallet: null,
  paperBalance: null,
  marginUtilized: null,
  sessionPnl: 0,
  realizedPnl: 0,
  unrealizedPnl: 0,
  tradesToday: 0,
  wsStatus: 'down',
  bootError: '',
  lastSetupError: '',
  typing: false,
  theme: initialTheme,
  seenEvents: new Set<string>(),
  seenSummaries: new Set<string>(),

  setTheme: (theme) => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('nova-theme', theme)
    set({ theme })
  },

  setWsStatus: (wsStatus) => set({ wsStatus }),
  setBootError: (bootError) => set({ bootError }),
  markCommandSent: () => set({ typing: true }),
  resetSession: () => {
    const theme = get().theme
    set({
      session: null,
      setupFlowStep: 'mode',
      setupDraft: { ...defaultDraft },
      setupState: 'IDLE',
      engineMode: null,
      config: {},
      messages: [],
      activeTrade: null,
      wallet: null,
      paperBalance: null,
      marginUtilized: null,
      sessionPnl: 0,
      realizedPnl: 0,
      unrealizedPnl: 0,
      tradesToday: 0,
      wsStatus: 'down',
      bootError: '',
      lastSetupError: '',
      typing: false,
      theme,
      seenEvents: new Set<string>(),
      seenSummaries: new Set<string>(),
    })
  },

  addUserMessage: (text) => {
    const message: RenderableMessage = {
      id: `local-${crypto.randomUUID()}`,
      type: 'client.message',
      ts: new Date().toISOString(),
      data: { text },
    }
    set((state) => ({ messages: appendMessage(state.messages, message) }))
  },

  updateSetupDraft: (patch) => set((state) => ({ setupDraft: { ...state.setupDraft, ...patch } })),
  setSetupFlowStep: (setupFlowStep) => set({ setupFlowStep, lastSetupError: '' }),

  loadBootstrap: (session) => set({ session }),

  loadSnapshot: (snapshot) => {
    set({
      setupState: snapshot.state,
      config: snapshot.config,
      engineMode: snapshot.config.engineMode ?? null,
      activeTrade: snapshot.activeTrade,
    })
    snapshot.events.forEach((event) => get().applyServerEvent(event))
  },

  applyServerEvent: (event) => {
    const current = get()
    if (current.seenEvents.has(event.id)) return

    const seenEvents = new Set(current.seenEvents)
    seenEvents.add(event.id)
    const patch = reduceSessionEvent(current, event)
    set({ ...patch, seenEvents })
  },

}))

export function initializePreferences(): void {
  document.documentElement.dataset.theme = initialTheme
  document.documentElement.dataset.motion = initialMotion
}

export function motionConfigMode(): 'always' | 'never' | 'user' {
  if (initialMotion === 'reduced') return 'always'
  if (initialMotion === 'system') return 'user'
  return 'never'
}

function reduceSessionEvent(state: SessionStore, event: ServerEvent): Partial<SessionStore> {
  if (event.type === 'setup.state') {
    const data = event.data as { state?: SetupState; config?: TradeConfig }
    const nextState = isSetupState(data.state) ? data.state : state.setupState
    const nextConfig = isTradeConfig(data.config) ? data.config : state.config
    return {
      setupState: nextState,
      config: nextConfig,
      engineMode: nextConfig.engineMode ?? state.engineMode,
      setupFlowStep: nextFlowStep(state.setupFlowStep, nextState),
      lastSetupError: '',
    }
  }

  if (event.type === 'mode.update') {
    const data = event.data as { engineMode?: EngineMode; paperBalance?: number | null }
    return {
      engineMode: data.engineMode ?? state.engineMode,
      paperBalance: optionalNumber(data.paperBalance, state.paperBalance),
      wallet: data.engineMode === 'paper' ? optionalNumber(data.paperBalance, state.wallet) : state.wallet,
      typing: false,
    }
  }

  if (event.type === 'bot.message') {
    return {
      typing: false,
      messages: appendMessage(state.messages, event),
    }
  }

  if (event.type === 'order.filled') {
    const trade = normalizeTrade(event.data as Partial<ActiveTrade>, state.config, state.session?.lotSize)
    return {
      typing: false,
      activeTrade: trade,
      tradesToday: state.tradesToday + 1,
    }
  }

  if (event.type === 'tick.pnl') {
    const patch = event.data as Partial<ActiveTrade>
    if (!state.activeTrade) return {}
    const previousLtp = state.activeTrade.ltp
    const nextLtp = Number(patch.ltp ?? previousLtp)
    const direction: ActiveTrade['ltpDirection'] = nextLtp > previousLtp ? 'up' : nextLtp < previousLtp ? 'down' : 'flat'
    const activeTrade = {
      ...state.activeTrade,
      ...patch,
      ltp: nextLtp,
      ltpDirection: direction,
      pnlPct: calculatePnlPct(Number(patch.pnl ?? state.activeTrade.pnl), state.activeTrade.avgPrice, state.activeTrade.qty),
    }
    return {
      activeTrade,
      sessionPnl: Number(patch.pnl ?? state.sessionPnl),
    }
  }

  if (event.type === 'funds.update') {
    const data = event.data as {
      wallet?: number | null
      availableBalance?: number | null
      availabelBalance?: number | null
      utilizedAmount?: number | null
      sessionPnl?: number | null
      realizedPnl?: number | null
      success?: boolean
    }
    if (data.success === false) {
      return { wallet: null, marginUtilized: null }
    }
    return {
      wallet: optionalNumber(data.wallet ?? data.availableBalance ?? data.availabelBalance, state.wallet),
      paperBalance: state.engineMode === 'paper'
        ? optionalNumber(data.wallet ?? data.availableBalance ?? data.availabelBalance, state.paperBalance)
        : state.paperBalance,
      marginUtilized: optionalNumber(data.utilizedAmount, state.marginUtilized),
      sessionPnl: optionalNumber(data.sessionPnl, state.sessionPnl) ?? state.sessionPnl,
      realizedPnl: optionalNumber(data.realizedPnl, state.realizedPnl) ?? state.realizedPnl,
    }
  }

  if (event.type === 'position.update') {
    const data = event.data as { sessionPnl?: number; pnl?: number; realizedPnl?: number; unrealizedPnl?: number; tradesToday?: number }
    return {
      sessionPnl: Number(data.sessionPnl ?? data.pnl ?? state.sessionPnl),
      realizedPnl: Number(data.realizedPnl ?? state.realizedPnl),
      unrealizedPnl: Number(data.unrealizedPnl ?? state.unrealizedPnl),
      tradesToday: Number(data.tradesToday ?? state.tradesToday),
    }
  }

  if (event.type === 'trade.exit' || event.type === 'session.eod') {
    return {
      activeTrade: null,
      typing: false,
      messages: appendMessage(state.messages, event),
    }
  }

  if (event.type === 'session.error' || event.type === 'order.rejected' || event.type === 'system.event' || event.type === 'signal.received' || event.type === 'order.placed') {
    const message = String((event.data as { message?: unknown }).message ?? '')
    return {
      typing: false,
      lastSetupError: event.type === 'session.error' ? message : state.lastSetupError,
      messages: appendMessage(state.messages, event),
    }
  }

  return {
    messages: appendMessage(state.messages, event),
  }
}

function normalizeTrade(data: Partial<ActiveTrade>, config: TradeConfig, lotSize = DEFAULT_NIFTY_LOT_SIZE): ActiveTrade {
  const avgPrice = Number(data.avgPrice ?? 0)
  const ltp = Number(data.ltp ?? avgPrice)
  const pnl = Number(data.pnl ?? 0)
  const qty = Number(data.qty ?? contractsForLots(config.risk?.lots ?? 1, lotSize))
  return {
    symbol: data.symbol ?? 'NIFTY option',
    strike: Number(data.strike ?? 0),
    optType: data.optType ?? 'CE',
    qty,
    avgPrice,
    ltp,
    pnl,
    pnlPct: calculatePnlPct(pnl, avgPrice, qty),
    expiry: data.expiry,
    exitOn: data.exitOn ?? exitModeLabel(config.exits?.mode),
    securityId: data.securityId,
    verifyUrl: data.verifyUrl ?? 'https://web.dhan.co/',
    ltpDirection: 'flat',
    orderId: data.orderId ?? 'pending',
    exchOrderId: data.exchOrderId,
    correlationId: data.correlationId ?? '',
    status: data.status ?? 'OPEN',
  }
}

function nextFlowStep(current: SetupFlowStep, backendState: SetupState): SetupFlowStep {
  if (backendState === 'MODE_PICKED') return current === 'mode' ? 'strategy' : current
  if (backendState === 'STRATEGY_PICKED') return current === 'strategy' ? 'broker' : current
  if (backendState === 'BROKER_CONNECTED') return current === 'broker' ? 'side' : current
  if (backendState === 'EXITS_CONFIGURED' || backendState === 'READY_TO_LAUNCH') return 'confirm'
  if (backendState === 'LIVE' || backendState === 'PAUSED' || backendState === 'ENDED') return 'complete'
  return current
}

function isSetupState(value: unknown): value is SetupState {
  return (
    value === 'IDLE' ||
    value === 'MODE_PICKED' ||
    value === 'STRATEGY_PICKED' ||
    value === 'BROKER_CONNECTED' ||
    value === 'RISK_CONFIGURED' ||
    value === 'EXITS_CONFIGURED' ||
    value === 'READY_TO_LAUNCH' ||
    value === 'LIVE' ||
    value === 'PAUSED' ||
    value === 'ENDED'
  )
}

function isTradeConfig(value: unknown): value is TradeConfig {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function calculatePnlPct(pnl: number, avgPrice: number, qty: number): number {
  const exposure = avgPrice * qty
  if (!exposure) return 0
  return (pnl / exposure) * 100
}

function optionalNumber(value: unknown, fallback: number | null): number | null {
  if (value === null || value === undefined || value === '') return fallback
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function appendMessage(messages: RenderableMessage[], message: RenderableMessage): RenderableMessage[] {
  return [...messages, message].slice(-250)
}
