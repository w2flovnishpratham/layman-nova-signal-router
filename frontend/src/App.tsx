import { useCallback, useEffect, useRef, useState } from 'react'
import { BarChart3, LineChart, Loader2, Sliders, Wallet } from 'lucide-react'
import { AuthScreen } from './components/AuthScreen'
import { EngineLeftPanel } from './components/EngineLeftPanel'
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
  const [leftDrawerOpen, setLeftDrawerOpen] = useState(false)
  const [rightDrawerOpen, setRightDrawerOpen] = useState(false)
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
      sharedMarketData={session?.sharedMarketData ?? false}
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
        <section className={engineLive ? 'engine-shell grid grid-cols-1 lg:grid-cols-12 gap-5 px-4 w-full max-w-[1480px] mx-auto lg:h-[calc(100vh-120px)] lg:min-h-0 min-h-screen items-stretch' : 'chat-shell'} aria-label="Nova trading session">
        {bootError ? <div className="error-banner col-span-full">{bootError}</div> : null}
        {!session && !bootError ? <div className="system-chip col-span-full">Starting session</div> : null}

        {engineLive ? (
          <aside className="hidden lg:block lg:col-span-4 lg:h-full lg:overflow-y-auto pr-1">
            <EngineLeftPanel marketSnapshot={marketSnapshot} engineMode={engineMode} activeTrade={activeTrade} />
          </aside>
        ) : null}

        <div className="engine-main-pane col-span-1 lg:col-span-5 lg:h-full flex flex-col min-h-[450px] lg:min-h-0">
          {!session && !bootError ? (
            <div className="flex flex-col items-center justify-center flex-grow min-h-[350px] gap-3 text-white/40">
              <Loader2 className="w-8 h-8 animate-spin text-[#9d5bff]" />
              <span className="text-xs font-semibold tracking-widest uppercase animate-pulse">Initializing Session...</span>
            </div>
          ) : (
            <>
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

              {/* Mobile-only inline active position & routing controls below main chat */}
              {engineLive ? (
                <div className="block lg:hidden mt-6">
                  <EngineSidebar
                    state={setupState}
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
        </div>

        {engineLive ? (
          <aside className="hidden lg:block lg:col-span-3 lg:h-full lg:overflow-y-auto pr-1">
            <EngineSidebar
              state={setupState}
              wallet={wallet}
              marginUtilized={marginUtilized}
              realizedPnl={realizedPnl}
              activeTrade={activeTrade}
              lotSize={session?.lotSize ?? DEFAULT_NIFTY_LOT_SIZE}
              side={config.risk?.side ?? 'BOTH'}
              engineMode={engineMode}
              onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
            />
          </aside>
        ) : null}
      </section>
      )}

      {/* Mobile-only sliding drawers and floating action bar */}
      {engineLive && (
        <>
          {/* Bottom-to-Top Drawer: Market LTP & Manual Order */}
          {leftDrawerOpen && (
            <div className="fixed inset-0 z-[110] block lg:hidden">
              <div className="fixed inset-0 bg-black/60 backdrop-blur-sm drawer-backdrop" onClick={() => setLeftDrawerOpen(false)} />
              <div className="fixed inset-x-0 bottom-0 max-h-[85vh] h-auto bg-[#0f0e1c] border-t border-white/10 rounded-t-2xl p-5 pb-8 overflow-y-auto shadow-2xl flex flex-col gap-4 drawer-content">
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
                <EngineLeftPanel marketSnapshot={marketSnapshot} engineMode={engineMode} activeTrade={activeTrade} />
              </div>
            </div>
          )}

          {/* Bottom-to-Top Drawer: Margin & Balance */}
          {rightDrawerOpen && (
            <div className="fixed inset-0 z-[110] block lg:hidden">
              <div className="fixed inset-0 bg-black/60 backdrop-blur-sm drawer-backdrop" onClick={() => setRightDrawerOpen(false)} />
              <div className="fixed inset-x-0 bottom-0 max-h-[85vh] h-auto bg-[#0f0e1c] border-t border-white/10 rounded-t-2xl p-5 pb-8 overflow-y-auto shadow-2xl flex flex-col gap-4 drawer-content">
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
                  state={setupState}
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
              </div>
            </div>
          )}

          {/* Sticky Bottom Navigation Bar on Mobile */}
          <div className="fixed bottom-0 left-0 right-0 h-16 bg-[#0c0a14]/95 backdrop-blur-md border-t border-white/10 flex items-center justify-around z-[90] lg:hidden px-2">
            <button
              type="button"
              className={`flex flex-col items-center justify-center gap-1 flex-1 py-1 mx-1 h-[80%] rounded-xl border-0 transition-all cursor-pointer ${view === 'trading' && !leftDrawerOpen && !rightDrawerOpen ? 'bg-[rgba(157,91,255,0.12)] text-[#9d5bff] font-semibold' : 'bg-transparent text-white/40 hover:text-white/70'}`}
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
              className={`flex flex-col items-center justify-center gap-1 flex-1 py-1 mx-1 h-[80%] rounded-xl border-0 transition-all cursor-pointer ${view === 'dashboard' ? 'bg-[rgba(157,91,255,0.12)] text-[#9d5bff] font-semibold' : 'bg-transparent text-white/40 hover:text-white/70'}`}
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
              className={`flex flex-col items-center justify-center gap-1 flex-1 py-1 mx-1 h-[80%] rounded-xl border-0 transition-all cursor-pointer ${leftDrawerOpen ? 'bg-[rgba(157,91,255,0.12)] text-[#9d5bff] font-semibold' : 'bg-transparent text-white/40 hover:text-white/70'}`}
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
              className={`flex flex-col items-center justify-center gap-1 flex-1 py-1 mx-1 h-[80%] rounded-xl border-0 transition-all cursor-pointer ${rightDrawerOpen ? 'bg-[rgba(157,91,255,0.12)] text-[#9d5bff] font-semibold' : 'bg-transparent text-white/40 hover:text-white/70'}`}
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
