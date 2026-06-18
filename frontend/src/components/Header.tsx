import { BarChart3, FlaskConical, LineChart, LogOut, MoreVertical, RotateCcw, ShieldAlert, X, Zap } from 'lucide-react'
import { useRef, useState } from 'react'
import type { AuthUser } from '../api'
import { modeBadgeText } from '../lib/mode'
import type { EngineMode, NovaView, SetupState, SystemHealth, WsStatus } from '../types'

interface Props {
  status: WsStatus
  clientId?: string
  engineLive: boolean
  engineMode: EngineMode | null
  setupState: SetupState
  health: SystemHealth | null
  user: AuthUser
  view: NovaView
  onNavigate: (view: NovaView) => void
  onKill: () => void
  onReconfigure: () => void
  onLogout: () => void
}

export function Header({
  status,
  clientId,
  engineLive,
  engineMode,
  setupState,
  health,
  user,
  view,
  onNavigate,
  onKill,
  onReconfigure,
  onLogout,
}: Props) {
  const [killDialogOpen, setKillDialogOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const holdTimer = useRef<number | null>(null)

  function cancelHold() {
    if (holdTimer.current !== null) {
      window.clearTimeout(holdTimer.current)
      holdTimer.current = null
    }
  }

  function startHold() {
    cancelHold()
    holdTimer.current = window.setTimeout(() => {
      holdTimer.current = null
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
          <span className="nova-mark" />
          <strong>NOVA SIGNAL ROUTER</strong>
          <nav className="nv-nav-tabs" aria-label="Primary">
            <button
              type="button"
              className={`nv-nav-tab${view === 'trading' ? ' active' : ''}`}
              aria-current={view === 'trading'}
              onClick={() => onNavigate('trading')}
            >
              <LineChart size={13} />
              Trading
            </button>
            <button
              type="button"
              className={`nv-nav-tab${view === 'dashboard' ? ' active' : ''}`}
              aria-current={view === 'dashboard'}
              onClick={() => onNavigate('dashboard')}
            >
              <BarChart3 size={13} />
              Dashboard
            </button>
          </nav>
        </div>

        <div className="header-actions relative flex items-center gap-3">
          <div className={`mode-badge mode-badge-${engineMode ?? 'unset'}`}>
            {engineMode === 'paper' ? <FlaskConical size={13} /> : engineMode === 'live' ? <Zap size={13} /> : null}
            <span className="status-dot" />
            {modeBadgeText(engineMode)}{engineLive ? ` - ${statusLabel(status, setupState)}` : ''}
          </div>

          <button
            className="icon-button p-1.5 rounded-lg border border-white/10 hover:bg-white/5 transition-colors"
            type="button"
            aria-label="More actions"
            title="More actions"
            onClick={() => setMenuOpen(!menuOpen)}
          >
            <MoreVertical size={18} />
          </button>

          {menuOpen && (
            <>
              <div className="fixed inset-0 z-30 cursor-default" onClick={() => setMenuOpen(false)} />
              <div className="absolute right-0 top-full mt-2 w-72 bg-[#12111b] border border-white/10 rounded-xl shadow-2xl p-4 flex flex-col gap-4 z-40 animate-in fade-in slide-in-from-top-2 duration-150">
                <div className="flex items-center gap-3 pb-3 border-b border-white/5">
                  {user.picture_url ? (
                    <img src={user.picture_url} alt="" referrerPolicy="no-referrer" className="w-10 h-10 rounded-full border border-white/10 object-cover" />
                  ) : null}
                  <div className="flex flex-col min-w-0">
                    <span className="font-semibold text-sm truncate text-white">{user.name || user.email}</span>
                    <span className="text-xs text-white/50 truncate">{user.email}</span>
                  </div>
                </div>

                {clientId && (
                  <div className="text-xs text-white/70 bg-white/5 px-2.5 py-1.5 rounded-lg border border-white/5 flex justify-between items-center">
                    <span className="opacity-70">Client ID:</span>
                    <strong className="font-mono">{maskClientId(clientId)}</strong>
                  </div>
                )}

                {engineLive && (
                  <div className="flex flex-col gap-2 py-1">
                    <span className="text-[10px] uppercase font-bold tracking-wider text-white/40">System Health</span>
                    <HealthStrip setupState={setupState} health={health} />
                  </div>
                )}

                <div className="flex flex-col gap-2 pt-2 border-t border-white/5">
                  {engineLive && (
                    <>
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
                          onReconfigure()
                        }}
                      >
                        <RotateCcw size={14} />
                        Re-Configure
                      </button>
                    </>
                  )}
                  <button
                    className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-white/60 hover:text-white bg-white/5 hover:bg-white/10 border border-white/5 hover:border-white/15 transition-all text-sm font-medium"
                    type="button"
                    onClick={() => {
                      setMenuOpen(false)
                      onLogout()
                    }}
                  >
                    <LogOut size={14} />
                    Log Out
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </header>

      {killDialogOpen ? (
        <div className="modal-backdrop fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200" role="presentation" onPointerDown={(event) => {
          if (event.currentTarget === event.target) closeDialog()
        }}>
          <section className="relative w-full max-w-[380px] bg-[#12101c] border border-red-500/20 rounded-2xl shadow-2xl p-6 flex flex-col items-center text-center gap-4 animate-in zoom-in-95 duration-200" role="dialog" aria-modal="true" aria-labelledby="kill-dialog-title">
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
                Hold to Stop & Square Off
              </button>
              <button
                className="w-full py-2.5 px-4 rounded-xl text-white/50 hover:text-white bg-transparent hover:bg-white/5 transition-all text-xs font-semibold cursor-pointer border-0"
                type="button"
                onClick={closeDialog}
              >
                Cancel
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </>
  )
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
      key: 'engine',
      label: 'Engine',
      value: setupState === 'PAUSED' ? 'Paused' : health?.engine === 'listening' ? 'Listening' : 'Idle',
      tone: setupState === 'PAUSED' ? 'warn' : health?.engine === 'listening' ? 'ok' : 'muted',
    },
  ]

  return (
    <div className="health-strip" aria-label="System health">
      {chips.map((chip) => (
        <span key={chip.key} className={`health-chip ${chip.tone}`} title={`${chip.label}: ${chip.value}`}>
          <span>{chip.label}</span>
          <strong>{chip.value}</strong>
        </span>
      ))}
    </div>
  )
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
