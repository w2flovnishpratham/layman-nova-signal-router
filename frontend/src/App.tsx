import { useEffect, useRef, useState } from 'react'
import { AuthLanding } from './components/AuthLanding'
import { EngineListening } from './components/EngineListening'
import { EngineSidebar } from './components/EngineSidebar'
import { Header } from './components/Header'
import { ChatLog } from './components/messages/ChatLog'
import { SetupPanel } from './components/setup/SetupPanel'
import { getAuthStatus, getSession, logout, prepareReconfigure, startSession } from './api'
import { DEFAULT_NIFTY_LOT_SIZE } from './lib/trading'
import { useSessionStore } from './state/sessionStore'
import { SessionWS } from './ws'
import type { AuthStatus, ClientCommand } from './types'

function App() {
  const wsRef = useRef<SessionWS | null>(null)
  const [bootNonce, setBootNonce] = useState(0)
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null)
  const [authError, setAuthError] = useState('')
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

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const oauthError = params.get('oauth_error')
    if (oauthError) {
      setAuthError(authErrorMessage(oauthError))
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
  }, [applyServerEvent, authStatus, bootNonce, loadBootstrap, loadSnapshot, resetSession, setBootError, setWsStatus])

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

  async function signOut() {
    await logout()
    wsRef.current?.close()
    resetSession()
    setAuthStatus(null)
    const nextStatus = await getAuthStatus()
    setAuthStatus(nextStatus)
  }

  if (!authStatus && !bootError) {
    return <main className="nova-app"><div className="system-chip">Checking access</div></main>
  }

  if (authStatus?.authRequired && !authStatus.authenticated) {
    return <AuthLanding googleConfigured={authStatus.googleConfigured} error={authError} />
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
        userEmail={authStatus?.user?.email}
        onKill={() => send({ type: 'session.kill', data: {} })}
        onReconfigure={reconfigure}
        onLogout={authStatus?.authRequired ? signOut : undefined}
      />

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
            onSend={(command) => sendWithUserMessage(command, commandMessage(command))}
          />
        ) : null}
      </section>

      <footer className="app-footer">(c) 2026 Layman Signal Route. Deployed Live with Dhan. All rights reserved.</footer>
    </main>
  )
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
