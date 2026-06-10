import { Ban, CheckCircle2, LogOut, Play } from 'lucide-react'
import type { ReactNode } from 'react'
import { ActiveTradeCard } from './ActiveTradeCard'
import { TickingNumber } from './TickingNumber'
import { formatCurrency } from '../lib/format'
import type { ActiveTrade, ClientCommand, EngineMode, SessionBootstrap, SetupState, SideFilter } from '../types'

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
  onSend: (command: ClientCommand) => void
}

export function EngineSidebar({ session, state, wallet, marginUtilized, realizedPnl, activeTrade, lotSize, side, engineMode, onSend }: Props) {
  const paper = engineMode === 'paper'
  const entriesBlocked = state === 'PAUSED'
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
          <ActiveTradeCard trade={{ ...activeTrade, mode: engineMode ?? undefined }} lotSize={lotSize} compact />
        ) : (
          <div className="empty-position">
            <CheckCircle2 size={20} />
            <span>No active options positions (Flat)</span>
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
          onClick={() => onSend({ type: entriesBlocked ? 'session.resume' : 'session.pause', data: {} })}
        >
          {entriesBlocked ? <Play size={14} /> : <Ban size={14} />}
          {entriesBlocked ? 'Allow Entry Requests' : 'Block Entry Requests'}
        </button>
        <button
          type="button"
          className="exit-open-button"
          disabled={!activeTrade}
          onClick={() => onSend({ type: 'session.exit_open', data: {} })}
        >
          <LogOut size={14} />
          Exit Open Position
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
                onClick={() => onSend({ type: 'session.patch_risk', data: { side: option } })}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
        <CopyField label="TradingView Webhook URL" value={session?.webhookUrl ?? 'Starting session'} />
        <CopyField label="Webhook Secret Key" value={session?.webhookSecret ?? 'Starting session'} />
      </section>
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
  return (
    <label className="copy-field">
      <span>
        {label}
        <button type="button" aria-label={`Copy ${label}`} onClick={() => void navigator.clipboard?.writeText(value)}>Copy</button>
      </span>
      <div className="copy-field-row">
        <code>{value}</code>
      </div>
    </label>
  )
}
