import { create } from 'zustand'
import { exitModeLabel } from '../lib/format'
import { contractsForLots, DEFAULT_NIFTY_LOT_SIZE } from '../lib/trading'
import type {
  ActiveTrade,
  EngineMode,
  MarketSnapshot,
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
import type { ManualOrderResponse } from '../api'
import type { RuntimeStatus } from '../api'

interface SessionStore {
  session: SessionBootstrap | null
  setupFlowStep: SetupFlowStep
  setupDraft: SetupDraft
  setupState: SetupState
  engineMode: EngineMode | null
  config: TradeConfig
  messages: RenderableMessage[]
  activeTrade: ActiveTrade | null
  marketSnapshot: MarketSnapshot | null
  marketSnapshotSource: 'push' | 'rest' | null
  lastTradePushAt: number
  lastMarketPushAt: number
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
  hiddenRecentMessages: RenderableMessage[]
  recentActivityVisible: boolean
  setTheme: (theme: string) => void
  setWsStatus: (status: WsStatus) => void
  setBootError: (message: string) => void
  markCommandSent: () => void
  resetSession: () => void
  showRecentActivity: () => void
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
  marketSnapshot: null,
  marketSnapshotSource: null,
  lastTradePushAt: 0,
  lastMarketPushAt: 0,
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
  hiddenRecentMessages: [],
  recentActivityVisible: false,

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
      marketSnapshot: null,
      marketSnapshotSource: null,
      lastTradePushAt: 0,
      lastMarketPushAt: 0,
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
      hiddenRecentMessages: [],
      recentActivityVisible: false,
    })
  },

  showRecentActivity: () => set((state) => ({
    recentActivityVisible: true,
    messages: appendMessages(state.messages, state.hiddenRecentMessages),
    hiddenRecentMessages: [],
  })),

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
    if (typeof window !== 'undefined' && (
      event.type === 'order.filled'
      || event.type === 'trade.exit'
      || event.type === 'position.update'
      || event.type === 'session.eod'
      || event.type === 'session.error'
      || event.type === 'order.rejected'
      || event.type === 'system.event'
      || event.type === 'bot.message'
      || event.type === 'market.candles'
      || event.type === 'market.candle.upsert'
    )) {
      window.dispatchEvent(new CustomEvent('nova:terminal-delta', { detail: event }))
    }
  },

}))

export function initializePreferences(): void {
  document.documentElement.dataset.theme = initialTheme
  document.documentElement.dataset.motion = initialMotion
}

/** Apply the same authoritative manual-order response to every trading panel. */
export function applyManualOrderHydration(response: ManualOrderResponse): void {
  const position = response.position
  if (response.operationState === 'POSITION_CLOSED') {
    useSessionStore.setState({
      activeTrade: null,
      wallet: optionalNumber(response.portfolio?.available_balance, useSessionStore.getState().wallet),
      paperBalance: optionalNumber(response.portfolio?.available_balance, useSessionStore.getState().paperBalance),
      marginUtilized: optionalNumber(response.portfolio?.utilized_amount, 0),
      sessionPnl: optionalNumber(response.portfolio?.session_pnl, useSessionStore.getState().sessionPnl) ?? 0,
    })
    return
  }
  if (response.operationState !== 'POSITION_OPEN' || !position?.has_open_position) return
  const current = useSessionStore.getState()
  const entryPrice = Number(position.entry_price ?? 0)
  const qty = Number(position.qty ?? 0)
  const symbol = position.trading_symbol || position.security_id || 'NIFTY option'
  useSessionStore.setState({
    activeTrade: normalizeTrade({
      positionId: position.position_id ?? undefined,
      positionVersion: position.position_version,
      symbol,
      strike: Number(position.strike ?? 0),
      optType: position.option_side === 'PE' ? 'PE' : 'CE',
      qty,
      avgPrice: entryPrice,
      ltp: entryPrice,
      pnl: 0,
      securityId: position.security_id ?? undefined,
      orderId: position.entry_order_id ?? undefined,
      expiry: position.expiry ?? undefined,
      status: 'OPEN',
    }, current.config, current.session?.lotSize),
    wallet: optionalNumber(response.portfolio?.available_balance, current.wallet),
    paperBalance: optionalNumber(response.portfolio?.available_balance, current.paperBalance),
    marginUtilized: optionalNumber(response.portfolio?.utilized_amount, current.marginUtilized),
    sessionPnl: optionalNumber(response.portfolio?.session_pnl, current.sessionPnl) ?? current.sessionPnl,
  })
}

