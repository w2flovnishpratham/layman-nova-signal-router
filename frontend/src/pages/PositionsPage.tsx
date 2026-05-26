import { useEffect, useState } from 'react'
import { getPositions } from '../api/dashboard'
import { OpenPosition } from '../types'

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

export default function PositionsPage() {
  const [position, setPosition] = useState<OpenPosition | null>(null)
  const [loading, setLoading] = useState(true)

  const loadData = () => {
    getPositions().then(r => setPosition(r.data)).finally(() => setLoading(false))
  }
  useEffect(() => {
    loadData()
    const id = window.setInterval(loadData, 5000)
    return () => window.clearInterval(id)
  }, [])

  return (
    <div className="w-full">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold" style={{ color: '#f4f1ea' }}>Position</h1>
        <p className="mt-1 text-sm" style={{ color: '#9a968f' }}>Current open position tracked by the signal engine.</p>
      </div>
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
            <Field label="Opened At" span2 value={position.opened_at ? new Date(position.opened_at).toLocaleString() : '-'} />
            <Field label="Last LTP Check" span2 value={position.live_pnl?.last_checked_at ? new Date(position.live_pnl.last_checked_at).toLocaleString() : '-'} />
          </div>
        )}
      </div>
    </div>
  )
}
