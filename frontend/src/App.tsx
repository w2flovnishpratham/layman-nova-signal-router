import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { BarChart3, ChevronLeft, ChevronRight, LineChart, Loader2, Sliders, Wallet, Webhook } from 'lucide-react'
import { AuthScreen } from './components/AuthScreen'
import { EngineLeftPanel } from './components/EngineLeftPanel'
import { EngineListening } from './components/EngineListening'
import { NiftyLiveChart } from './components/NiftyLiveChart'
import { EngineSidebar } from './components/EngineSidebar'
import { Header } from './components/Header'
import { MotionPulseText, MotionSpinner, softEase, useAppReducedMotion } from './components/MotionPrimitives'
import { ChatLog } from './components/messages/ChatLog'
import { SetupPanel } from './components/setup/SetupPanel'
import { PortfolioDashboard } from './dashboard/PortfolioDashboard'
import { PersonalStrategiesPage } from './strategies/PersonalStrategiesPage'
import type { NovaView } from './types'
import { goToRoute, isImplemented, useAppRoute } from './appRoutes'
import { NovaSidebar } from './components/shell/NovaSidebar'
import { PlaceholderPage } from './components/shell/PlaceholderPage'
import { SignalsPage } from './signals/SignalsPage'
import { WebhooksPage } from './webhooks/WebhooksPage'
import { RiskPage } from './risk/RiskPage'
import { CredentialsPage } from './credentials/CredentialsPage'
import { SetupPage, type SetupSnapshot } from './setup/SetupPage'
import { getConfigurationState, saveConfiguration } from './setup/configurationApi'
import {
  getCurrentUser,
  getMarketSnapshot,
  getRuntimeStatus,
  getSession,
  getSystemHealth,
  googleLoginUrl,
  logout,
  prepareReconfigure,
  refreshRuntimeAccount,
  resetPaperRuntime,
  saveRuntimeConfig,
  selectCatalogStrategy,
  squareOffRuntime,
  startSelectedPaperEngine,
  startSession,
  stopRuntimeEngine,
  switchRuntimeMode,
} from './api'
import type { AuthUser, LogoutEngineAction, RuntimeStatus } from './api'
import { DEFAULT_NIFTY_LOT_SIZE } from './lib/trading'
import { applyRestMarketSnapshot, applyRuntimeHydration, useSessionStore } from './state/sessionStore'
import { SessionWS } from './ws'
import type { ClientCommand, SystemHealth } from './types'

