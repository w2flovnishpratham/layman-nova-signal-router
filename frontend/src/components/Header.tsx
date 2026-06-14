import { FlaskConical, LogOut, RotateCcw, ShieldAlert, Zap } from 'lucide-react'
import { useState } from 'react'
import { ConfirmationDialog } from './ConfirmationDialog'
import { modeBadgeText } from '../lib/mode'
import type { EngineMode, SetupState, WsStatus } from '../types'

interface Props {
  status: WsStatus
  clientId?: string
  engineLive: boolean
  engineMode: EngineMode | null
  setupState: SetupState
  userEmail?: string
  onKill: () => void
  onReconfigure: () => void
  onLogout?: () => void
  killPending: boolean
  reconfigurePending: boolean
  logoutPending: boolean
  actionError?: string
}

export function Header({
  status,
  clientId,
  engineLive,
  engineMode,
  setupState,
  userEmail,
  onKill,
  onReconfigure,
  onLogout,
  killPending,
  reconfigurePending,
  logoutPending,
  actionError,
}: Props) {
  const [dialog, setDialog] = useState<'kill' | 'reset' | null>(null)

  return (
    <>
      <header className="app-header">
        <div className="brand-lockup">
          <span className="nova-mark" />
          <strong>NOVA SIGNAL ROUTER</strong>
        </div>

        <div className="header-actions">
          <div className={`mode-badge mode-badge-${engineMode ?? 'unset'}`}>
            {engineMode === 'paper' ? <FlaskConical size={13} /> : engineMode === 'live' ? <Zap size={13} /> : null}
            <span className="status-dot" />
            {modeBadgeText(engineMode)}{engineLive ? ` - ${statusLabel(status, setupState)}` : ''}
          </div>
          {clientId ? (
            <div className="client-badge">CLIENT: {maskClientId(clientId)}</div>
          ) : null}
          {userEmail ? <div className="client-badge">{userEmail}</div> : null}
          {engineLive ? (
            <>
              <button className="kill-button" type="button" disabled={killPending} onClick={() => setDialog('kill')}>
                <ShieldAlert size={14} />
                {killPending ? 'Stopping...' : 'Stop & Square Off'}
              </button>
              <button className="secondary-button" type="button" disabled={reconfigurePending} onClick={() => setDialog('reset')}>
                <RotateCcw size={14} />
                {reconfigurePending ? 'Resetting...' : 'Re-Configure'}
              </button>
            </>
          ) : null}
          {onLogout ? (
            <button className="secondary-button" type="button" onClick={onLogout} disabled={logoutPending}>
              <LogOut size={14} />
              {logoutPending ? 'Signing out...' : 'Logout'}
            </button>
          ) : null}
        </div>
      </header>

      <ConfirmationDialog
        open={dialog === 'kill'}
        title="Stop routing and square off?"
        consequence="This stops new entries and exits NOVA's tracked open position."
        confirmLabel="Stop and Square Off"
        confirmPhrase="PANIC EXIT"
        mode={engineMode}
        affectsRealOrders={engineMode === 'live'}
        pending={killPending}
        error={actionError}
        onClose={() => setDialog(null)}
        onConfirm={onKill}
      />
      <ConfirmationDialog
        open={dialog === 'reset'}
        title="Reset and reconfigure this session?"
        consequence="This stops the current engine session and clears the active setup flow before starting again."
        confirmLabel="Reset Session"
        confirmPhrase="RESET"
        mode={engineMode}
        affectsRealOrders={engineMode === 'live' && engineLive}
        pending={reconfigurePending}
        error={actionError}
        onClose={() => setDialog(null)}
        onConfirm={onReconfigure}
      />
    </>
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
