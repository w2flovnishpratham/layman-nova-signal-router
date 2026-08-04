import { Button } from '@/components/ui/button'
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import { toast } from '@/components/ui/toast'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { AuthScreen } from './components/AuthScreen'
import { EngineLeftPanel } from './components/EngineLeftPanel'
import { NiftyLiveChart } from './components/NiftyLiveChart'
import { EngineSidebar } from './components/EngineSidebar'
import { Header } from './components/Header'
import { softEase, useAppReducedMotion } from './components/MotionPrimitives'
import { PageSkeleton } from './components/PageSkeleton'
import { TerminalMobileBar, type TerminalMobileSection } from './components/TerminalMobileBar'
import { SetupPanel } from './components/setup/SetupPanel'
import type { NovaView } from './types'
import { goToRoute, isImplemented, useAppRoute } from './appRoutes'
import { PageErrorBoundary } from './components/shell/PageErrorBoundary'
import { PlaceholderPage } from './components/shell/PlaceholderPage'
import { TradingActivityTabs } from './trading/TradingActivityTabs'

// Only the terminal (chart + activity, below) is on the critical path for a
// logged-in trader. Every other tab is visited far less often per session,
// so it ships as its own chunk instead of bloating the initial bundle.
const SignalsPage = lazy(() => import('./signals/SignalsPage').then((m) => ({ default: m.SignalsPage })))
const WebhooksPage = lazy(() => import('./webhooks/WebhooksPage').then((m) => ({ default: m.WebhooksPage })))
const RiskPage = lazy(() => import('./risk/RiskPage').then((m) => ({ default: m.RiskPage })))
const CredentialsPage = lazy(() => import('./credentials/CredentialsPage').then((m) => ({ default: m.CredentialsPage })))
const ReportsPage = lazy(() => import('./reports/ReportsPage').then((m) => ({ default: m.ReportsPage })))
const AddStrategyPage = lazy(() => import('./strategies/AddStrategyPage').then((m) => ({ default: m.AddStrategyPage })))
const SettingsPage = lazy(() => import('./settings/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const AutomationsPage = lazy(() => import('./automations/AutomationsPage').then((m) => ({ default: m.AutomationsPage })))
const PortfolioDashboard = lazy(() => import('./dashboard/PortfolioDashboard').then((m) => ({ default: m.PortfolioDashboard })))
const PersonalStrategiesPage = lazy(() => import('./strategies/PersonalStrategiesPage').then((m) => ({ default: m.PersonalStrategiesPage })))
import type { EngineConfigValues } from './trading/EngineConfigCard'
import { SetupPage, type SetupSnapshot } from './setup/SetupPage'
import { getConfigurationState, saveConfiguration } from './setup/configurationApi'
import { attemptAfterFailure, attemptKeyFor } from './lib/engineStartIdempotency'
import {
  getCurrentUser,
  getMarketSnapshot,
  getSession,
  getSystemHealth,
  getTradingBootstrap,
  googleLoginUrl,
  logout,
  prepareReconfigure,
  resetPaperRuntime,
  saveRuntimeConfig,
  selectCatalogStrategy,
  squareOffRuntime,
  startSelectedEngine,
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
  // Stable across retries of the SAME start attempt (e.g. a client-side
  // timeout after the server already committed) so a retry reuses NOVA's
  // durable engine-start idempotency claim instead of minting a fresh key
  // and risking a second engine. Cleared on success/failure so the NEXT
  // distinct attempt (a different revision) gets its own key.
  const engineStartAttemptRef = useRef<{ revisionId: string; key: string } | null>(null)
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
  // Whether the routed-page ternary below would fall through to the terminal
  // (chart + activity) rather than one of the named single-purpose pages --
  // mirrors that ternary's conditions exactly. Used to keep the terminal
  // mounted persistently (see below) instead of destroying/recreating the
  // live chart and its fetched data every time the user visits another tab
  // and comes back.
  const SECONDARY_ROUTES = new Set(['signals', 'webhooks', 'risk', 'credentials', 'reports', 'strategies/new', 'settings', 'automations'])
  const showTerminal = isImplemented(route) && !SECONDARY_ROUTES.has(route) && view === 'trading'
  // Held here so the Trading route can project the setup rail and configuration
  // panel from the same conversation state the controller owns.
  const [setupSnapshot, setSetupSnapshot] = useState<SetupSnapshot | null>(null)
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [authError, setAuthError] = useState('')
  const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null)
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null)
  const [runtimeLoading, setRuntimeLoading] = useState(true)
  const [runtimeError, setRuntimeError] = useState('')
  const [managedStrategyId, setManagedStrategyId] = useState<string | null>(null)
  const [leftDrawerOpen, setLeftDrawerOpen] = useState(false)
  const [riskDrawerOpen, setRiskDrawerOpen] = useState(false)
  const [rightDrawerOpen, setRightDrawerOpen] = useState(false)
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false)
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false)
  const [snapshotLoaded, setSnapshotLoaded] = useState(false)
  const [runtimeActionPending, setRuntimeActionPending] = useState(false)
  const reduceMotion = useAppReducedMotion()
  const session = useSessionStore((state) => state.session)
  const setupFlowStep = useSessionStore((state) => state.setupFlowStep)
  const setupDraft = useSessionStore((state) => state.setupDraft)
  const setupState = useSessionStore((state) => state.setupState)
  const engineMode = useSessionStore((state) => state.engineMode)
  const config = useSessionStore((state) => state.config)
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
      Promise.allSettled([getMarketSnapshot(), getSystemHealth(), getTradingBootstrap()]).then(([market, health, runtime]) => {
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
      const status = await getTradingBootstrap()
      setRuntimeStatus(status)
      applyRuntimeHydration(status)
      setRuntimeError('')
    } catch (err) {
      setRuntimeError(err instanceof Error ? err.message : 'Could not load strategy state.')
    }
  }

  async function reconfigure() {
    setBootError('')
    const toastId = toast.add({
      title: 'Stopping the engine and preparing reconfiguration…',
      type: 'loading',
      timeout: 0,
    })
    try {
      await prepareReconfigure()
      const status = await getTradingBootstrap()
      setRuntimeStatus(status)
      applyRuntimeHydration(status)
      setRuntimeError('')
      if (status.position.has_open_position) {
        setBootError('Engine stopped. Configuration changes remain blocked until the tracked position is flat.')
        toast.update(toastId, {
          title: 'Engine stopped, but reconfiguration is blocked until the position is flat.',
          type: 'warning',
          timeout: 5000,
        })
        return
      }
      wsRef.current?.close()
      resetSession()
      setSnapshotLoaded(false)
      setBootNonce((current) => current + 1)
      toast.update(toastId, { title: 'Ready to reconfigure.', type: 'success', timeout: 5000 })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not reconfigure Nova'
      setBootError(message)
      toast.update(toastId, { title: message, type: 'error', timeout: 5000 })
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
    // Guards the repeated-click case directly: a second click while the first
    // request is still in flight no-ops instead of firing a duplicate action,
    // on top of the buttons showing a loading state so it's visibly pending.
    if (runtimeActionPending) return
    setRuntimeActionPending(true)
    setBootError('')
    try {
      await action()
      const latest = await getTradingBootstrap()
      setRuntimeStatus(latest)
      applyRuntimeHydration(latest)
      setRuntimeError('')
    } catch (error) {
      setBootError(error instanceof Error ? error.message : 'Runtime action failed')
    } finally {
      setRuntimeActionPending(false)
    }
  }

  async function selectTradingStrategy(strategyKey: string) {
    await selectCatalogStrategy(strategyKey)
    const latest = await getTradingBootstrap()
    setRuntimeStatus(latest)
    applyRuntimeHydration(latest)
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
    const mode = runtimeStatus?.engine.mode
    if (!mode) {
      throw new Error('Choose Paper or Live before saving a configuration.')
    }
    // Strategy setup and risk settings commit as one revision; a partial save
    // could leave new limits paired with old sizing.
    const state = await getConfigurationState(mode)
    await saveConfiguration({
      strategyKey,
      mode,
      setup: values,
      risk,
      expectedRevision: state.revision,
    })
    const latest = await getTradingBootstrap()
    setRuntimeStatus(latest)
    applyRuntimeHydration(latest)
    setRuntimeError('')
  }

  async function saveTerminalConfig(values: EngineConfigValues) {
    const selected = runtimeStatus?.selected_configuration
    const strategyKey = runtimeStatus?.strategy_catalog?.selected_strategy_key
    if (!selected || !strategyKey) throw new Error('No saved strategy configuration is selected.')
    await configureTradingStrategy(
      strategyKey,
      {
        ...selected.configuration,
        lots: values.lots,
        stop_loss_percent: values.stopLossPercent,
        take_profit_percent: values.takeProfitPercent,
      } as Record<string, string | number>,
      {
        ...selected.risk,
        max_trades_per_day: values.maxTradesPerDay,
        option_sl_percent: values.stopLossPercent,
        option_tp_percent: values.takeProfitPercent,
      } as Record<string, string | number>,
    )
  }

  async function startTradingStrategy(instanceId: string, liveAcknowledged: boolean) {
    if (runtimeStatus?.selected_strategy?.instance_id !== instanceId) {
      throw new Error('The selected strategy changed. Refresh before starting.')
    }
    const selectedConfiguration = runtimeStatus?.selected_configuration
    if (!selectedConfiguration || selectedConfiguration.strategy_instance_id !== instanceId) {
      throw new Error('Save and select an exact configuration revision before starting.')
    }
    const attempt = attemptKeyFor(engineStartAttemptRef.current, selectedConfiguration.id)
    engineStartAttemptRef.current = attempt
    try {
      const latest = await startSelectedEngine(
        instanceId,
        selectedConfiguration.strategy_version_id,
        selectedConfiguration.id,
        selectedConfiguration.revision,
        selectedConfiguration.mode,
        liveAcknowledged,
        attempt.key as ReturnType<typeof crypto.randomUUID>,
      )
      engineStartAttemptRef.current = null
      setRuntimeStatus(latest)
      applyRuntimeHydration(latest)
      setRuntimeError('')
    } catch (error) {
      engineStartAttemptRef.current = attemptAfterFailure(attempt, error)
      throw error
    }
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

  const sessionEngineLive = runtimeStatus?.engine.running ?? false
  const runtimeEntriesBlocked = sessionEngineLive && systemHealth?.engine === 'paused'
  const effectiveSetupState = runtimeEntriesBlocked || setupState === 'PAUSED' ? 'PAUSED' : setupState
  const engineLive = sessionEngineLive
  // Latches true the first time the engine goes live and never resets --
  // gates when the terminal (chart + activity) first mounts, so it doesn't
  // fetch/render before setup is even done. Once mounted it stays mounted
  // (see showTerminal below), so switching to another tab and back doesn't
  // destroy and recreate the live chart.
  const [hasGoneLive, setHasGoneLive] = useState(false)
  useEffect(() => {
    if (engineLive) setHasGoneLive(true)
  }, [engineLive])
  const panelLayoutTransition = reduceMotion
    ? { duration: 0 }
    : { duration: 0.4, ease: softEase }
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
    <CollapsibleTrigger asChild>
      <Button variant="unstyled"
        type="button"
        className="panel-collapse-button"
        aria-label={leftPanelCollapsed ? 'Expand market and manual order panel' : 'Collapse market and manual order panel'}
        title={leftPanelCollapsed ? 'Expand panel' : 'Collapse panel'}
      >
        {leftPanelCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
      </Button>
    </CollapsibleTrigger>
  )
  const renderRightCollapseButton = () => (
    <CollapsibleTrigger asChild>
      <Button variant="unstyled"
        type="button"
        className="panel-collapse-button"
        aria-label={rightPanelCollapsed ? 'Expand account and controls panel' : 'Collapse account and controls panel'}
        title={rightPanelCollapsed ? 'Expand panel' : 'Collapse panel'}
      >
        {rightPanelCollapsed ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
      </Button>
    </CollapsibleTrigger>
  )
  const setupPanel = (
    <SetupPanel
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
      <div className="nova-main">
      <Header
        route={route}
        status={wsStatus}
        clientId={config.broker?.clientId}
        runtime={runtimeStatus}
        engineLive={engineLive}
        engineMode={engineMode}
        setupState={effectiveSetupState}
        user={authUser}
        market={displayedMarketSnapshot}
        onNavigate={goToRoute}
        onKill={() => void updateRuntime(squareOffRuntime)}
        onLogout={handleLogout}
        onMode={(mode) => void updateRuntime(() => switchRuntimeMode(mode))}
        onSaveConfig={(mode, lots, sl, tp) => void updateRuntime(() => saveRuntimeConfig(mode, lots, sl, tp))}
        onPaperReset={() => void updateRuntime(() => resetPaperRuntime())}
        actionPending={runtimeActionPending}
      />

      {runtimeStatus?.position.has_open_position && runtimeStatus.engine.state === 'STOPPED' ? (
        <div className="error-banner mx-4 mt-3" role="status">
          POSITION OPEN — ENGINE STOPPED. New entries are blocked; the tracked position remains visible and monitored.
        </div>
      ) : null}

      {/* A page-level boundary: a crash in the routed page shows a retry card
          instead of blanking the header and the whole shell. */}
      <div className="nova-page">
      <PageErrorBoundary resetKey={route}>
      <Suspense fallback={<PageSkeleton label="Loading page" />}>
      {!isImplemented(route) ? (
        <PlaceholderPage route={route} />
      ) : route === 'signals' ? (
        <SignalsPage />
      ) : route === 'webhooks' ? (
        <WebhooksPage user={authUser} />
      ) : route === 'risk' ? (
        <RiskPage runtime={runtimeStatus} />
      ) : route === 'credentials' ? (
        <CredentialsPage />
      ) : route === 'reports' ? (
        <ReportsPage initialMode={engineMode} />
      ) : route === 'strategies/new' ? (
        <AddStrategyPage />
      ) : route === 'settings' ? (
        <SettingsPage user={authUser} onNavigate={goToRoute} onLogout={() => void handleLogout('keep_running')} />
      ) : route === 'automations' ? (
        <AutomationsPage />
      ) : view === 'dashboard' ? (
        <PortfolioDashboard
          runtime={runtimeStatus}
          health={systemHealth}
          onKill={() => void updateRuntime(squareOffRuntime)}
          onManageStrategies={() => setView('strategies')}
          onViewHealth={() => goToRoute('webhooks')}
          actionPending={runtimeActionPending}
        />
      ) : view === 'strategies' ? (
        <PersonalStrategiesPage user={authUser} focusInstanceId={managedStrategyId} />
      ) : !engineLive ? (
        <SetupPage conversation={setupPanel} snapshot={setupSnapshot} unavailable={Boolean(runtimeError)} />
      ) : null}
      {/* Rendered as its own always-present sibling (not a ternary branch)
          once the engine has ever gone live, so switching to another tab and
          back never destroys and recreates the live chart or its fetched
          data -- only its visibility toggles, via the style below. */}
      {hasGoneLive ? (
        <motion.section
          style={{ display: showTerminal && engineLive ? undefined : 'none' }}
          layout
          transition={panelLayoutTransition}
          className={
            engineLive
              ? `engine-shell engine-shell-grid ${leftPanelCollapsed ? 'left-panel-collapsed' : ''} ${rightPanelCollapsed ? 'right-panel-collapsed' : ''}`
              : 'chat-shell'
          }
          aria-label="Nova trading session"
        >
        {bootError ? <div className="error-banner col-span-full">{bootError}</div> : null}
        {!session && !bootError ? <div className="system-chip col-span-full">Starting session</div> : null}

        {engineLive ? (
          <Collapsible
            asChild
            open={!leftPanelCollapsed}
            onOpenChange={(open) => setLeftPanelCollapsed(!open)}
          >
          <motion.aside
            layout
            transition={panelLayoutTransition}
            className={`desktop-engine-panel engine-panel-left ${leftPanelCollapsed ? 'is-collapsed' : ''}`}
          >
            {renderLeftCollapseButton()}
            <div className="engine-panel-shell">
              {leftPanelCollapsed ? (
                <motion.div
                  className="panel-collapse-rail"
                  initial={false}
                  animate={{ opacity: 1 }}
                  transition={panelContentTransition}
                >
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
                <CollapsibleContent asChild forceMount>
                <motion.div
                  className="panel-scroll"
                  animate={leftPanelContentMotion}
                  initial={reduceMotion ? false : { opacity: 0, x: -10, filter: 'blur(2px)' }}
                  transition={panelContentTransition}
                >
                  <EngineLeftPanel
                    engineMode={engineMode}
                    runtime={runtimeStatus}
                    activeTrade={activeTrade}
                    runtimePositionOpen={Boolean(runtimeStatus?.position.has_open_position)}
                    onStop={() => void updateRuntime(stopRuntimeEngine)}
                    onSaveConfig={saveTerminalConfig}
                    side={config.risk?.side ?? 'BOTH'}
                    onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
                    actionPending={runtimeActionPending}
                  />
                </motion.div>
                </CollapsibleContent>
              )}
            </div>
          </motion.aside>
          </Collapsible>
        ) : null}

        <motion.div
          layout
          transition={panelLayoutTransition}
          className={`engine-main-pane lg:h-full flex flex-col min-h-[450px] lg:min-h-0${engineLive ? ' engine-live-layout' : ''}`}
        >
          {!snapshotLoaded && !bootError ? (
            <PageSkeleton label="Initializing trading session" variant="terminal" />
          ) : (
            <>
              <div className="live-engine-stack">
                <NiftyLiveChart
                  engineMode={engineMode}
                  defaultTimeframe={runtimeStatus?.chart_preferences?.default_timeframe}
                />
                <TradingActivityTabs mode={engineMode} />
              </div>

            </>
          )}
        </motion.div>

        {engineLive ? (
          <Collapsible
            asChild
            open={!rightPanelCollapsed}
            onOpenChange={(open) => setRightPanelCollapsed(!open)}
          >
          <motion.aside
            layout
            transition={panelLayoutTransition}
            className={`desktop-engine-panel engine-panel-right ${rightPanelCollapsed ? 'is-collapsed' : ''}`}
          >
            {renderRightCollapseButton()}
            <div className="engine-panel-shell">
              {rightPanelCollapsed ? (
                <motion.div
                  className="panel-collapse-rail"
                  initial={false}
                  animate={{ opacity: 1 }}
                  transition={panelContentTransition}
                >
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
                <CollapsibleContent asChild forceMount>
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
                    runtime={runtimeStatus}
                    onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
                    marketSnapshot={displayedMarketSnapshot}
                  />
                </motion.div>
                </CollapsibleContent>
              )}
            </div>
          </motion.aside>
          </Collapsible>
        ) : null}
      </motion.section>
      ) : null}
      </Suspense>
      </PageErrorBoundary>
      </div>

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
                <Button variant="unstyled"
                  type="button"
                  aria-label="Close market and order drawer"
                  className="terminal-drawer-close text-white/40 hover:text-white hover:bg-white/5 w-7 h-7 flex items-center justify-center rounded-md border-0 cursor-pointer font-semibold transition-colors"
                  onClick={() => setLeftDrawerOpen(false)}
                >
                  <X size={16} />
                  ✕
                </Button>
              </div>
              <EngineLeftPanel
                engineMode={engineMode}
                activeTrade={activeTrade}
                runtimePositionOpen={Boolean(runtimeStatus?.position.has_open_position)}
                runtime={runtimeStatus}
                onStop={() => void updateRuntime(stopRuntimeEngine)}
                onSaveConfig={saveTerminalConfig}
                side={config.risk?.side ?? 'BOTH'}
                onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
                actionPending={runtimeActionPending}
              />
                </motion.div>
              </motion.div>
            ) : null}
          </AnimatePresence>

          {/* Bottom-to-Top Drawer: Market Bias & Risk */}
          <AnimatePresence initial={false}>
            {riskDrawerOpen ? (
              <motion.div
                className="fixed inset-0 z-[110] block lg:hidden"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={drawerTransition}
              >
                <motion.div
                  className="fixed inset-0 bg-black/60 backdrop-blur-sm"
                  onClick={() => setRiskDrawerOpen(false)}
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
                    <span className="font-bold text-sm text-white tracking-wide">Bias &amp; Risk</span>
                    <Button variant="unstyled"
                      type="button"
                      aria-label="Close bias and risk drawer"
                      className="terminal-drawer-close text-white/40 hover:text-white hover:bg-white/5 w-7 h-7 flex items-center justify-center rounded-md border-0 cursor-pointer font-semibold transition-colors"
                      onClick={() => setRiskDrawerOpen(false)}
                    >
                      <X size={16} />
                      âœ•
                    </Button>
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
                    runtime={runtimeStatus}
                    onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
                    section="risk"
                    marketSnapshot={displayedMarketSnapshot}
                  />
                </motion.div>
              </motion.div>
            ) : null}
          </AnimatePresence>

          {/* Bottom-to-Top Drawer: Account & P&L */}
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
                <span className="font-bold text-sm text-white tracking-wide">Account &amp; P&amp;L</span>
                <Button variant="unstyled"
                  type="button"
                  aria-label="Close account and P&L drawer"
                  className="terminal-drawer-close text-white/40 hover:text-white hover:bg-white/5 w-7 h-7 flex items-center justify-center rounded-md border-0 cursor-pointer font-semibold transition-colors"
                  onClick={() => setRightDrawerOpen(false)}
                >
                  <X size={16} />
                  ✕
                </Button>
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
                runtime={runtimeStatus}
                onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
                section="account"
                marketSnapshot={displayedMarketSnapshot}
              />
                </motion.div>
              </motion.div>
            ) : null}
          </AnimatePresence>

          <TerminalMobileBar
            active={leftDrawerOpen ? 'market' : riskDrawerOpen ? 'risk' : rightDrawerOpen ? 'account' : null}
            onSelect={(section: TerminalMobileSection) => {
              setLeftDrawerOpen(section === 'market')
              setRiskDrawerOpen(section === 'risk')
              setRightDrawerOpen(section === 'account')
            }}
          />
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