function App() {
  const wsRef = useRef<SessionWS | null>(null)
  const lastCommandRef = useRef<{ key: string; at: number } | null>(null)
  const [bootNonce, setBootNonce] = useState(0)
  // View is derived from the URL (nested /app/* routes), never from in-memory
  // state, so refresh and Back/Forward work on every page. setView is kept as the
  // same seam so existing call sites simply navigate.
  const route = useAppRoute()
  const view: NovaView = route === 'dashboard'
    ? 'dashboard'
    : route === 'strategies' || route === 'strategies/new'
      ? 'strategies'
      : 'trading'
  const setView = useCallback((next: NovaView) => {
    goToRoute(next === 'dashboard' ? 'dashboard' : next === 'strategies' ? 'strategies' : 'trading')
  }, [])
  // Held here so /app/setup can project the rail and configuration panel from
  // the same conversation state the controller owns.
  const [setupSnapshot, setSetupSnapshot] = useState<SetupSnapshot | null>(null)
  const [railOpen, setRailOpen] = useState(false)
  const [railCollapsed, setRailCollapsed] = useState(false)
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [authError, setAuthError] = useState('')
  const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null)
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null)
  const [runtimeLoading, setRuntimeLoading] = useState(true)
  const [runtimeError, setRuntimeError] = useState('')
  const [managedStrategyId, setManagedStrategyId] = useState<string | null>(null)
  const [leftDrawerOpen, setLeftDrawerOpen] = useState(false)
  const [rightDrawerOpen, setRightDrawerOpen] = useState(false)
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false)
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false)
  const [snapshotLoaded, setSnapshotLoaded] = useState(false)
  const reduceMotion = useAppReducedMotion()
  const session = useSessionStore((state) => state.session)
  const setupFlowStep = useSessionStore((state) => state.setupFlowStep)
  const setupDraft = useSessionStore((state) => state.setupDraft)
  const setupState = useSessionStore((state) => state.setupState)
  const engineMode = useSessionStore((state) => state.engineMode)
  const config = useSessionStore((state) => state.config)
  const messages = useSessionStore((state) => state.messages)
  const activeTrade = useSessionStore((state) => state.activeTrade)
  const pushedMarketSnapshot = useSessionStore((state) => state.marketSnapshot)
  const wallet = useSessionStore((state) => state.wallet)
  const marginUtilized = useSessionStore((state) => state.marginUtilized)
  const realizedPnl = useSessionStore((state) => state.realizedPnl)
  const wsStatus = useSessionStore((state) => state.wsStatus)
  const bootError = useSessionStore((state) => state.bootError)
  const lastSetupError = useSessionStore((state) => state.lastSetupError)
  const typing = useSessionStore((state) => state.typing)
  const loadBootstrap = useSessionStore((state) => state.loadBootstrap)
  const loadSnapshot = useSessionStore((state) => state.loadSnapshot)
  const applyServerEvent = useSessionStore((state) => state.applyServerEvent)
  const setWsStatus = useSessionStore((state) => state.setWsStatus)
  const setBootError = useSessionStore((state) => state.setBootError)
  const markCommandSent = useSessionStore((state) => state.markCommandSent)
  const resetSession = useSessionStore((state) => state.resetSession)
  const addUserMessage = useSessionStore((state) => state.addUserMessage)
  const updateSetupDraft = useSessionStore((state) => state.updateSetupDraft)
  const setSetupFlowStep = useSessionStore((state) => state.setSetupFlowStep)

  const verifyLogin = useCallback(() => {
    setAuthLoading(true)
    setAuthError('')
    getCurrentUser()
      .then(setAuthUser)
      .catch((error: unknown) => {
        setAuthUser(null)
        setAuthError(error instanceof Error ? error.message : 'Could not verify login')
      })
      .finally(() => setAuthLoading(false))
  }, [])

  useEffect(() => {
    let mounted = true
    getCurrentUser()
      .then((user) => {
        if (mounted) setAuthUser(user)
      })
      .catch((error: unknown) => {
        if (!mounted) return
        setAuthUser(null)
        setAuthError(error instanceof Error ? error.message : 'Could not verify login')
      })
      .finally(() => {
        if (mounted) setAuthLoading(false)
      })
    return () => {
      mounted = false
    }
  }, [])

  useEffect(() => {
    if (!authUser) return
    let mounted = true

    resetSession()
    startSession()
      .then(async (bootstrap) => {
        if (!mounted) return
        loadBootstrap(bootstrap)
        const snapshot = await getSession(bootstrap.sessionId)
        if (!mounted) return
        loadSnapshot(snapshot)
        setSnapshotLoaded(true)

        const ws = new SessionWS(bootstrap.sessionId, bootstrap.sessionToken)
        wsRef.current = ws
        ws.on('*', applyServerEvent)
        ws.onStatus(setWsStatus)
        ws.onInvalidSession(() => {
          if (!mounted) return
          ws.close()
          resetSession()
          setBootNonce((current) => current + 1)
        })
        ws.connect()
      })
      .catch((err: unknown) => {
        if (!mounted) return
        setBootError(err instanceof Error ? err.message : 'Could not initialize Nova')
      })

    return () => {
      mounted = false
      wsRef.current?.close()
    }
  }, [applyServerEvent, authUser, bootNonce, loadBootstrap, loadSnapshot, resetSession, setBootError, setWsStatus])

  useEffect(() => {
    if (!authUser) return
    let mounted = true
    let timer: number | null = null

    const poll = () => {
      const requestStartedAt = Date.now()
      Promise.allSettled([getMarketSnapshot(), getSystemHealth(), getRuntimeStatus()]).then(([market, health, runtime]) => {
        if (!mounted) return
        if (market.status === 'fulfilled') applyRestMarketSnapshot(market.value, requestStartedAt)
        if (health.status === 'fulfilled') setSystemHealth(health.value)
        if (runtime.status === 'fulfilled') {
          setRuntimeStatus(runtime.value)
          applyRuntimeHydration(runtime.value, requestStartedAt)
          setRuntimeError('')
        } else {
          setRuntimeError(runtime.reason instanceof Error ? runtime.reason.message : 'Could not load strategy state.')
        }
        setRuntimeLoading(false)
      })
      timer = window.setTimeout(poll, 15000)
    }

    poll()
    return () => {
      mounted = false
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [authUser, bootNonce])

  const displayedMarketSnapshot = pushedMarketSnapshot

  useEffect(() => {
    const handler = () => {
      void reconfigure()
    }
    window.addEventListener('nova:reconfigure-request', handler)
    return () => window.removeEventListener('nova:reconfigure-request', handler)
  })

  useEffect(() => {
    document.documentElement.dataset.mode = engineMode ?? 'unset'
  }, [engineMode])

  function send(command: ClientCommand) {
    // Double-click / double-submit guard: ignore an identical command that is
    // re-fired within a short window while the previous one is still in flight.
    // Distinct commands (e.g. the next setup step) are never blocked.
    const key = JSON.stringify(command)
    const now = Date.now()
    const last = lastCommandRef.current
    if (last && last.key === key && now - last.at < 4000) {
      return
    }
    lastCommandRef.current = { key, at: now }
    markCommandSent()
    wsRef.current?.send(command)
  }

  function sendWithUserMessage(command: ClientCommand, text: string) {
    addUserMessage(text)
    send(command)
  }

  async function retryRuntime() {
    try {
      const status = await getRuntimeStatus()
      setRuntimeStatus(status)
      setRuntimeError('')
    } catch (err) {
      setRuntimeError(err instanceof Error ? err.message : 'Could not load strategy state.')
    }
  }

  async function reconfigure() {
    setBootError('')
    try {
      await prepareReconfigure()
      const status = await getRuntimeStatus()
      setRuntimeStatus(status)
      setRuntimeError('')
      if (status.position.has_open_position) {
        setBootError('Engine stopped. Configuration changes remain blocked until the tracked position is flat.')
        return
      }
      wsRef.current?.close()
      resetSession()
      setSnapshotLoaded(false)
      setBootNonce((current) => current + 1)
    } catch (error) {
      setBootError(error instanceof Error ? error.message : 'Could not reconfigure Nova')
    }
  }

  async function handleLogout(engineAction: LogoutEngineAction) {
    setBootError('')
    try {
      await logout(engineAction)
      wsRef.current?.close()
      resetSession()
      setSnapshotLoaded(false)
      setRuntimeStatus(null)
      setRuntimeLoading(true)
      setRuntimeError('')
      setAuthUser(null)
    } catch (error) {
      setBootError(error instanceof Error ? error.message : 'Could not log out')
    }
  }

  async function updateRuntime(action: () => Promise<RuntimeStatus>) {
    setBootError('')
    try {
      await action()
      const latest = await getRuntimeStatus()
      setRuntimeStatus(latest)
      applyRuntimeHydration(latest)
      setRuntimeError('')
    } catch (error) {
      setBootError(error instanceof Error ? error.message : 'Runtime action failed')
    }
  }

  async function selectTradingStrategy(strategyKey: string) {
    await selectCatalogStrategy(strategyKey)
    setRuntimeStatus(await getRuntimeStatus())
    setRuntimeError('')
  }

  async function configureTradingStrategy(
    strategyKey: string,
    values: Record<string, string | number>,
    risk: Record<string, string | number>,
  ) {
    if (runtimeStatus?.strategy_catalog?.selected_strategy_key !== strategyKey) {
      throw new Error('The selected strategy changed. Refresh before saving settings.')
    }
    // Strategy setup and risk settings commit as one revision; a partial save
    // could leave new limits paired with old sizing.
    const state = await getConfigurationState('paper')
    await saveConfiguration({
      strategyKey,
      mode: 'paper',
      setup: values,
      risk,
      expectedRevision: state.revision,
    })
    setRuntimeStatus(await getRuntimeStatus())
    setRuntimeError('')
  }

  async function startTradingStrategy(instanceId: string) {
    if (runtimeStatus?.selected_strategy?.instance_id !== instanceId) {
      throw new Error('The selected strategy changed. Refresh before starting.')
    }
    const latest = await startSelectedPaperEngine(instanceId)
    setRuntimeStatus(latest)
    applyRuntimeHydration(latest)
    setRuntimeError('')
  }

  if (authLoading || !authUser) {
    return (
      <AuthScreen
        loading={authLoading}
        error={authError}
        onLogin={() => window.location.assign(googleLoginUrl())}
        onRetry={verifyLogin}
      />
    )
  }

  const sessionEngineLive = runtimeStatus?.engine.running ?? (setupState === 'LIVE' || setupState === 'PAUSED')
  const runtimeEntriesBlocked = sessionEngineLive && systemHealth?.engine === 'paused'
  const effectiveSetupState = runtimeEntriesBlocked || setupState === 'PAUSED' ? 'PAUSED' : setupState
  const engineLive = sessionEngineLive
  const panelLayoutTransition = reduceMotion
    ? { duration: 0 }
    : { type: 'spring' as const, stiffness: 380, damping: 42, mass: 0.8 }
  const panelContentTransition = reduceMotion
    ? { duration: 0 }
    : { duration: 0.18, ease: [0.16, 1, 0.3, 1] as [number, number, number, number] }
  const drawerTransition = reduceMotion
    ? { duration: 0 }
    : { duration: 0.32, ease: softEase }
  const leftPanelContentMotion = leftPanelCollapsed
    ? { opacity: 0, x: -14, filter: 'blur(2px)', transitionEnd: { visibility: 'hidden' as const } }
    : { opacity: 1, x: 0, filter: 'blur(0px)', visibility: 'visible' as const }
  const rightPanelContentMotion = rightPanelCollapsed
    ? { opacity: 0, x: 14, filter: 'blur(2px)', transitionEnd: { visibility: 'hidden' as const } }
    : { opacity: 1, x: 0, filter: 'blur(0px)', visibility: 'visible' as const }
  const renderLeftCollapseButton = () => (
    <button
      type="button"
      className="panel-collapse-button"
      aria-label={leftPanelCollapsed ? 'Expand market and manual order panel' : 'Collapse market and manual order panel'}
      title={leftPanelCollapsed ? 'Expand panel' : 'Collapse panel'}
      onClick={() => setLeftPanelCollapsed((collapsed) => !collapsed)}
    >
      {leftPanelCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
    </button>
  )
  const renderRightCollapseButton = () => (
    <button
      type="button"
      className="panel-collapse-button"
      aria-label={rightPanelCollapsed ? 'Expand account and controls panel' : 'Collapse account and controls panel'}
      title={rightPanelCollapsed ? 'Expand panel' : 'Collapse panel'}
      onClick={() => setRightPanelCollapsed((collapsed) => !collapsed)}
    >
      {rightPanelCollapsed ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
    </button>
  )
  const setupPanel = (
    <SetupPanel
      state={effectiveSetupState}
      flowStep={setupFlowStep}
      draft={setupDraft}
      lastError={lastSetupError}
      verifyPending={typing && setupFlowStep === 'broker'}
      commandPending={typing}
      lotSize={session?.lotSize ?? DEFAULT_NIFTY_LOT_SIZE}
      sharedMarketData={session?.sharedMarketData ?? false}
      runtime={runtimeStatus}
      runtimeLoading={runtimeLoading}
      runtimeError={runtimeError}
      onManageStrategy={(instanceId) => {
        setManagedStrategyId(instanceId)
        setView('strategies')
      }}
      onConfigureStrategy={configureTradingStrategy}
      onSelectStrategy={selectTradingStrategy}
      onStartStrategy={startTradingStrategy}
      onRetryRuntime={retryRuntime}
      onSend={send}
      onUserReply={addUserMessage}
      onDraft={updateSetupDraft}
      onStep={setSetupFlowStep}
      onSetupState={setSetupSnapshot}
    />
  )

  return (
    <main className="nova-app">
      <div className="nova-shell">
      <NovaSidebar
        route={route}
        open={railOpen}
        collapsed={railCollapsed}
        onNavigate={goToRoute}
        onToggleCollapsed={() => setRailCollapsed((c) => !c)}
        onClose={() => setRailOpen(false)}
      />
      <div className="nova-main">
      <Header
        status={wsStatus}
        clientId={config.broker?.clientId}
        runtime={runtimeStatus}
        engineLive={engineLive}
        engineMode={engineMode}
        setupState={effectiveSetupState}
        health={systemHealth}
        user={authUser}
        view={view}
        onNavigate={setView}
        onKill={() => void updateRuntime(squareOffRuntime)}
        onStop={() => void updateRuntime(stopRuntimeEngine)}
        onReconfigure={reconfigure}
        onLogout={handleLogout}
        onMode={(mode) => void updateRuntime(() => switchRuntimeMode(mode))}
        onSaveConfig={(mode, lots, sl, tp) => void updateRuntime(() => saveRuntimeConfig(mode, lots, sl, tp))}
        onPaperReset={() => void updateRuntime(() => resetPaperRuntime())}
        onAccountRefresh={async () => {
          const result = await refreshRuntimeAccount()
          const message = String(result.message || result.status || 'Account refresh completed.')
          setBootError(result.ok === false ? message : '')
          const latest = await getRuntimeStatus()
          setRuntimeStatus(latest)
        }}
      />

      {runtimeStatus?.position.has_open_position && runtimeStatus.engine.state === 'STOPPED' ? (
        <div className="error-banner mx-4 mt-3" role="status">
          POSITION OPEN — ENGINE STOPPED. New entries are blocked; the tracked position remains visible and monitored.
        </div>
      ) : null}

      {engineLive && view === 'trading' ? (
        <div className="router-banner-slot">
          <EngineListening
            paused={effectiveSetupState === 'PAUSED'}
            activeTrade={activeTrade}
            side={config.risk?.side ?? 'BOTH'}
            engineMode={engineMode}
          />
        </div>
      ) : null}

      {!isImplemented(route) ? (
        <PlaceholderPage route={route} />
      ) : route === 'signals' ? (
        <SignalsPage />
      ) : route === 'webhooks' ? (
        <WebhooksPage />
      ) : route === 'risk' ? (
        <RiskPage />
      ) : route === 'setup' ? (
        <SetupPage conversation={setupPanel} snapshot={setupSnapshot} />
      ) : route === 'credentials' ? (
        <CredentialsPage />
      ) : view === 'dashboard' ? (
        <PortfolioDashboard />
      ) : view === 'strategies' ? (
        <PersonalStrategiesPage user={authUser} focusInstanceId={managedStrategyId} />
      ) : (
        <motion.section
          layout
          transition={panelLayoutTransition}
          className={
            engineLive
              ? `engine-shell engine-shell-grid ${leftPanelCollapsed ? 'left-panel-collapsed' : ''} ${rightPanelCollapsed ? 'right-panel-collapsed' : ''} px-4 w-full max-w-[1480px] mx-auto lg:h-[calc(100vh-120px)] lg:min-h-0 min-h-screen items-stretch`
              : 'chat-shell'
          }
          aria-label="Nova trading session"
        >
        {bootError ? <div className="error-banner col-span-full">{bootError}</div> : null}
        {!session && !bootError ? <div className="system-chip col-span-full">Starting session</div> : null}

        {engineLive ? (
          <motion.aside
            layout
            transition={panelLayoutTransition}
            className={`desktop-engine-panel engine-panel-left ${leftPanelCollapsed ? 'is-collapsed' : ''}`}
          >
            <div className="engine-panel-shell">
              {leftPanelCollapsed ? (
                <motion.div
                  className="panel-collapse-rail"
                  initial={false}
                  animate={{ opacity: 1 }}
                  transition={panelContentTransition}
                >
                  {renderLeftCollapseButton()}
                  <motion.span
                    className="panel-rail-label"
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={panelContentTransition}
                  >
                    Market
                  </motion.span>
                </motion.div>
              ) : (
                <motion.div
                  className="panel-scroll"
                  animate={leftPanelContentMotion}
                  initial={reduceMotion ? false : { opacity: 0, x: -10, filter: 'blur(2px)' }}
                  transition={panelContentTransition}
                >
                  <EngineLeftPanel
                    marketSnapshot={displayedMarketSnapshot}
                    engineMode={engineMode}
                    activeTrade={activeTrade}
                    runtimePositionOpen={Boolean(runtimeStatus?.position.has_open_position)}
                    collapseControl={renderLeftCollapseButton()}
                  />
                </motion.div>
              )}
            </div>
          </motion.aside>
        ) : null}

        <motion.div
          layout
          transition={panelLayoutTransition}
          className={`engine-main-pane lg:h-full flex flex-col min-h-[450px] lg:min-h-0${engineLive ? ' engine-live-layout' : ''}`}
        >
          {!snapshotLoaded && !bootError ? (
            <div className="flex flex-col items-center justify-center flex-grow min-h-[350px] gap-3 text-white/40">
              <MotionSpinner className="text-[#2F6BED]">
                <Loader2 className="w-8 h-8" />
              </MotionSpinner>
              <MotionPulseText className="text-xs font-semibold tracking-widest uppercase">Initializing Session...</MotionPulseText>
            </div>
          ) : (
            <>
              <ChatLog
                messages={messages}
                typing={typing}
                panelKey={`${effectiveSetupState}-${setupFlowStep}-${engineLive ? 'engine' : 'setup'}`}
                inlinePanel={setupFlowStep === 'mode' ? setupPanel : null}
              >
                {setupFlowStep === 'mode' ? null : setupPanel}
              </ChatLog>
              {engineLive ? (
                <div className="live-engine-stack">
                  <NiftyLiveChart engineMode={engineMode} />
                </div>
              ) : null}

              {/* Mobile-only inline active position & routing controls below main chat */}
              {engineLive ? (
                <div className="block lg:hidden mt-6">
                  <EngineSidebar
                    state={effectiveSetupState}
                    wallet={wallet}
                    marginUtilized={marginUtilized}
                    realizedPnl={realizedPnl}
                    activeTrade={activeTrade}
                    lotSize={session?.lotSize ?? DEFAULT_NIFTY_LOT_SIZE}
                    side={config.risk?.side ?? 'BOTH'}
                    engineMode={engineMode}
                    onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
                    hideMargin={true}
                  />
                </div>
              ) : null}
            </>
          )}
        </motion.div>

        {engineLive ? (
          <motion.aside
            layout
            transition={panelLayoutTransition}
            className={`desktop-engine-panel engine-panel-right ${rightPanelCollapsed ? 'is-collapsed' : ''}`}
          >
            <div className="engine-panel-shell">
              {rightPanelCollapsed ? (
                <motion.div
                  className="panel-collapse-rail"
                  initial={false}
                  animate={{ opacity: 1 }}
                  transition={panelContentTransition}
                >
                  {renderRightCollapseButton()}
                  <motion.span
                    className="panel-rail-label"
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={panelContentTransition}
                  >
                    Account
                  </motion.span>
                </motion.div>
              ) : (
                <motion.div
                  className="panel-scroll"
                  animate={rightPanelContentMotion}
                  initial={reduceMotion ? false : { opacity: 0, x: 10, filter: 'blur(2px)' }}
                  transition={panelContentTransition}
                >
                  <EngineSidebar
                    state={effectiveSetupState}
                    wallet={wallet}
                    marginUtilized={marginUtilized}
                    realizedPnl={realizedPnl}
                    activeTrade={activeTrade}
                    lotSize={session?.lotSize ?? DEFAULT_NIFTY_LOT_SIZE}
                    side={config.risk?.side ?? 'BOTH'}
                    engineMode={engineMode}
                    onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
                    collapseControl={renderRightCollapseButton()}
                  />
                </motion.div>
              )}
            </div>
          </motion.aside>
        ) : null}
      </motion.section>
      )}

      {/* Mobile-only sliding drawers and floating action bar */}
      {engineLive && (
        <>
          {/* Bottom-to-Top Drawer: Market LTP & Manual Order */}
          <AnimatePresence initial={false}>
            {leftDrawerOpen ? (
              <motion.div
                className="fixed inset-0 z-[110] block lg:hidden"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={drawerTransition}
              >
                <motion.div
                  className="fixed inset-0 bg-black/60 backdrop-blur-sm"
                  onClick={() => setLeftDrawerOpen(false)}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={drawerTransition}
                />
                <motion.div
                  className="fixed inset-x-0 bottom-0 max-h-[85vh] h-auto bg-[#0f0e1c] border-t border-white/10 rounded-t-2xl p-5 pb-8 overflow-y-auto shadow-2xl flex flex-col gap-4 z-[120]"
                  initial={{ y: '100%' }}
                  animate={{ y: 0 }}
                  exit={{ y: '100%' }}
                  transition={drawerTransition}
                >
              <div className="w-12 h-1.5 bg-white/15 rounded-full mx-auto mb-1 flex-shrink-0" />
              <div className="flex justify-between items-center pb-3 border-b border-white/5">
                <span className="font-bold text-sm text-white tracking-wide">Market & Order</span>
                <button
                  type="button"
                  className="text-white/40 hover:text-white hover:bg-white/5 w-7 h-7 flex items-center justify-center rounded-md border-0 cursor-pointer text-sm font-semibold transition-colors"
                  onClick={() => setLeftDrawerOpen(false)}
                >
                  ✕
                </button>
              </div>
              <EngineLeftPanel marketSnapshot={displayedMarketSnapshot} engineMode={engineMode} activeTrade={activeTrade} runtimePositionOpen={Boolean(runtimeStatus?.position.has_open_position)} />
                </motion.div>
              </motion.div>
            ) : null}
          </AnimatePresence>

          {/* Bottom-to-Top Drawer: Margin & Balance */}
          <AnimatePresence initial={false}>
            {rightDrawerOpen ? (
              <motion.div
                className="fixed inset-0 z-[110] block lg:hidden"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={drawerTransition}
              >
                <motion.div
                  className="fixed inset-0 bg-black/60 backdrop-blur-sm"
                  onClick={() => setRightDrawerOpen(false)}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={drawerTransition}
                />
                <motion.div
                  className="fixed inset-x-0 bottom-0 max-h-[85vh] h-auto bg-[#0f0e1c] border-t border-white/10 rounded-t-2xl p-5 pb-8 overflow-y-auto shadow-2xl flex flex-col gap-4 z-[120]"
                  initial={{ y: '100%' }}
                  animate={{ y: 0 }}
                  exit={{ y: '100%' }}
                  transition={drawerTransition}
                >
              <div className="w-12 h-1.5 bg-white/15 rounded-full mx-auto mb-1 flex-shrink-0" />
              <div className="flex justify-between items-center pb-3 border-b border-white/5">
                <span className="font-bold text-sm text-white tracking-wide">Account & Balance</span>
                <button
                  type="button"
                  className="text-white/40 hover:text-white hover:bg-white/5 w-7 h-7 flex items-center justify-center rounded-md border-0 cursor-pointer text-sm font-semibold transition-colors"
                  onClick={() => setRightDrawerOpen(false)}
                >
                  ✕
                </button>
              </div>
              <EngineSidebar
                state={effectiveSetupState}
                wallet={wallet}
                marginUtilized={marginUtilized}
                realizedPnl={realizedPnl}
                activeTrade={activeTrade}
                lotSize={session?.lotSize ?? DEFAULT_NIFTY_LOT_SIZE}
                side={config.risk?.side ?? 'BOTH'}
                engineMode={engineMode}
                onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
                onlyMargin={true}
              />
                </motion.div>
              </motion.div>
            ) : null}
          </AnimatePresence>

          {/* Sticky Bottom Navigation Bar on Mobile */}
          <div className="fixed bottom-0 left-0 right-0 h-16 bg-[#0c0a14]/95 backdrop-blur-md border-t border-white/10 flex items-center justify-around z-[90] lg:hidden px-2">
            <button
              type="button"
              className={`flex flex-col items-center justify-center gap-1 flex-1 py-1 mx-1 h-[80%] rounded-xl border-0 transition-all cursor-pointer ${view === 'trading' && !leftDrawerOpen && !rightDrawerOpen ? 'bg-[rgba(47,107,237,0.14)] text-[#2F6BED] font-semibold' : 'bg-transparent text-white/40 hover:text-white/70'}`}
              onClick={() => {
                setView('trading')
                setLeftDrawerOpen(false)
                setRightDrawerOpen(false)
              }}
            >
              <LineChart size={18} />
              <span className="text-[10px]">Trading</span>
            </button>

            <button
              type="button"
              className={`flex flex-col items-center justify-center gap-1 flex-1 py-1 mx-1 h-[80%] rounded-xl border-0 transition-all cursor-pointer ${view === 'dashboard' ? 'bg-[rgba(47,107,237,0.14)] text-[#2F6BED] font-semibold' : 'bg-transparent text-white/40 hover:text-white/70'}`}
              onClick={() => {
                setView('dashboard')
                setLeftDrawerOpen(false)
                setRightDrawerOpen(false)
              }}
            >
              <BarChart3 size={18} />
              <span className="text-[10px]">Dashboard</span>
            </button>

            <button
              type="button"
              className={`flex flex-col items-center justify-center gap-1 flex-1 py-1 mx-1 h-[80%] rounded-xl border-0 transition-all cursor-pointer ${view === 'strategies' ? 'bg-[rgba(47,107,237,0.14)] text-[#2F6BED] font-semibold' : 'bg-transparent text-white/40 hover:text-white/70'}`}
              onClick={() => {
                setView('strategies')
                setLeftDrawerOpen(false)
                setRightDrawerOpen(false)
              }}
            >
              <Webhook size={18} />
              <span className="text-[10px]">Strategies</span>
            </button>

            <button
              type="button"
              className={`flex flex-col items-center justify-center gap-1 flex-1 py-1 mx-1 h-[80%] rounded-xl border-0 transition-all cursor-pointer ${leftDrawerOpen ? 'bg-[rgba(47,107,237,0.14)] text-[#2F6BED] font-semibold' : 'bg-transparent text-white/40 hover:text-white/70'}`}
              onClick={() => {
                setView('trading')
                setLeftDrawerOpen(true)
                setRightDrawerOpen(false)
              }}
            >
              <Sliders size={18} />
              <span className="text-[10px]">Market & Trade</span>
            </button>

            <button
              type="button"
              className={`flex flex-col items-center justify-center gap-1 flex-1 py-1 mx-1 h-[80%] rounded-xl border-0 transition-all cursor-pointer ${rightDrawerOpen ? 'bg-[rgba(47,107,237,0.14)] text-[#2F6BED] font-semibold' : 'bg-transparent text-white/40 hover:text-white/70'}`}
              onClick={() => {
                setView('trading')
                setLeftDrawerOpen(false)
                setRightDrawerOpen(true)
              }}
            >
              <Wallet size={18} />
              <span className="text-[10px]">Account</span>
            </button>
          </div>
        </>
      )}

      <footer className="app-footer pb-20 lg:pb-4">(c) 2026 Layman Signal Route. Deployed Live with Dhan. All rights reserved.</footer>
      </div>
      </div>
    </main>
  )
}

export default App

function commandMessage(command: ClientCommand): string {
  if (command.type === 'session.pause') return 'block entry requests'
  if (command.type === 'session.resume') return 'allow entry requests'
  if (command.type === 'session.exit_open') return 'exit open position'
  if (command.type === 'session.apply_sr_suggestion') return 'use suggested S/R SL/TP'
  if (command.type === 'session.patch_risk') {
    const side = (command.data as { side?: string }).side
    return `automated entry side: ${side ?? 'unchanged'}`
  }
  return command.type
}