/** Merge the owner-scoped runtime poll without overwriting a newer push. */
export function applyRuntimeHydration(status: RuntimeStatus, requestStartedAt = Date.now()): void {
  // Defense in depth: the API layer already rejects an incomplete snapshot
  // before it reaches any caller, but never trust an external object's shape
  // twice removed from its own type declaration.
  if (!status?.engine || !status.pnl || !status.config || !status.position) return
  const current = useSessionStore.getState()
  const engineMode = status.engine.mode
  const setupState: SetupState = status.engine.state === 'RUNNING'
    ? (status.engine.accepting_signals ? 'LIVE' : 'PAUSED')
    : status.engine.state === 'STOPPING'
      ? 'PAUSED'
      : 'IDLE'
  const base = {
    setupState,
    engineMode,
    wallet: optionalNumber(status.pnl.available_balance, current.wallet),
    paperBalance: engineMode === 'paper'
      ? optionalNumber(status.pnl.available_balance, current.paperBalance)
      : current.paperBalance,
    marginUtilized: optionalNumber(status.pnl.utilized_amount, current.marginUtilized),
    sessionPnl: optionalNumber(status.pnl.session, current.sessionPnl) ?? current.sessionPnl,
    realizedPnl: optionalNumber(status.pnl.realized, current.realizedPnl) ?? current.realizedPnl,
    unrealizedPnl: optionalNumber(status.pnl.unrealized, 0) ?? 0,
  }
  if (requestStartedAt < current.lastTradePushAt) {
    useSessionStore.setState(base)
    return
  }
  const position = status.position
  const risk = position.risk
  const activeTrade = position.has_open_position
    ? normalizeTrade({
      positionId: position.position_id ?? undefined,
      positionVersion: position.position_version,
      mode: engineMode ?? undefined,
      symbol: position.trading_symbol || 'NIFTY option',
      strike: Number(position.strike ?? 0),
      optType: position.option_side === 'PE' ? 'PE' : 'CE',
      qty: Number(position.qty ?? 0),
      avgPrice: Number(position.entry_price ?? 0),
      ltp: Number(position.ltp.value ?? position.entry_price ?? 0),
      pnl: Number(position.unrealized_pnl ?? 0),
      expiry: position.expiry ?? undefined,
      securityId: position.security_id ?? undefined,
      orderId: position.entry_order_id ?? 'paper-position',
      activeExitLevels: risk && (risk.stop_price != null || risk.target_price != null)
        ? {
          source: risk.source ?? (risk.server_managed ? 'server_monitor' : undefined),
          stopLossPrice: risk.stop_price ?? undefined,
          targetPrice: risk.target_price ?? undefined,
        }
        : null,
      riskArmed: risk?.armed,
      riskSource: risk?.source,
      quoteSource: position.ltp.source,
      quoteReceivedAt: position.ltp.received_at,
      quoteStale: position.ltp.stale,
      quoteStatus: position.ltp.status,
      quoteAgeSeconds: position.ltp.age_seconds,
      correlationId: '',
      status: 'OPEN',
    }, current.config, current.session?.lotSize)
    : null
  useSessionStore.setState({ ...base, activeTrade })
}

