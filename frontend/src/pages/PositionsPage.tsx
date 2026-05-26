import { useEffect, useState } from 'react'
import { getPositions } from '../api/dashboard'
import { OpenPosition } from '../types'

interface FieldProps { label: string; value: React.ReactNode; mono?: boolean; span2?: boolean }

function Field({ label, value, mono, span2 }: FieldProps) {
  return (
    <div className={span2 ? 'col-span-2' : ''}>
      <p className="text-xs mb-0.5" style={{ color: '#77736c' }}>{label}</p>
      <p className={`text-sm ${mono ? 'font-mono' : 'font-medium'}`} style={{ color: '#f4f1ea' }}>
        {value}
      </p>
    </div>
  )
}

export default function PositionsPage() {
  const [position, setPosition] = useState<OpenPosition | null>(null)
  const [loading, setLoading] = useState(true)

  const loadData = () => {
    getPositions()
      .then((response) => setPosition(response.data))
      .finally(() => setLoading(false))
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
        <p className="mt-1 text-sm" style={{ color: '#9a968f' }}>
          Current open position tracked by the signal engine.
        </p>
      </div>

      <div className="card">
        {loading ? (
          <p className="text-sm" style={{ color: '#77736c' }}>Loading position…</p>
        ) : !position?.has_open_position ? (
          <div className="flex flex-col items-center justify-center py-10 gap-2">
            <p className="text-sm font-medium" style={{ color: '#9a968f' }}>No open position</p>
            <p className="text-xs" style={{ color: '#77736c' }}>A position will appear here once an entry order is executed.</p>
            {position?.broker_sync?.message && (
              <p className="max-w-md text-center text-xs" style={{ color: '#98e94d' }}>{position.broker_sync.message}</p>
            )}
          </div>
        ) : (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-x-8 gap-y-5">
              <Field label="Strategy" value={position.strategy_code} />
              <Field label="Trading Symbol" value={position.trading_symbol} mono />
              <Field label="Security ID" value={position.security_id} mono />
              <Field label="Qty" value={position.qty} />
              <Field label="Entry Order ID" value={position.entry_order_id} mono />
              <Field
                label="Entry Price"
                value={position.entry_price == null ? '—' : `₹${position.entry_price.toFixed(2)}`}
              />
              <Field
                label="Opened At"
                span2
                value={position.opened_at ? new Date(position.opened_at).toLocaleString() : '—'}
              />
            </div>

            {/* Small Order Note */}
            <div
              className="p-3.5 rounded-lg border text-xs"
              style={{
                borderColor: 'rgba(245,158,11,0.2)',
                background: 'rgba(245,158,11,0.04)',
                color: '#fde68a'
              }}
            >
              <p className="font-semibold mb-1 uppercase tracking-wider">Active Position Note</p>
              <p style={{ color: '#bcb5aa' }}>
                This is a live active position. The signal engine is monitoring incoming exit alerts to automatically liquidate this contract. Any manual Panic Exit trigger from the Controls panel will force an immediate market sell order.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
