import { Activity, Clock, TrendingDown, TrendingUp } from 'lucide-react'
import type { ReactNode } from 'react'
import { formatCurrency } from '../lib/format'
import type { MarketSnapshot } from '../types'

export function MarketCard({ snapshot, collapseControl }: { snapshot: MarketSnapshot | null; collapseControl?: ReactNode }) {
  const change = snapshot?.dayChangePct ?? null
  const atm = snapshot?.atm ?? null
  const ce = atm?.options?.CE
  const pe = atm?.options?.PE
  return (
    <section className="sidebar-card market-card">
      <div className="sidebar-title market-panel-header">
        <div className="market-title-group">
          <span>NIFTY Market</span>
          <span className={`market-status ${snapshot?.marketStatus === 'open' ? 'open' : 'closed'}`}>
            {snapshot?.marketStatus === 'open' ? 'Open' : 'Closed'}
          </span>
        </div>
        {collapseControl ? <div className="panel-header-action">{collapseControl}</div> : null}
      </div>
      <div className="market-main-row">
        <div>
          <span>NIFTY spot</span>
          <strong>{snapshot?.niftySpot === null || snapshot?.niftySpot === undefined ? 'Pending' : formatCurrency(snapshot.niftySpot, { decimals: 2 })}</strong>
        </div>
        <div className={change === null ? '' : change >= 0 ? 'text-up' : 'text-down'}>
          {change === null ? <Activity size={15} /> : change >= 0 ? <TrendingUp size={15} /> : <TrendingDown size={15} />}
          <strong>{change === null ? 'Pending' : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`}</strong>
        </div>
      </div>
      <MiniSparkline values={snapshot?.sparkline ?? []} />
      <div className="atm-strip">
        <div>
          <span>ATM strike</span>
          <strong>{atm?.atmStrike ? atm.atmStrike.toLocaleString() : 'Pending'}</strong>
        </div>
      </div>
      <div className="atm-ltp-grid">
        <AtmCell label="ATM CE" ltp={ce?.ltp} />
        <AtmCell label="ATM PE" ltp={pe?.ltp} />
      </div>
      <div className="market-detail-grid">
        <div><span>Latest signal</span><strong>{signalLabel(snapshot)}</strong></div>
        <div><span>Updated</span><strong><Clock size={12} className="inline mr-1" /> {snapshot?.lastUpdatedAt ? shortTime(snapshot.lastUpdatedAt) : 'Pending'}</strong></div>
      </div>
    </section>
  )
}

function AtmCell({ label, ltp }: { label: string; ltp?: number | null }) {
  return (
    <div className="atm-ltp-cell">
      <span>{label}</span>
      <strong>{ltp === null || ltp === undefined ? 'Pending' : formatCurrency(ltp, { decimals: 2 })}</strong>
    </div>
  )
}

function MiniSparkline({ values }: { values: number[] }) {
  if (values.length < 2) {
    return <div className="mini-sparkline empty"><span>No tick history</span></div>
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const spread = Math.max(max - min, 1)
  return (
    <div className="mini-sparkline" aria-label="Mini price chart">
      {values.map((value, index) => (
        <span
          key={`${value}-${index}`}
          style={{ height: `${18 + ((value - min) / spread) * 34}px` }}
        />
      ))}
    </div>
  )
}

function signalLabel(snapshot: MarketSnapshot | null): string {
  const signal = snapshot?.latestSignal
  if (!signal?.action) return 'None'
  return [signal.action, signal.optionSide].filter(Boolean).join(' ')
}

function shortTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'Pending'
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
