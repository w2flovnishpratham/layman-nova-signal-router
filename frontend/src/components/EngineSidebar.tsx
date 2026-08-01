import { Button } from '@/components/ui/button'
import { Ban, CheckCircle2, LogOut, Play } from 'lucide-react'
import type { ReactNode } from 'react'
import { ActiveTradeCard } from './ActiveTradeCard'
import { TickingNumber } from './TickingNumber'
import { formatCurrency } from '../lib/format'
import type { RuntimeStatus } from '../api'
import type { ActiveTrade, ClientCommand, EngineMode, MarketSnapshot, SetupState, SideFilter } from '../types'
import { MarketBiasCard } from '../trading/MarketBiasCard'
import { RiskAutomationCard } from '../trading/RiskAutomationCard'

interface Props {
  state: SetupState
  wallet: number | null
  marginUtilized: number | null
  realizedPnl: number
  activeTrade: ActiveTrade | null
  lotSize: number
  side: SideFilter
  engineMode: EngineMode | null
  runtime: RuntimeStatus | null
  marketSnapshot?: MarketSnapshot | null
  onSend: (command: ClientCommand) => void
  section?: 'all' | 'account' | 'risk'
}

export function EngineSidebar({
  state,
  wallet,
  marginUtilized,
  realizedPnl,
  activeTrade,
  lotSize,
  side,
  engineMode,
  runtime,
  marketSnapshot,
  onSend,
  section = 'all',
}: Props) {
  const paper = engineMode === 'paper'
  const entriesBlocked = state === 'PAUSED'
  return (
    <div className="engine-sidebar flex flex-col gap-4" aria-label={`${paper ? 'Paper' : 'Live'} account data`}>
      {section !== 'risk' && (
        <section className="sidebar-card position-card">
          <div className="sidebar-title">
            <span>Active Position</span>
          </div>
          {activeTrade ? (
            <ActiveTradeCard
              trade={{ ...activeTrade, mode: engineMode ?? undefined }}
              lotSize={lotSize}
              compact
              onApplySrSuggestion={() => onSend({ type: 'session.apply_sr_suggestion', data: {} })}
            />
          ) : (
            <div className="empty-position">
              <CheckCircle2 size={20} />
              <span>No active options positions (Flat)</span>
            </div>
          )}
        </section>
      )}

      {section !== 'risk' && (
        <section className="sidebar-card pnl-overview-card">
          <div className="sidebar-title"><span>P&amp;L Overview</span></div>
          <div className="pnl-overview-grid">
            <MetricRow label="Session"><TickingNumber value={runtime?.pnl.session ?? realizedPnl} decimals={2} signed /></MetricRow>
            <MetricRow label="Realized"><TickingNumber value={runtime?.pnl.realized ?? realizedPnl} decimals={2} signed /></MetricRow>
            <MetricRow label="Unrealized"><TickingNumber value={runtime?.pnl.unrealized ?? 0} decimals={2} signed /></MetricRow>
          </div>
        </section>
      )}

      {section !== 'account' && (
        <>
          <MarketBiasCard />
          <RiskAutomationCard runtime={runtime} />

          {section === 'all' && (
          <section className="sidebar-card route-card">
            <div className="sidebar-title">
              <span>Routing Engine Controls</span>
            </div>
            <Button variant="unstyled"
              type="button"
              className={`engine-toggle entry-block-toggle ${entriesBlocked ? 'blocked' : ''}`}
              aria-pressed={entriesBlocked}
              onClick={() => onSend({ type: entriesBlocked ? 'session.resume' : 'session.pause', data: {} })}
            >
              {entriesBlocked ? <Play size={14} /> : <Ban size={14} />}
              {entriesBlocked ? 'Allow Entry Requests' : 'Block Entry Requests'}
            </Button>
            <Button variant="unstyled"
              type="button"
              className="exit-open-button"
              disabled={!activeTrade}
              onClick={() => onSend({ type: 'session.exit_open', data: {} })}
            >
              <LogOut size={14} />
              Exit Open Position
            </Button>
            <div className="side-filter-control">
              <span>Automated entry side</span>
              <div className="side-filter-segmented" role="group" aria-label="Automated entry side">
                {(['CE', 'PE', 'BOTH'] as SideFilter[]).map((option) => (
                  <Button variant="unstyled"
                    key={option}
                    type="button"
                    className={side === option ? 'selected' : ''}
                    aria-pressed={side === option}
                    onClick={() => onSend({ type: 'session.patch_risk', data: { side: option } })}
                  >
                    {option}
                  </Button>
                ))}
              </div>
            </div>
          </section>
          )}
        </>
      )}

      {section !== 'risk' && (
        <section className="sidebar-card account-card">
          <div className="sidebar-title">
            <span>Account ({paper ? 'Paper' : 'Live'})</span>
            <span className={`sidebar-mode-chip ${engineMode ?? 'unset'}`}>{paper ? 'paper' : 'live'}</span>
          </div>
          <MetricRow label={paper ? 'Virtual Balance' : 'Margin Available'}>{wallet === null ? 'Pending' : <TickingNumber value={wallet} decimals={2} />}</MetricRow>
          <MetricRow label={paper ? 'Margin Utilized (sim)' : 'Margin Utilized'}>{marginUtilized === null ? 'Pending' : formatCurrency(marginUtilized)}</MetricRow>
          <MetricRow label={paper ? 'Paper P&L' : 'Realized Session P&L'}><TickingNumber value={realizedPnl} decimals={0} signed /></MetricRow>
          <MetricRow label="ATM Strike">{marketSnapshot?.atm?.atmStrike?.toLocaleString('en-IN') ?? 'Pending'}</MetricRow>
          <MetricRow label="ATM CE / PE LTP">{formatAtmPair(marketSnapshot)}</MetricRow>
        </section>
      )}
    </div>
  )
}

function formatAtmPair(snapshot?: MarketSnapshot | null): string {
  const ce = snapshot?.atm?.options?.CE?.ltp
  const pe = snapshot?.atm?.options?.PE?.ltp
  if (ce == null && pe == null) return 'Pending'
  const format = (value?: number | null) => value == null ? '—' : value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return `${format(ce)} / ${format(pe)}`
}

function MetricRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="metric-row">
      <span>{label}</span>
      <strong>{children}</strong>
    </div>
  )
}
