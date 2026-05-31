import { useState } from 'react'
import { getPositions } from '../api/dashboard'
import { usePolling } from '../hooks/usePolling'
import { ExternalPositionsSnapshot, OpenPosition } from '../types'

function Field({ label, value, mono, span2 }: { label: string; value: React.ReactNode; mono?: boolean; span2?: boolean }) {
  return (
    <div className={span2 ? 'col-span-2' : ''}>
      <p className="text-xs mb-0.5" style={{ color: '#77736c' }}>{label}</p>
      <p className={`text-sm ${mono ? 'font-mono' : 'font-medium'}`} style={{ color: '#f4f1ea' }}>{value}</p>
    </div>
  )
}

function currency(value: number | null | undefined) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `Rs.${Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
}

function BrokerOnlyPositions({ snapshot }: { snapshot?: ExternalPositionsSnapshot }) {
  const rows = [
    ...(snapshot?.positions ?? []).map(item => ({
      kind: 'Position',
      symbol: item.trading_symbol ?? item.security_id ?? '-',
      qty: item.net_qty ?? '-',
      status: item.position_type ?? 'OPEN',
      checkedAt: item.detected_at,
    })),
    ...(snapshot?.open_orders ?? []).map(item => ({
      kind: 'Open order',
      symbol: item.trading_symbol ?? item.security_id ?? '-',
      qty: item.remaining_quantity ?? '-',
      status: item.order_status ?? 'OPEN',
      checkedAt: item.detected_at,
    })),
  ]

  if (!rows.length) return null

  return (
    <div className="card mb-4 space-y-3" style={{ borderColor: 'rgba(239,68,68,0.45)', background: 'rgba(239,68,68,0.05)' }}>
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-widest text-red-300">Broker-only Dhan exposure</h2>
        <p className="mt-1 text-xs text-red-200/80">
          These rows exist at Dhan but are not NOVA's tracked open position.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(239,68,68,0.25)' }}>
              {['Type', 'Symbol', 'Qty', 'Status', 'Checked'].map(h => (
                <th key={h} className="pb-2 pr-4 text-xs font-semibold uppercase tracking-wider text-red-300/80">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.kind}-${row.symbol}-${index}`} style={{ borderBottom: '1px solid rgba(239,68,68,0.12)' }}>
                <td className="py-2 pr-4 text-xs text-red-200">{row.kind}</td>
                <td className="py-2 pr-4 font-mono text-xs text-red-100">{row.symbol}</td>
                <td className="py-2 pr-4 text-red-100">{String(row.qty)}</td>
                <td className="py-2 pr-4 text-xs text-red-200">{row.status}</td>
                <td className="py-2 pr-4 text-xs text-red-200/70">
                  {row.checkedAt ? new Date(row.checkedAt).toLocaleString() : '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function SlTpDriftPanel({ snapshot }: { snapshot?: ExternalPositionsSnapshot }) {
  const drift = snapshot?.sl_tp_drift
  if (!drift?.drift_detected) return null

  return (
    <div className="card mb-4 space-y-3" style={{ borderColor: 'rgba(245,158,11,0.45)', background: 'rgba(245,158,11,0.06)' }}>
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-widest text-amber-200">Broker SL/TP drift</h2>
        <p className="mt-1 text-xs text-amber-100/75">{drift.message}</p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {(drift.items ?? []).map(item => (
          <div key={item.leg ?? item.leg_name ?? 'leg'} className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <span className="text-[10px] font-bold uppercase tracking-wide text-amber-200">{(item.leg ?? 'leg').replace('_', ' ')}</span>
              <span className={item.drift ? 'badge-yellow' : 'badge-green'}>{item.drift ? 'DRIFT' : 'OK'}</span>
            </div>
            <p className="mt-1 text-xs text-amber-100/80">
              Expected {currency(item.expected_price)} · Dhan {currency(item.actual_price)}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function PositionsPage() {
  const [position, setPosition] = useState<OpenPosition | null>(null)
  const [loading, setLoading] = useState(true)

  const loadData = () => {
    getPositions()
      .then(r => setPosition(r.data))
      .catch((err) => console.warn('PositionsPage: failed to refresh:', err?.message ?? err))
      .finally(() => setLoading(false))
  }
  usePolling(loadData, 5000)

  return (
    <div className="w-full">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold" style={{ color: '#f4f1ea' }}>Position</h1>
        <p className="mt-1 text-sm" style={{ color: '#9a968f' }}>Current open position tracked by the signal engine.</p>
      </div>
      <BrokerOnlyPositions snapshot={position?.external_positions} />
      <SlTpDriftPanel snapshot={position?.external_positions} />
      <div className="card">
        {loading ? (
          <p className="text-sm" style={{ color: '#77736c' }}>Loading position...</p>
        ) : !position?.has_open_position ? (
          <div className="flex flex-col items-center justify-center py-10 gap-2">
            <p className="text-sm font-medium" style={{ color: '#9a968f' }}>No open position</p>
            <p className="text-xs" style={{ color: '#77736c' }}>A position will appear here once an entry order is executed.</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-x-8 gap-y-5">
            <Field label="Strategy" value={position.strategy_code} />
            <Field label="Trading Symbol" value={position.trading_symbol} mono />
            <Field label="Security ID" value={position.security_id} mono />
            <Field label="Qty" value={position.qty} />
            <Field label="Entry Order ID" value={position.entry_order_id} mono />
            <Field label="Entry Price" value={currency(position.entry_price)} />
            <Field label="Option LTP" value={currency(position.live_pnl?.ltp)} />
            <Field label="SL Price" value={currency(position.live_pnl?.sl_price)} />
            <Field label="TP Price" value={currency(position.live_pnl?.tp_price)} />
            <Field label="Unrealized PnL" value={currency(position.live_pnl?.unrealized_pnl)} />
            <Field label="PnL %" value={position.live_pnl?.pnl_percent == null ? '-' : `${position.live_pnl.pnl_percent.toFixed(2)}%`} />
            <Field label="Monitor Status" value={position.live_pnl?.status ?? '-'} />
            <Field label="LTP Source" value={position.live_pnl?.source ?? '-'} />
            <Field label="Quote Age" value={position.live_pnl?.quote_age_seconds == null ? '-' : `${position.live_pnl.quote_age_seconds}s`} />
            <Field label="Opened At" span2 value={position.opened_at ? new Date(position.opened_at).toLocaleString() : '-'} />
            <Field label="Last LTP Check" span2 value={position.live_pnl?.last_checked_at ? new Date(position.live_pnl.last_checked_at).toLocaleString() : '-'} />
          </div>
        )}
      </div>
    </div>
  )
}