/** Use the existing market poll as recovery when no newer push won the race. */
export function applyRestMarketSnapshot(snapshot: MarketSnapshot, requestStartedAt = Date.now()): void {
  const current = useSessionStore.getState()
  if (requestStartedAt < current.lastMarketPushAt) return
  useSessionStore.setState({ marketSnapshot: snapshot, marketSnapshotSource: 'rest' })
}

export function motionConfigMode(): 'always' | 'never' | 'user' {
  if (initialMotion === 'reduced') return 'always'
  if (initialMotion === 'system') return 'user'
  return 'never'
}

function reduceSessionEvent(state: SessionStore, event: ServerEvent): Partial<SessionStore> {
  if (isRestoredRecentEvent(event) && !state.recentActivityVisible) {
    if (event.type === 'system.event' && event.data.restoredRecentHeader) {
      return {
        typing: false,
        messages: appendMessage(state.messages, event),
      }
    }
    return {
      typing: false,
      hiddenRecentMessages: appendMessage(state.hiddenRecentMessages, event),
    }
  }

  if (event.type === 'setup.state') {
    const data = event.data as { state?: SetupState; config?: TradeConfig; lotSize?: number }
    const nextState = isSetupState(data.state) ? data.state : state.setupState
    const nextConfig = isTradeConfig(data.config) ? data.config : state.config
    const lotSize = Number(data.lotSize)
    const nextSession = state.session && Number.isFinite(lotSize) && lotSize > 0
      ? { ...state.session, lotSize }
      : state.session
    return {
      setupState: nextState,
      config: nextConfig,
      engineMode: nextConfig.engineMode ?? state.engineMode,
      setupFlowStep: nextFlowStep(state.setupFlowStep, nextState, nextConfig),
      lastSetupError: '',
      session: nextSession,
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
    const data = event.data as { state?: SetupState }
    if (data.state === 'MODE_PICKED' && state.config.engineMode === 'live') {
      return { typing: false }
    }
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
      messages: appendMessage(state.messages, event),
      lastTradePushAt: Date.now(),
    }
  }

  if (event.type === 'market.snapshot') {
    return {
      marketSnapshot: event.data as unknown as MarketSnapshot,
      marketSnapshotSource: 'push',
      lastMarketPushAt: Date.now(),
    }
  }

  if (event.type === 'market.candles' || event.type === 'market.candle.upsert') {
    // NiftyLiveChart reads this straight off the nova:terminal-delta window
    // event dispatched below, not off the store -- nothing to reduce here,
    // and without this early return it would fall through to the default
    // case and get appended to the chat message log every ~20s.
    return {}
  }

  if (event.type === 'tick.pnl') {
    const patch = event.data as Partial<ActiveTrade>
    // A tick is a P&L update for an EXISTING position, never a position creator.
    // After confirmed flat (activeTrade === null) a delayed tick must not
    // resurrect the closed trade; a genuinely new position arrives via
    // order.filled / the position snapshot, which are the authoritative sources.
    const baseTrade = state.activeTrade
    if (!baseTrade) return {}
    const previousLtp = baseTrade.ltp
    const nextLtp = Number(patch.ltp ?? previousLtp)
    const direction: ActiveTrade['ltpDirection'] = nextLtp > previousLtp ? 'up' : nextLtp < previousLtp ? 'down' : 'flat'
    const nextPnl = Number(patch.pnl ?? baseTrade.pnl)
    const patchedPnlPct = optionalNumber(patch.pnlPct, null)
    const activeTrade = {
      ...baseTrade,
      ...patch,
      ltp: nextLtp,
      ltpDirection: direction,
      pnl: nextPnl,
      pnlPct: patchedPnlPct ?? calculatePnlPct(nextPnl, baseTrade.avgPrice, baseTrade.qty),
    }
    return {
      activeTrade,
      sessionPnl: nextPnl,
      unrealizedPnl: nextPnl,
      lastTradePushAt: Date.now(),
    }
  }

  if (event.type === 'funds.update') {
    const data = event.data as {
      wallet?: number | null
      availableBalance?: number | null
      utilizedAmount?: number | null
      sessionPnl?: number | null
      realizedPnl?: number | null
      success?: boolean
    }
    if (data.success === false) {
      return { wallet: null, marginUtilized: null }
    }
    return {
      wallet: optionalNumber(data.wallet ?? data.availableBalance, state.wallet),
      paperBalance: state.engineMode === 'paper'
        ? optionalNumber(data.wallet ?? data.availableBalance, state.paperBalance)
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
      lastTradePushAt: Date.now(),
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
    positionId: data.positionId,
    positionVersion: data.positionVersion,
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
    sourceLtp: optionalNumber(data.sourceLtp, null) ?? undefined,
    simulatedCharges: optionalNumber(data.simulatedCharges, null) ?? undefined,
    slippagePercent: optionalNumber(data.slippagePercent, null) ?? undefined,
    srSuggestion: normalizeSrSuggestion(data.srSuggestion),
    activeExitLevels: normalizeActiveExitLevels(data.activeExitLevels),
    riskArmed: data.riskArmed,
    riskSource: data.riskSource,
    quoteSource: data.quoteSource,
    quoteReceivedAt: data.quoteReceivedAt,
    quoteStale: data.quoteStale,
    quoteStatus: data.quoteStatus,
    quoteAgeSeconds: data.quoteAgeSeconds,
    correlationId: data.correlationId ?? '',
    status: data.status ?? 'OPEN',
  }
}

function nextFlowStep(current: SetupFlowStep, backendState: SetupState, config: TradeConfig): SetupFlowStep {
  if (backendState === 'MODE_PICKED') {
    if (current !== 'mode') return current
    return config.engineMode === 'live' ? 'live_access' : 'strategy'
  }
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function normalizeSrSuggestion(value: unknown): ActiveTrade['srSuggestion'] {
  if (!isRecord(value)) return undefined
  const stopLossPrice = optionalNumber(value.stopLossPrice ?? value.stop_loss_price ?? value.sl, null) ?? undefined
  const targetPrice = optionalNumber(value.targetPrice ?? value.target_price ?? value.tp, null) ?? undefined
  return {
    available: Boolean(value.available),
    accepted: Boolean(value.accepted),
    source: typeof value.source === 'string' ? value.source : undefined,
    stopLossPrice,
    targetPrice,
    message: typeof value.message === 'string' ? value.message : undefined,
    basis: isRecord(value.basis) ? value.basis : undefined,
  }
}

function normalizeActiveExitLevels(value: unknown): ActiveTrade['activeExitLevels'] {
  if (!isRecord(value)) return undefined
  return {
    source: typeof value.source === 'string' ? value.source : undefined,
    stopLossPrice: optionalNumber(value.stopLossPrice ?? value.stop_loss_price ?? value.sl, null) ?? undefined,
    targetPrice: optionalNumber(value.targetPrice ?? value.target_price ?? value.tp, null) ?? undefined,
    acceptedAt: typeof value.acceptedAt === 'string' ? value.acceptedAt : undefined,
  }
}

function appendMessage(messages: RenderableMessage[], message: RenderableMessage): RenderableMessage[] {
  if (message.type === 'setup.info') return messages
  return [...messages, message].slice(-250)
}

function appendMessages(messages: RenderableMessage[], nextMessages: RenderableMessage[]): RenderableMessage[] {
  return [...messages, ...nextMessages.filter((message) => message.type !== 'setup.info')].slice(-250)
}

function isRestoredRecentEvent(event: ServerEvent): boolean {
  return Boolean((event.data as { restoredRecent?: unknown; restoredRecentHeader?: unknown }).restoredRecent)
    || Boolean((event.data as { restoredRecent?: unknown; restoredRecentHeader?: unknown }).restoredRecentHeader)
}
