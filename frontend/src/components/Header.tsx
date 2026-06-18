import { BarChart3, FlaskConical, LineChart, LogOut, RotateCcw, ShieldAlert, X, Zap } from 'lucide-react'
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

        <div className="header-actions">
          <div className={`mode-badge mode-badge-${engineMode ?? 'unset'}`}>
            {engineMode === 'paper' ? <FlaskConical size={13} /> : engineMode === 'live' ? <Zap size={13} /> : null}
            <span className="status-dot" />
            {modeBadgeText(engineMode)}{engineLive ? ` - ${statusLabel(status, setupState)}` : ''}
          </div>
          {engineLive ? <HealthStrip setupState={setupState} health={health} /> : null}
          {clientId ? (
            <div className="client-badge">CLIENT: {maskClientId(clientId)}</div>
          ) : null}
          <div className="user-badge" title={user.email}>
            {user.picture_url ? <img src={user.picture_url} alt="" referrerPolicy="no-referrer" /> : null}
            <span>{user.name || user.email}</span>
          </div>
          {engineLive ? (
            <>
              <button className="kill-button" type="button" onClick={() => setKillDialogOpen(true)}>
                <ShieldAlert size={14} />
                Stop & Square Off
              </button>
              <button className="secondary-button" type="button" onClick={onReconfigure}>
                <RotateCcw size={14} />
                Re-Configure
              </button>
            </>
          ) : null}
          <button className="icon-button" type="button" aria-label="Log out" title="Log out" onClick={onLogout}>
            <LogOut size={15} />
          </button>
        </div>
      </header>

      {killDialogOpen ? (
        <div className="modal-backdrop" role="presentation" onPointerDown={(event) => {
          if (event.currentTarget === event.target) closeDialog()
        }}>
          <section className="kill-dialog" role="dialog" aria-modal="true" aria-labelledby="kill-dialog-title">
            <button className="icon-button" type="button" aria-label="Close stop and square-off confirmation" onClick={closeDialog}>
              <X size={16} />
            </button>
            <ShieldAlert size={24} />
            <h2 id="kill-dialog-title">Stop routing and square off?</h2>
            <p>This stops new entries and exits NOVA's tracked open position.</p>
            <button
              className="hold-confirm"
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
            <button className="secondary-button" type="button" onClick={closeDialog}>Cancel</button>
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
