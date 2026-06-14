import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { AuthLanding } from './components/AuthLanding'
import { EngineListening } from './components/EngineListening'
import { EngineSidebar } from './components/EngineSidebar'
import { Header } from './components/Header'
import { SafetyBanner } from './components/SafetyBanner'
import { SafetyReadiness } from './components/SafetyReadiness'
import { StrategyPlatform } from './components/StrategyPlatform'
import { ChatLog } from './components/messages/ChatLog'
import { SetupPanel } from './components/setup/SetupPanel'
import { getAuthStatus, getSafetyStatus, getSession, logout, prepareReconfigure, startSession } from './api'
import { DEFAULT_NIFTY_LOT_SIZE } from './lib/trading'
import { useSessionStore } from './state/sessionStore'
import { SessionWS } from './ws'
import type { AuthStatus, ClientCommand, SafetyStatus, ServerEvent } from './types'

function App() {
  const wsRef = useRef<SessionWS | null>(null)
  const [bootNonce, setBootNonce] = useState(0)
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null)
  const [authError] = useState(readInitialAuthError)
  const [safetyStatus, setSafetyStatus] = useState<SafetyStatus | null>(null)
  const [safetyError, setSafetyError] = useState('')
  const [pendingActions, setPendingActions] = useState<Set<string>>(new Set())
  const [actionError, setActionError] = useState('')
  const [reconfigurePending, setReconfigurePending] = useState(false)
  const [logoutPending, setLogoutPending] = useState(false)
  const [strategyRefreshNonce, setStrategyRefreshNonce] = useState(0)
  const pendingActionKeysRef = useRef(new Set<string>())
  const pendingTimeoutsRef = useRef(new Map<string, number>())
  const session = useSessionStore((state) => state.session)
  const setupFlowStep = useSessionStore((state) => state.setupFlowStep)
  const setupDraft = useSessionStore((state) => state.setupDraft)
  const setupState = useSessionStore((state) => state.setupState)
  const engineMode = useSessionStore((state) => state.engineMode)
  const config = useSessionStore((state) => state.config)
  const messages = useSessionStore((state) => state.messages)
  const activeTrade = useSessionStore((state) => state.activeTrade)
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

  const refreshSafety = useCallback(async () => {
    try {
      const status = await getSafetyStatus()
      setSafetyStatus(status)
      setSafetyError('')
    } catch (error) {
      setSafetyStatus(null)
      setSafetyError(error instanceof Error ? error.message : 'Safety status is unavailable.')
    }
  }, [])

  const clearPendingActions = useCallback((keys?: string[]) => {
    const targets = keys ?? Array.from(pendingActionKeysRef.current)
    targets.forEach((key) => {
      pendingActionKeysRef.current.delete(key)
      const timeout = pendingTimeoutsRef.current.get(key)
      if (timeout !== undefined) window.clearTimeout(timeout)
      pendingTimeoutsRef.current.delete(key)
    })
    setPendingActions(new Set(pendingActionKeysRef.current))
  }, [])

  const handleServerEvent = useCallback((event: ServerEvent) => {
    applyServerEvent(event)
    const settled = settledActionsForEvent(event)
    if (settled.length) clearPendingActions(settled)
    if (event.type === 'session.error' || event.type === 'order.rejected') {
      const message = String(event.data.message ?? 'The backend rejected this action.')
      setActionError(message)
      toast.error(message)
      clearPendingActions()
    } else if (settled.length) {
      setActionError('')
      settled.forEach((action) => {
        const message = actionSuccessMessage(action)
        if (message) toast.success(message)
      })
    }
    if (event.type === 'setup.state' || event.type === 'mode.update' || event.type === 'system.event') {
      void refreshSafety()
    }
    if (
      event.type === 'strategy.job'
      || event.type === 'strategy.signal.received'
      || event.type.startsWith('strategy.job.')
      || event.type.startsWith('paper.position.')
    ) {
      setStrategyRefreshNonce((current) => current + 1)
      const status = String(event.data.status ?? '')
      const message = String(event.data.reason ?? 'Strategy signal status updated.')
      if (status === 'paper_filled' || event.type === 'paper.position.opened' || event.type === 'paper.position.closed') {
        toast.success(message)
      }
      else toast.info(message)
    }
  }, [applyServerEvent, clearPendingActions, refreshSafety])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const oauthError = params.get('oauth_error')
    if (oauthError) {
      window.history.replaceState(null, '', window.location.pathname)
    } else if (params.get('login') === 'ok') {
      window.history.replaceState(null, '', window.location.pathname)
    }

    getAuthStatus()
      .then(setAuthStatus)
      .catch((error: unknown) => {
        setBootError(error instanceof Error ? error.message : 'Could not load authentication status')
      })
  }, [setBootError])

  useEffect(() => {
    const handleExpiredSession = () => {
      setSafetyStatus(null)
      setSafetyError('Session expired. Please sign in again.')
      setAuthStatus((current) => current ? { ...current, authenticated: false, user: null } : current)
      wsRef.current?.close()
      resetSession()
    }
    window.addEventListener('nova:session-expired', handleExpiredSession)
    return () => window.removeEventListener('nova:session-expired', handleExpiredSession)
  }, [resetSession])

  useEffect(() => {
    if (!authStatus || (authStatus.authRequired && !authStatus.authenticated)) return
    const initialTimer = window.setTimeout(() => void refreshSafety(), 0)
    const timer = window.setInterval(() => void refreshSafety(), 30_000)
    return () => {
      window.clearTimeout(initialTimer)
      window.clearInterval(timer)
    }
  }, [authStatus, refreshSafety])

  useEffect(() => {
    if (!authStatus) return
    if (authStatus.authRequired && !authStatus.authenticated) return

    let mounted = true

    startSession()
      .then(async (bootstrap) => {
        if (!mounted) return
        loadBootstrap(bootstrap)
        const snapshot = await getSession(bootstrap.sessionId)
        if (!mounted) return
        loadSnapshot(snapshot)
        void refreshSafety()

        const ws = new SessionWS(bootstrap.sessionId, bootstrap.sessionToken)
        wsRef.current = ws
        ws.on('*', handleServerEvent)
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
  }, [authStatus, bootNonce, handleServerEvent, loadBootstrap, loadSnapshot, refreshSafety, resetSession, setBootError, setWsStatus])

  useEffect(() => {
    document.documentElement.dataset.mode = engineMode ?? 'unset'
  }, [engineMode])

  useEffect(() => () => clearPendingActions(), [clearPendingActions])

  function send(command: ClientCommand): boolean {
    const actionKey = command.type
    if (pendingActionKeysRef.current.has(actionKey)) return false
    if (wsStatus !== 'live' || !wsRef.current) {
      const message = 'Backend disconnected. Reconnect before sending trading commands.'
      setActionError(message)
      toast.error(message)
      return false
    }
    pendingActionKeysRef.current.add(actionKey)
    setPendingActions(new Set(pendingActionKeysRef.current))
    setActionError('')
    const timeout = window.setTimeout(() => {
      if (!pendingActionKeysRef.current.has(actionKey)) return
      clearPendingActions([actionKey])
      const message = 'The backend did not confirm this action. Check status before trying again.'
      setActionError(message)
      toast.error(message)
    }, 15_000)
    pendingTimeoutsRef.current.set(actionKey, timeout)
    markCommandSent()
    if (!wsRef.current.send(command)) {
      clearPendingActions([actionKey])
      setActionError('Command was not sent because the backend connection is unavailable.')
      return false
    }
    return true
  }

  function sendWithUserMessage(command: ClientCommand, text: string): boolean {
    if (!send(command)) return false
    addUserMessage(text)
    return true
  }

  async function reconfigure() {
    if (reconfigurePending) return
    setReconfigurePending(true)
    setBootError('')
    setActionError('')
    try {
      await prepareReconfigure()
      wsRef.current?.close()
      clearPendingActions()
      resetSession()
      setBootNonce((current) => current + 1)
      toast.success('Session reset. Review setup before starting again.')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not reconfigure Nova'
      setActionError(message)
      toast.error(message)
    } finally {
      setReconfigurePending(false)
    }
  }

  async function signOut() {
    if (logoutPending) return
    setLogoutPending(true)
    try {
      await logout()
      wsRef.current?.close()
      clearPendingActions()
      resetSession()
      setSafetyStatus(null)
      setAuthStatus(null)
      const nextStatus = await getAuthStatus()
      setAuthStatus(nextStatus)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not sign out.')
    } finally {
      setLogoutPending(false)
    }
  }

  if (!authStatus && !bootError) {
    return <main className="nova-app"><div className="system-chip">Checking access</div></main>
  }

  if (authStatus?.authRequired && !authStatus.authenticated) {
    return <AuthLanding googleConfigured={authStatus.googleConfigured} error={authError} />
  }

  if (!authStatus) {
    return <main className="nova-app"><div className="error-banner">{bootError || 'Authentication status is unavailable.'}</div></main>
  }

  const engineLive = setupState === 'LIVE' || setupState === 'PAUSED'
  const setupPanel = (
    <SetupPanel
      state={setupState}
      flowStep={setupFlowStep}
      draft={setupDraft}
      lastError={lastSetupError}
      verifyPending={typing && setupFlowStep === 'broker'}
      pendingActions={pendingActions}
      safetyStatus={safetyStatus}
      connected={wsStatus === 'live'}
      lotSize={session?.lotSize ?? DEFAULT_NIFTY_LOT_SIZE}
      onSend={send}
      onUserReply={addUserMessage}
      onDraft={updateSetupDraft}
      onStep={setSetupFlowStep}
    />
  )

  return (
    <main className="nova-app">
      <Header
        status={wsStatus}
        clientId={config.broker?.clientId}
        engineLive={engineLive}
        engineMode={engineMode}
        setupState={setupState}
        userEmail={authStatus?.user?.email}
        onKill={() => send({ type: 'session.kill', data: {} })}
        onReconfigure={reconfigure}
        onLogout={authStatus?.authRequired ? signOut : undefined}
        killPending={pendingActions.has('session.kill')}
        reconfigurePending={reconfigurePending}
        logoutPending={logoutPending}
        actionError={actionError}
      />

      <SafetyBanner
        engineMode={engineMode}
        safety={safetyStatus}
        wsStatus={wsStatus}
        error={safetyError}
      />

      <div className="safety-overview-shell">
        <SafetyReadiness
          auth={authStatus}
          safety={safetyStatus}
          session={session}
          config={config}
        />
      </div>

      <StrategyPlatform refreshNonce={strategyRefreshNonce} isAdmin={authStatus.isAdmin} />

      <section className={engineLive ? 'engine-shell' : 'chat-shell'} aria-label="Nova trading session">
        {bootError ? <div className="error-banner">{bootError}</div> : null}
        {!session && !bootError ? <div className="system-chip">Starting session</div> : null}

        <div className="engine-main-pane">
          <ChatLog
            messages={messages}
            typing={typing}
            panelKey={`${setupState}-${setupFlowStep}-${engineLive ? 'engine' : 'setup'}`}
            inlinePanel={setupFlowStep === 'mode' || setupFlowStep === 'strategy' ? setupPanel : null}
          >
            {setupFlowStep === 'mode' || setupFlowStep === 'strategy' ? null : setupPanel}
          </ChatLog>
          {engineLive ? (
            <div className="live-engine-stack">
              <EngineListening
                paused={setupState === 'PAUSED'}
                activeTrade={activeTrade}
                side={config.risk?.side ?? 'BOTH'}
                engineMode={engineMode}
              />
            </div>
          ) : null}
        </div>

        {engineLive ? (
          <EngineSidebar
            session={session}
            state={setupState}
            wallet={wallet}
            marginUtilized={marginUtilized}
            realizedPnl={realizedPnl}
            activeTrade={activeTrade}
            lotSize={session?.lotSize ?? DEFAULT_NIFTY_LOT_SIZE}
            side={config.risk?.side ?? 'BOTH'}
            engineMode={engineMode}
            safetyStatus={safetyStatus}
            pendingActions={pendingActions}
            connected={wsStatus === 'live'}
            actionError={actionError}
            onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
          />
        ) : null}
      </section>

      <footer className="app-footer">(c) 2026 Layman Signal Route. Paper beta; live trading remains policy-gated.</footer>
    </main>
  )
}

function settledActionsForEvent(event: ServerEvent): string[] {
  if (event.type === 'session.error' || event.type === 'order.rejected') return []
  if (event.type === 'funds.update') return ['setup.broker_creds']
  if (event.type === 'trade.exit') return ['session.exit_open']
  if (event.type === 'session.eod') return ['session.kill', 'session.exit_open']
  if (event.type === 'tick.pnl') return ['session.apply_sr_suggestion']
  if (event.type !== 'setup.state') return []

  const state = String(event.data.state ?? '')
  if (state === 'MODE_PICKED') return ['setup.mode']
  if (state === 'STRATEGY_PICKED') return ['setup.select_strategy']
  if (state === 'BROKER_CONNECTED') return ['setup.broker_creds']
  if (state === 'RISK_CONFIGURED') return ['setup.risk']
  if (state === 'EXITS_CONFIGURED' || state === 'READY_TO_LAUNCH') return ['setup.exits']
  if (state === 'LIVE') return ['setup.confirm_live', 'session.resume', 'session.patch_risk']
  if (state === 'PAUSED') return ['session.pause', 'session.patch_risk']
  if (state === 'ENDED') return ['session.kill']
  return []
}

function actionSuccessMessage(action: string): string | null {
  if (action === 'setup.broker_creds') return 'Dhan account verified. Access token value is hidden.'
  if (action === 'setup.confirm_live') return 'Engine start confirmed by the backend.'
  if (action === 'session.pause') return 'New entry requests are blocked.'
  if (action === 'session.resume') return 'Entry requests are allowed.'
  if (action === 'session.exit_open') return 'Position exit completed.'
  if (action === 'session.kill') return 'Routing stopped and the tracked position exit flow completed.'
  if (action === 'session.apply_sr_suggestion') return 'Suggested paper SL/TP applied.'
  return null
}

function readInitialAuthError(): string {
  const oauthError = new URLSearchParams(window.location.search).get('oauth_error')
  return oauthError ? authErrorMessage(oauthError) : ''
}

export default App

function authErrorMessage(code: string): string {
  if (code === 'not_allowed') return 'This Google account is not approved for the beta.'
  if (code === 'invalid_state') return 'Login expired. Try again.'
  if (code === 'email_not_verified') return 'Google has not verified this email address.'
  if (code === 'missing_google_profile') return 'Google did not return the required profile data.'
  return 'Google login failed. Try again.'
}

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
