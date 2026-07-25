import { Check, Copy, LogOut, RotateCcw, ShieldAlert, Wifi, X } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import type { AuthUser, LogoutEngineAction, RuntimeStatus } from '../api'
import { getEgressStatus } from '../api'
import type { AppRoute } from '../appRoutes'
import { modeBadgeText } from '../lib/mode'
import { MotionPing, MotionProgressFill, softEase, useAppReducedMotion } from './MotionPrimitives'
import type { EngineMode, MarketSnapshot, SetupState, SystemHealth, WsStatus } from '../types'

interface Props {
  route: AppRoute
  status: WsStatus
  clientId?: string
  runtime: RuntimeStatus | null
  engineLive: boolean
  engineMode: EngineMode | null
  setupState: SetupState
  health: SystemHealth | null
  user: AuthUser
  market: MarketSnapshot | null
  onNavigate: (route: AppRoute) => void
  onKill: () => void
  onStop: () => void
  onReconfigure: () => void
  onLogout: (action: LogoutEngineAction) => void
  onMode: (mode: 'paper' | 'live') => void
  onSaveConfig: (mode: 'paper' | 'live', lots: number, sl: number, tp: number) => void
  onPaperReset: () => void
  onAccountRefresh: () => void
}

export function Header({
  route,
  status,
  clientId,
  runtime,
  engineLive,
  engineMode,
  setupState,
  health,
  user,
  market,
  onNavigate,
  onKill,
  onStop,
  onReconfigure,
  onLogout,
  onMode,
  onSaveConfig,
  onPaperReset,
  onAccountRefresh,
}: Props) {
  const [killDialogOpen, setKillDialogOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [holdingKill, setHoldingKill] = useState(false)
  const [logoutDialogOpen, setLogoutDialogOpen] = useState(false)
  const [reconfigureDialogOpen, setReconfigureDialogOpen] = useState(false)
  const [lots, setLots] = useState<number | null>(null)
  const [slPercent, setSlPercent] = useState<number | null>(null)
  const [tpPercent, setTpPercent] = useState<number | null>(null)
  const [staticIp, setStaticIp] = useState<string | null>(null)
  const [ipCopied, setIpCopied] = useState(false)
  const [clock, setClock] = useState(() => new Date())
  const holdTimer = useRef<number | null>(null)

  // Fetch the assigned Nova Static IP whenever the account menu opens, so a
  // paid user can view/copy their dedicated IP at any time (e.g. to whitelist
  // it in Dhan), not only during the one-time setup step.
  useEffect(() => {
    if (!menuOpen) return
    let active = true
    getEgressStatus()
      .then((egress) => { if (active) setStaticIp(egress?.public_ip ?? null) })
      .catch(() => { if (active) setStaticIp(null) })
    return () => { active = false }
  }, [menuOpen])

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const activeConfig = runtime?.config.active
  const displayedLots = lots ?? Number(activeConfig?.configured_lots ?? 1)
  const displayedSlPercent = slPercent ?? Number(activeConfig?.option_sl_percent ?? 10)
  const displayedTpPercent = tpPercent ?? Number(activeConfig?.option_tp_percent ?? 20)

  function chooseMode(mode: 'paper' | 'live') {
    setLots(null)
    setSlPercent(null)
    setTpPercent(null)
    onMode(mode)
  }

  function copyStaticIp() {
    if (!staticIp) return
    void navigator.clipboard?.writeText(staticIp)
    setIpCopied(true)
    window.setTimeout(() => setIpCopied(false), 1500)
  }
  const reduceMotion = useAppReducedMotion()
  const popTransition = reduceMotion ? { duration: 0 } : { duration: 0.18, ease: softEase }

  function cancelHold() {
    if (holdTimer.current !== null) {
      window.clearTimeout(holdTimer.current)
      holdTimer.current = null
    }
    setHoldingKill(false)
  }

  function startHold() {
    cancelHold()
    setHoldingKill(true)
    holdTimer.current = window.setTimeout(() => {
      holdTimer.current = null
      setHoldingKill(false)
      setKillDialogOpen(false)
      onKill()
    }, 800)
  }

  function closeDialog() {
    cancelHold()
    setKillDialogOpen(false)
  }

  return (
    <>
      <header className="app-header">
        <div className="brand-lockup">
          <span className="brand-copy"><strong>NOVA</strong><span>Signal Router</span></span>
        </div>

        <nav className="top-navigation" aria-label="Primary navigation">
          {TOP_NAVIGATION.map((item) => (
            <button
              type="button"
              key={item.label}
              className={route === item.route ? 'is-active' : ''}
              aria-current={route === item.route ? 'page' : undefined}
              onClick={() => onNavigate(item.route)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="header-runtime">
          <div className={`header-market-status is-${market?.marketStatus ?? 'closed'}`}>
            <span>{market?.marketStatus === 'open' ? 'Market Open' : 'Market Closed'}</span>
            <time>{clock.toLocaleTimeString('en-IN', { hour12: false, timeZone: 'Asia/Kolkata' })} IST</time>
          </div>
          <div className="header-nifty">
            <span>NIFTY 50</span>
            <strong>{market?.niftySpot == null ? '—' : market.niftySpot.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</strong>
            <em className={(market?.dayChangePct ?? 0) >= 0 ? 'positive' : 'negative'}>
              {market?.dayChangePct == null ? '—' : `${market.dayChangePct >= 0 ? '+' : ''}${market.dayChangePct.toFixed(2)}%`}
            </em>
          </div>
        </div>

        <div className="header-actions relative flex items-center gap-2">
          <div className={`mode-badge mode-badge-${engineMode ?? 'unset'}`}>
            <MotionPing className={status === 'live' ? 'mode-status-ok' : 'mode-status-warn'} />
            {modeBadgeText(engineMode)}{engineLive ? ` - ${statusLabel(status, setupState)}` : ''}
          </div>

          <button
            type="button"
            className="header-user"
            onClick={() => setMenuOpen(!menuOpen)}
            aria-label="Open account menu"
            aria-expanded={menuOpen}
            aria-haspopup="menu"
          >
            {user.picture_url ? <img src={user.picture_url} alt="" referrerPolicy="no-referrer" /> : <span>{initials(user.name || user.email)}</span>}
            <span className="header-user-copy">
              <strong>{user.name || user.email}</strong>
              <small>{formatMoney(runtime?.pnl.available_balance)}</small>
            </span>
          </button>

          <AnimatePresence initial={false}>
            {menuOpen ? (
              <>
                <motion.div
                  key="header-menu-backdrop"
                  className="fixed inset-0 z-30 cursor-default"
                  onClick={() => setMenuOpen(false)}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={popTransition}
                />
                <motion.div
                  key="header-menu"
                  className="absolute right-0 top-full mt-2 w-72 bg-[#12111b] border border-white/10 rounded-xl shadow-2xl p-4 flex flex-col gap-4 z-40"
                  initial={{ opacity: 0, y: -8, scale: 0.98 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: -6, scale: 0.98 }}
                  transition={popTransition}
                >
                <div className="flex items-center gap-3 pb-3 border-b border-white/5">
                  {user.picture_url ? (
                    <img src={user.picture_url} alt="" referrerPolicy="no-referrer" className="w-10 h-10 rounded-full border border-white/10 object-cover" />
                  ) : null}
                  <div className="flex flex-col min-w-0">
                    <span className="font-semibold text-sm truncate text-white">{user.name || user.email}</span>
                    <span className="text-xs text-white/50 truncate">{user.email}</span>
                  </div>
                </div>

                {(runtime?.account.dhan_client_id_masked || clientId) && (
                  <div className="text-xs text-white/70 bg-white/5 px-2.5 py-1.5 rounded-lg border border-white/5 flex justify-between items-center">
                    <span className="opacity-70">Client ID:</span>
                    <strong className="font-mono">{runtime?.account.dhan_client_id_masked || maskClientId(clientId || '')}</strong>
                  </div>
                )}

                {runtime ? (
                  <div className="text-xs bg-white/5 px-2.5 py-2 rounded-lg border border-white/5 flex flex-col gap-1">
                    <strong className={runtime.engine.running ? 'text-emerald-300' : 'text-amber-300'}>
                      {runtime.engine.display}
                    </strong>
                    <span className="text-white/55">
                      {runtime.engine.mode?.toUpperCase() || 'NO MODE'} · P&amp;L {runtime.pnl.session ?? '—'}
                    </span>
                    {runtime.position.has_open_position ? (
                      <span className="text-amber-200">
                        {runtime.position.trading_symbol || runtime.position.option_side || 'Position'} · Qty {runtime.position.qty}
                      </span>
                    ) : <span className="text-emerald-200">Flat — no active option position</span>}
                    {runtime.position.has_open_position ? (
                      <>
                        <span className={runtime.position.ltp.stale || runtime.position.ltp.value == null ? 'text-amber-300' : 'text-white/45'}>
                          {runtime.position.ltp.value == null
                            ? 'Live option quote unavailable'
                            : `LTP ${runtime.position.ltp.value} · ${runtime.position.ltp.status}`}
                        </span>
                        {(runtime.position.ltp.stale || runtime.position.ltp.value == null) ? (
                          <span className="text-white/40">
                            {runtime.position.ltp.source || 'source unknown'}
                            {runtime.position.ltp.received_at ? ` · ${new Date(runtime.position.ltp.received_at).toLocaleTimeString()}` : ''}
                            {runtime.position.ltp.age_seconds != null ? ` · ${Math.round(runtime.position.ltp.age_seconds)}s old` : ''}
                          </span>
                        ) : null}
                      </>
                    ) : <span className="text-white/45">No active LTP</span>}
                  </div>
                ) : null}

                {staticIp && (
                  <div className="text-xs text-white/70 bg-white/5 px-2.5 py-1.5 rounded-lg border border-white/5 flex justify-between items-center gap-2">
                    <span className="opacity-70 flex items-center gap-1.5"><Wifi size={12} /> Nova Static IP:</span>
                    <span className="flex items-center gap-2 min-w-0">
                      <strong className="font-mono truncate">{staticIp}</strong>
                      <button
                        type="button"
                        onClick={copyStaticIp}
                        aria-label="Copy static IP"
                        title="Copy static IP"
                        className="p-1 rounded-md border border-white/10 hover:bg-white/10 transition-colors text-white/70 hover:text-white"
                      >
                        {ipCopied ? <Check size={12} /> : <Copy size={12} />}
                      </button>
                    </span>
                  </div>
                )}

                {engineLive && (
                  <div className="flex flex-col gap-2 py-1">
                    <span className="text-[10px] uppercase font-bold tracking-wider text-white/40">System Health</span>
                    <HealthStrip setupState={setupState} health={health} />
                  </div>
                )}

                <div className="flex flex-col gap-2 pt-2 border-t border-white/5">
                  {runtime && runtime.engine.state === 'STOPPED' ? (
                    <div className="flex flex-col gap-2 pb-2 border-b border-white/5">
                      <span className="text-[10px] uppercase font-bold tracking-wider text-white/40">Stopped configuration</span>
                      <div className="grid grid-cols-2 gap-2">
                        {(['paper', 'live'] as const).map((mode) => (
                          <button
                            key={mode}
                            type="button"
                            disabled={runtime.position.has_open_position}
                            onClick={() => chooseMode(mode)}
                            className={`py-1.5 rounded-lg border text-xs font-semibold ${
                              runtime.engine.mode === mode
                                ? 'border-violet-400/60 bg-violet-500/20 text-white'
                                : 'border-white/10 bg-white/5 text-white/60'
                            } disabled:opacity-40`}
                          >
                            {mode === 'paper' ? 'Paper' : 'Live'}
                          </button>
                        ))}
                      </div>
                      <div className="grid grid-cols-3 gap-1.5">
                        <label className="text-[10px] text-white/50">Lots
                          <input aria-label="Runtime lots" type="number" min={1} max={20} value={displayedLots} onChange={(event) => setLots(Number(event.target.value))} className="w-full mt-1 bg-black/20 border border-white/10 rounded px-1 py-1 text-white" />
                        </label>
                        <label className="text-[10px] text-white/50">SL %
                          <input aria-label="Runtime stop loss percent" type="number" min={0} max={100} value={displayedSlPercent} onChange={(event) => setSlPercent(Number(event.target.value))} className="w-full mt-1 bg-black/20 border border-white/10 rounded px-1 py-1 text-white" />
                        </label>
                        <label className="text-[10px] text-white/50">TP %
                          <input aria-label="Runtime target profit percent" type="number" min={0} max={1000} value={displayedTpPercent} onChange={(event) => setTpPercent(Number(event.target.value))} className="w-full mt-1 bg-black/20 border border-white/10 rounded px-1 py-1 text-white" />
                        </label>
                      </div>
                      <button
                        type="button"
                        disabled={!runtime.engine.mode || runtime.position.has_open_position}
                        onClick={() => runtime.engine.mode && onSaveConfig(runtime.engine.mode, displayedLots, displayedSlPercent, displayedTpPercent)}
                        className="secondary-button py-1.5 rounded-lg text-xs disabled:opacity-40"
                      >
                        Save {runtime.engine.mode?.toUpperCase() || ''} settings
                      </button>
                      {runtime.engine.mode === 'paper' ? (
                        <button type="button" disabled={runtime.position.has_open_position} onClick={onPaperReset} className="secondary-button py-1.5 rounded-lg text-xs disabled:opacity-40">
                          Reset Paper session
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                  {engineLive && (
                    <>
                      <button
                        className="secondary-button w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-white/80 bg-white/5 border border-white/10 text-sm font-medium"
                        type="button"
                        onClick={() => {
                          setMenuOpen(false)
                          onStop()
                        }}
                      >
                        Stop Engine
                      </button>
                      <button
                        className="kill-button w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-red-400 hover:text-white bg-red-950/30 hover:bg-red-900/40 border border-red-900/30 hover:border-red-500/40 transition-all font-medium text-sm"
                        type="button"
                        onClick={() => {
                          setMenuOpen(false)
                          setKillDialogOpen(true)
                        }}
                      >
                        <ShieldAlert size={14} />
                        Stop & Square Off
                      </button>
                      <button
                        className="secondary-button w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-white/80 hover:text-white bg-white/5 hover:bg-white/10 border border-white/10 transition-all text-sm font-medium"
                        type="button"
                        onClick={() => {
                          setMenuOpen(false)
                          setReconfigureDialogOpen(true)
                        }}
                      >
                        <RotateCcw size={14} />
                        Re-Configure
                      </button>
                    </>
                  )}
                  {!engineLive && runtime?.engine.state === 'STOPPED' && (
                    <button
                      className="secondary-button w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-white/80 bg-white/5 border border-white/10 text-sm font-medium"
                      type="button"
                      onClick={() => {
                        setMenuOpen(false)
                        setReconfigureDialogOpen(true)
                      }}
                    >
                      <RotateCcw size={14} />
                      Re-Configure
                    </button>
                  )}
                  <button type="button" onClick={onAccountRefresh} className="secondary-button w-full py-2 rounded-lg text-xs">
                    Refresh Dhan account
                  </button>
                  <button
                    className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-white/60 hover:text-white bg-white/5 hover:bg-white/10 border border-white/5 hover:border-white/15 transition-all text-sm font-medium"
                    type="button"
                    onClick={() => {
                      setMenuOpen(false)
                      setLogoutDialogOpen(true)
                    }}
                  >
                    <LogOut size={14} />
                    Log Out
                  </button>
                </div>
                </motion.div>
              </>
            ) : null}
          </AnimatePresence>
        </div>
      </header>

      <AnimatePresence initial={false}>
        {killDialogOpen ? (
          <motion.div
            className="modal-backdrop fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            role="presentation"
            onPointerDown={(event) => {
              if (event.currentTarget === event.target) closeDialog()
            }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={popTransition}
          >
            <motion.section
              className="kill-dialog relative w-full max-w-[380px] bg-[#12101c] border border-red-500/20 rounded-2xl shadow-2xl p-6 flex flex-col items-center text-center gap-4"
              role="dialog"
              aria-modal="true"
              aria-labelledby="kill-dialog-title"
              initial={{ opacity: 0, y: 10, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.98 }}
              transition={popTransition}
            >
            <button
              className="absolute right-4 top-4 text-white/40 hover:text-white hover:bg-white/5 p-1.5 rounded-lg transition-colors border-0 cursor-pointer"
              type="button"
              aria-label="Close stop and square-off confirmation"
              onClick={closeDialog}
            >
              <X size={16} />
            </button>

            <div className="w-12 h-12 rounded-full bg-red-500/10 border border-red-500/20 flex items-center justify-center text-red-400 mt-2">
              <ShieldAlert size={24} />
            </div>

            <div className="flex flex-col gap-1.5">
              <h2 id="kill-dialog-title" className="text-lg font-bold text-white tracking-wide m-0">
                Stop routing and square off?
              </h2>
              <p className="text-xs text-white/60 leading-relaxed px-2 m-0">
                This stops new entries and exits NOVA's tracked open position.
              </p>
            </div>

            <div className="w-full flex flex-col gap-2 mt-2">
              <button
                className="hold-confirm w-full py-3 px-4 rounded-xl font-semibold border border-red-500/30 bg-red-500/10 hover:bg-red-500/20 text-red-400 hover:text-white transition-all duration-150 flex items-center justify-center gap-2 text-sm select-none cursor-pointer relative overflow-hidden"
                type="button"
                onPointerDown={startHold}
                onPointerUp={cancelHold}
                onPointerLeave={cancelHold}
                onPointerCancel={cancelHold}
                onKeyDown={(event) => {
                  if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) startHold()
                }}
                onKeyUp={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') cancelHold()
                }}
              >
                {holdingKill ? <MotionProgressFill durationSeconds={0.8} tone="danger" /> : null}
                <span className="hold-button-content">Hold to Stop & Square Off</span>
              </button>
              <button
                className="w-full py-2.5 px-4 rounded-xl text-white/50 hover:text-white bg-transparent hover:bg-white/5 transition-all text-xs font-semibold cursor-pointer border-0"
                type="button"
                onClick={closeDialog}
              >
                Cancel
              </button>
            </div>
            </motion.section>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence initial={false}>
        {logoutDialogOpen ? (
          <motion.div className="modal-backdrop fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <motion.section className="kill-dialog w-full max-w-[420px] bg-[#12101c] border border-white/10 rounded-2xl shadow-2xl p-6 flex flex-col gap-4" role="dialog" aria-modal="true" aria-labelledby="logout-dialog-title">
              <h2 id="logout-dialog-title" className="text-lg font-bold text-white m-0">Log out of NOVA</h2>
              <p className="text-xs text-white/60 m-0">
                Choose what the server should do with your engine. Closing this browser alone never stops it.
              </p>
              <button type="button" className="secondary-button py-3 rounded-xl text-sm" onClick={() => { setLogoutDialogOpen(false); onLogout('keep_running') }}>
                Keep engine running and log out
              </button>
              <button type="button" className="secondary-button py-3 rounded-xl text-sm" onClick={() => { setLogoutDialogOpen(false); onLogout('stop_engine') }}>
                Stop engine and log out
              </button>
              <button type="button" className="py-2 text-xs text-white/50" onClick={() => setLogoutDialogOpen(false)}>
                Cancel
              </button>
            </motion.section>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence initial={false}>
        {reconfigureDialogOpen ? (
          <motion.div className="modal-backdrop fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <motion.section className="kill-dialog w-full max-w-[420px] bg-[#12101c] border border-white/10 rounded-2xl shadow-2xl p-6 flex flex-col gap-4" role="dialog" aria-modal="true" aria-labelledby="reconfigure-dialog-title">
              <h2 id="reconfigure-dialog-title" className="text-lg font-bold text-white m-0">Stop engine and reconfigure?</h2>
              <p className="text-xs text-white/60 m-0">
                The engine will stop and will not restart automatically. Any tracked open position is preserved; position-invalidating changes stay blocked until flat.
              </p>
              <button type="button" className="secondary-button py-3 rounded-xl text-sm" onClick={() => { setReconfigureDialogOpen(false); onReconfigure() }}>
                Stop and continue
              </button>
              <button type="button" className="py-2 text-xs text-white/50" onClick={() => setReconfigureDialogOpen(false)}>
                Cancel
              </button>
            </motion.section>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </>
  )
}

const TOP_NAVIGATION: Array<{ route: AppRoute; label: string }> = [
  { route: 'dashboard', label: 'Dashboard' },
  { route: 'trading', label: 'Trading' },
  { route: 'strategies', label: 'Strategies' },
  { route: 'signals', label: 'Signals' },
  { route: 'automations', label: 'Automations' },
  { route: 'webhooks', label: 'Webhooks' },
  { route: 'credentials', label: 'Credentials' },
  { route: 'risk', label: 'Risk' },
  { route: 'reports', label: 'Reports' },
  { route: 'settings', label: 'Settings' },
]

function initials(value: string): string {
  return value
    .split(/\s+|@/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('')
}

function formatMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'Balance unavailable'
  return `₹${value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function HealthStrip({ setupState, health }: { setupState: SetupState; health: SystemHealth | null }) {
  const chips = [
    {
      key: 'dhan',
      label: 'Dhan',
      value: dhanLabel(health?.dhan),
      tone: health?.dhan === 'connected' ? 'ok' : health?.dhan === 'auth_issue' ? 'bad' : 'muted',
    },
    {
      key: 'market',
      label: 'Market',
      value: health?.market === 'open' ? 'Open' : 'Closed',
      tone: health?.market === 'open' ? 'ok' : 'muted',
    },
    {
      key: 'feed',
      label: 'Feed',
      value: feedLabel(health?.feed),
      tone: feedTone(health?.feed),
      title: health?.feedReason ?? undefined,
    },
    {
      key: 'engine',
      label: 'Engine',
      value: setupState === 'PAUSED' ? 'Paused' : health?.engine === 'listening' ? 'Listening' : 'Idle',
      tone: setupState === 'PAUSED' ? 'warn' : health?.engine === 'listening' ? 'ok' : 'muted',
    },
  ] as Array<{ key: string; label: string; value: string; tone: string; title?: string }>

  return (
    <div className="health-strip" aria-label="System health">
      {chips.map((chip) => (
        <span key={chip.key} className={`health-chip ${chip.tone}`} title={chip.title ?? `${chip.label}: ${chip.value}`}>
          <span>{chip.label}</span>
          <strong>{chip.value}</strong>
        </span>
      ))}
    </div>
  )
}

function feedLabel(value: SystemHealth['feed'] | undefined): string {
  if (value === 'live') return 'Live'
  if (value === 'stale') return 'Stale'
  if (value === 'down') return 'Down'
  if (value === 'off') return 'Idle'
  return 'Unknown'
}

function feedTone(value: SystemHealth['feed'] | undefined): string {
  if (value === 'live') return 'ok'
  if (value === 'stale') return 'warn'
  if (value === 'down') return 'bad'
  return 'muted'
}

function maskClientId(clientId: string): string {
  return `******${clientId.slice(-4).padStart(4, '*')}`
}

function statusLabel(status: WsStatus, setupState: SetupState): string {
  if (status !== 'live') return 'reconnecting'
  if (setupState === 'PAUSED') return 'paused'
  return 'listening'
}

function dhanLabel(value: SystemHealth['dhan'] | undefined): string {
  if (value === 'connected') return 'Connected'
  if (value === 'auth_issue') return 'Auth Issue'
  return 'Unknown'
}
