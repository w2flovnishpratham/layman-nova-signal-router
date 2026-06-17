import { useCallback, useEffect, useRef, useState } from 'react'
import { AuthScreen } from './components/AuthScreen'
import { EngineListening } from './components/EngineListening'
import { EngineSidebar } from './components/EngineSidebar'
import { Header } from './components/Header'
import { ChatLog } from './components/messages/ChatLog'
import { SetupPanel } from './components/setup/SetupPanel'
import { PortfolioDashboard } from './dashboard/PortfolioDashboard'
import type { NovaView } from './types'
import {
  getCurrentUser,
  getMarketSnapshot,
  getSession,
  getSystemHealth,
  googleLoginUrl,
  logout,
  prepareReconfigure,
  startSession,
} from './api'
import type { AuthUser } from './api'
import { DEFAULT_NIFTY_LOT_SIZE } from './lib/trading'
import { useSessionStore } from './state/sessionStore'
import { SessionWS } from './ws'
import type { ClientCommand, MarketSnapshot, SystemHealth } from './types'

function App() {
  const wsRef = useRef<SessionWS | null>(null)
  const [bootNonce, setBootNonce] = useState(0)
  const [view, setView] = useState<NovaView>('trading')
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [authError, setAuthError] = useState('')
  const [marketSnapshot, setMarketSnapshot] = useState<MarketSnapshot | null>(null)
  const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null)
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
      Promise.allSettled([getMarketSnapshot(), getSystemHealth()]).then(([market, health]) => {
        if (!mounted) return
        if (market.status === 'fulfilled') setMarketSnapshot(market.value)
        if (health.status === 'fulfilled') setSystemHealth(health.value)
      })
      timer = window.setTimeout(poll, 4000)
    }

    poll()
    return () => {
      mounted = false
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [authUser, bootNonce])

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
    markCommandSent()
    wsRef.current?.send(command)
  }

  function sendWithUserMessage(command: ClientCommand, text: string) {
    addUserMessage(text)
    send(command)
  }

  async function reconfigure() {
    setBootError('')
    try {
      await prepareReconfigure()
      wsRef.current?.close()
      resetSession()
      setBootNonce((current) => current + 1)
    } catch (error) {
      setBootError(error instanceof Error ? error.message : 'Could not reconfigure Nova')
    }
  }

  async function handleLogout() {
    wsRef.current?.close()
    resetSession()
    setBootError('')
    try {
      await logout()
    } finally {
      setAuthUser(null)
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

  const engineLive = setupState === 'LIVE' || setupState === 'PAUSED'
  const setupPanel = (
    <SetupPanel
      state={setupState}
      flowStep={setupFlowStep}
      draft={setupDraft}
      lastError={lastSetupError}
      verifyPending={typing && setupFlowStep === 'broker'}
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
        health={systemHealth}
        user={authUser}
        view={view}
        onNavigate={setView}
        onKill={() => send({ type: 'session.kill', data: {} })}
        onReconfigure={reconfigure}
        onLogout={handleLogout}
      />

      {view === 'dashboard' ? (
        <PortfolioDashboard />
      ) : (
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
            state={setupState}
            wallet={wallet}
            marginUtilized={marginUtilized}
            realizedPnl={realizedPnl}
            activeTrade={activeTrade}
            lotSize={session?.lotSize ?? DEFAULT_NIFTY_LOT_SIZE}
            side={config.risk?.side ?? 'BOTH'}
            engineMode={engineMode}
            marketSnapshot={marketSnapshot}
            health={systemHealth}
            onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
          />
        ) : null}
      </section>
      )}

      <footer className="app-footer">(c) 2026 Layman Signal Route. Deployed Live with Dhan. All rights reserved.</footer>
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
