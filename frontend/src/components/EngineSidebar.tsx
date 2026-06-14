import { Ban, CheckCircle2, LogOut, Play } from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { ActiveTradeCard } from './ActiveTradeCard'
import { ConfirmationDialog } from './ConfirmationDialog'
import { TickingNumber } from './TickingNumber'
import { formatCurrency } from '../lib/format'
import type { ActiveTrade, ClientCommand, EngineMode, SafetyStatus, SessionBootstrap, SetupState, SideFilter } from '../types'

interface Props {
  session: SessionBootstrap | null
  state: SetupState
  wallet: number | null
  marginUtilized: number | null
  realizedPnl: number
  activeTrade: ActiveTrade | null
  lotSize: number
  side: SideFilter
  engineMode: EngineMode | null
  safetyStatus: SafetyStatus | null
  pendingActions: ReadonlySet<string>
  connected: boolean
  actionError?: string
  onSend: (command: ClientCommand) => boolean
}

export function EngineSidebar({ session, state, wallet, marginUtilized, realizedPnl, activeTrade, lotSize, side, engineMode, safetyStatus, pendingActions, connected, actionError, onSend }: Props) {
  const [exitDialogOpen, setExitDialogOpen] = useState(false)
  const paper = engineMode === 'paper'
  const entriesBlocked = state === 'PAUSED'
  const routePending = pendingActions.has('session.pause') || pendingActions.has('session.resume')
  const exitPending = pendingActions.has('session.exit_open')
  const riskPending = pendingActions.has('session.patch_risk')
  return (
    <aside className="engine-sidebar" aria-label={`${paper ? 'Paper' : 'Live'} account data`}>
      <section className="sidebar-card account-card">
        <span className={`sidebar-mode-chip ${engineMode ?? 'unset'}`}>{paper ? 'paper' : 'live'}</span>
        <MetricRow label={paper ? 'Virtual Balance' : 'Margin Available'}>{wallet === null ? 'Pending' : <TickingNumber value={wallet} decimals={2} />}</MetricRow>
        <MetricRow label={paper ? 'Margin Utilized (sim)' : 'Margin Utilized'}>{marginUtilized === null ? 'Pending' : formatCurrency(marginUtilized)}</MetricRow>
        <MetricRow label={paper ? 'Paper P&L' : 'Realized Session P&L'}><TickingNumber value={realizedPnl} decimals={0} signed /></MetricRow>
      </section>

      <section className="sidebar-card position-card">
        <div className="sidebar-title">
          <span>Current Active Position</span>
        </div>
        {activeTrade ? (
          <ActiveTradeCard
            trade={{ ...activeTrade, mode: engineMode ?? undefined }}
            lotSize={lotSize}
            compact
            actionPending={pendingActions.has('session.apply_sr_suggestion')}
            onApplySrSuggestion={() => { onSend({ type: 'session.apply_sr_suggestion', data: {} }) }}
          />
        ) : (
          <div className="empty-position">
            <CheckCircle2 size={20} />
            <span>{paper ? 'No active paper trades yet.' : 'No active broker position is tracked.'}</span>
          </div>
        )}
      </section>

      <section className="sidebar-card route-card">
        <div className="sidebar-title">
          <span>Routing Engine Controls</span>
        </div>
        <button
          type="button"
          className={`engine-toggle entry-block-toggle ${entriesBlocked ? 'blocked' : ''}`}
          aria-pressed={entriesBlocked}
          disabled={!connected || routePending}
          onClick={() => onSend({ type: entriesBlocked ? 'session.resume' : 'session.pause', data: {} })}
        >
          {entriesBlocked ? <Play size={14} /> : <Ban size={14} />}
          {routePending ? 'Working...' : entriesBlocked ? 'Allow Entry Requests' : 'Block Entry Requests'}
        </button>
        <button
          type="button"
          className="exit-open-button"
          disabled={!activeTrade || !connected || exitPending}
          onClick={() => setExitDialogOpen(true)}
        >
          <LogOut size={14} />
          {exitPending ? 'Exiting...' : 'Exit Open Position'}
        </button>
        <div className="side-filter-control">
          <span>Automated entry side</span>
          <div className="side-filter-segmented" role="group" aria-label="Automated entry side">
            {(['CE', 'PE', 'BOTH'] as SideFilter[]).map((option) => (
              <button
                key={option}
                type="button"
                className={side === option ? 'selected' : ''}
                aria-pressed={side === option}
                disabled={!connected || riskPending}
                onClick={() => onSend({ type: 'session.patch_risk', data: { side: option } })}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
        <CopyField label="TradingView Webhook URL" value={safetyStatus?.webhook.url ?? session?.webhookUrl ?? ''} />
        <StatusField label="Webhook Secret" value={safetyStatus?.webhook.secret_masked ?? 'Configured - value hidden'} />
        <StatusField label="Webhook Signing" value={safetyStatus?.signing_relay_configured ? 'Signing relay ready' : 'Signing relay not configured'} warning={!safetyStatus?.signing_relay_configured} />
      </section>
      <ConfirmationDialog
        open={exitDialogOpen}
        title="Exit the tracked open position?"
        consequence="NOVA will send an exit request for the currently tracked position."
        confirmLabel="Exit Open Position"
        confirmPhrase="PANIC EXIT"
        mode={engineMode}
        affectsRealOrders={engineMode === 'live'}
        pending={exitPending}
        error={actionError}
        onClose={() => setExitDialogOpen(false)}
        onConfirm={() => { onSend({ type: 'session.exit_open', data: {} }) }}
      />
    </aside>
  )
}

function MetricRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="metric-row">
      <span>{label}</span>
      <strong>{children}</strong>
    </div>
  )
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    if (!value) return
    await navigator.clipboard?.writeText(value)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }
  return (
    <label className="copy-field">
      <span>
        {label}
        <button type="button" aria-label={`Copy ${label}`} disabled={!value || copied} onClick={() => void copy()}>{copied ? 'Copied' : 'Copy'}</button>
      </span>
      <div className="copy-field-row">
        <code>{value || 'Unavailable'}</code>
      </div>
    </label>
  )
}

function StatusField({ label, value, warning = false }: { label: string; value: string; warning?: boolean }) {
  return (
    <div className={warning ? 'status-field warning' : 'status-field'}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
