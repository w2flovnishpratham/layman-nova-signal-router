import { useEffect, useState } from 'react'
import { getOrders } from '../api/dashboard'
import { OrderEvent } from '../types'

const badge = (order: OrderEvent) => {
  if (order.blocked) return 'badge-red'
  if (order.success) return 'badge-green'
  if (order.phase === 'before_request') return 'badge-yellow'
  return 'badge-gray'
}

const COLUMNS = [
  { label: 'Time', class: '' },
  { label: 'Format', class: 'hidden md:table-cell' },
  { label: 'Phase', class: 'hidden md:table-cell' },
  { label: 'Action', class: '' },
  { label: 'Side', class: 'hidden md:table-cell' },
  { label: 'Mode', class: 'hidden sm:table-cell' },
  { label: 'Symbol', class: '' },
  { label: 'Option', class: 'hidden sm:table-cell' },
  { label: 'Qty', class: '' },
  { label: 'Status', class: '' },
  { label: 'Order ID', class: 'hidden lg:table-cell' },
  { label: 'Reason', class: '' },
]

export default function OrdersPage() {
  const [orders, setOrders] = useState<OrderEvent[]>([])
  const [loading, setLoading] = useState(true)

  const loadData = () => {
    getOrders()
      .then((response) => setOrders(response.data))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadData()
    const id = window.setInterval(loadData, 5000)
    return () => window.clearInterval(id)
  }, [])

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold" style={{ color: '#f4f1ea' }}>Orders</h1>
        <p className="mt-1 text-sm" style={{ color: '#9a968f' }}>
          All order events routed through the signal engine.
        </p>
      </div>

      <div className="card">
        {loading ? (
          <p className="text-sm" style={{ color: '#77736c' }}>Loading orders…</p>
        ) : orders.length === 0 ? (
          <p className="text-sm" style={{ color: '#77736c' }}>No order events yet.</p>
        ) : (
          <>
            {/* Mobile Stacked List View */}
            <div className="block sm:hidden space-y-3">
              {orders.map((order) => (
                <div 
                  key={order.id} 
                  className="rounded-xl p-3.5 space-y-2.5 border border-[#1d1c19] bg-[#0c0c0b]"
                >
                  {/* Header: Action + Mode + Status + Time */}
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="badge-blue text-[10px] uppercase font-bold">{order.normalized_action || order.action || '—'}</span>
                      <span className={order.dhan_mode === 'REAL' ? 'badge-red-solid text-[9px] px-1.5 py-0.5' : 'badge-green-solid text-[9px] px-1.5 py-0.5'}>
                        {order.dhan_mode || '-'}
                      </span>
                      <span className="text-[10px] text-[#77736c] font-mono">
                        {order.created_at ? new Date(order.created_at).toLocaleTimeString() : '-'}
                      </span>
                    </div>
                    <span className={badge(order)}>{order.status || (order.blocked ? 'BLOCKED' : order.phase)}</span>
                  </div>

                  {/* Symbol + Option + Qty */}
                  <div className="space-y-1">
                    <div className="flex items-center justify-between gap-4">
                      <span className="font-mono text-xs font-bold text-[#f4f1ea] break-words">
                        {order.trading_symbol || order.normalized_symbol || order.security_id || '-'}
                      </span>
                      <span className="text-xs font-semibold text-[#d8d3c8] whitespace-nowrap">
                        Qty: {order.normalized_qty ?? order.qty ?? '-'}
                      </span>
                    </div>
                    {(order.normalized_expiry || order.normalized_strike || order.normalized_option_side) && (
                      <p className="text-[10px]" style={{ color: '#9a968f' }}>
                        {order.normalized_expiry || '-'} · {order.normalized_strike ?? ''} · {order.normalized_option_side || ''}
                      </p>
                    )}
                  </div>

                  {/* Extra metadata: Format & Phase */}
                  <div className="flex items-center gap-4 text-[10px] font-mono" style={{ color: '#77736c' }}>
                    <div>Format: <span style={{ color: '#bcb5aa' }}>{order.payload_format || '-'}</span></div>
                    <div>Phase: <span style={{ color: '#bcb5aa' }}>{order.phase || '-'}</span></div>
                  </div>

                  {/* Order ID */}
                  {order.order_id && (
                    <div className="text-[10px] font-mono text-[#77736c]">
                      Order ID: <span className="select-all text-[#bcb5aa]">{order.order_id}</span>
                    </div>
                  )}

                  {/* Reason */}
                  {order.reason && (
                    <div 
                      className="rounded-lg p-2.5 text-[11px] leading-relaxed" 
                      style={{ 
                        background: order.blocked ? 'rgba(239, 68, 68, 0.04)' : 'rgba(245, 158, 11, 0.04)', 
                        border: order.blocked ? '1px solid rgba(239, 68, 68, 0.15)' : '1px solid rgba(245, 158, 11, 0.15)', 
                        color: order.blocked ? '#f87171' : '#fbbf24' 
                      }}
                    >
                      <span className="font-bold uppercase tracking-wider text-[9px] mr-1 block">Details / Reason:</span>
                      {order.reason}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* Desktop Table View */}
            <div className="hidden sm:block overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead>
                  <tr style={{ borderBottom: '1px solid #24231f' }}>
                    {COLUMNS.map(col => (
                      <th key={col.label} className={`pb-2 pr-4 text-xs font-semibold uppercase tracking-wider ${col.class}`} style={{ color: '#77736c' }}>
                        {col.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {orders.map((order) => (
                    <tr
                      key={order.id}
                      className="align-top"
                      style={{ borderBottom: '1px solid #1d1c19' }}
                      onMouseEnter={e => (e.currentTarget.style.background = '#151513')}
                      onMouseLeave={e => (e.currentTarget.style.background = '')}
                    >
                      <td className="py-2 pr-4 text-xs whitespace-nowrap font-mono" style={{ color: '#77736c' }}>
                        {order.created_at ? new Date(order.created_at).toLocaleString() : '-'}
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs hidden md:table-cell" style={{ color: '#bcb5aa' }}>{order.payload_format || '-'}</td>
                      <td className="py-2 pr-4 font-mono text-xs hidden md:table-cell" style={{ color: '#bcb5aa' }}>{order.phase || '-'}</td>
                      <td className="py-2 pr-4" style={{ color: '#f4f1ea' }}>{order.normalized_action || order.action || '-'}</td>
                      <td className="py-2 pr-4 hidden md:table-cell" style={{ color: '#f4f1ea' }}>{order.normalized_side || order.side || '-'}</td>
                      <td className="py-2 pr-4 hidden sm:table-cell">
                        <span className={order.dhan_mode === 'REAL' ? 'badge-red-solid' : 'badge-green-solid'}>
                          {order.dhan_mode || '-'}
                        </span>
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs" style={{ color: '#d3cec5' }}>
                        {order.trading_symbol || order.normalized_symbol || order.security_id || '-'}
                      </td>
                      <td className="py-2 pr-4 text-xs hidden sm:table-cell" style={{ color: '#9a968f' }}>
                        {order.normalized_expiry || '-'} {order.normalized_strike ?? ''} {order.normalized_option_side || ''}
                      </td>
                      <td className="py-2 pr-4" style={{ color: '#f4f1ea' }}>{order.normalized_qty ?? order.qty ?? '-'}</td>
                      <td className="py-2 pr-4">
                        <span className={badge(order)}>{order.status || (order.blocked ? 'BLOCKED' : order.phase)}</span>
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs hidden lg:table-cell" style={{ color: '#77736c' }}>{order.order_id || '-'}</td>
                      <td className="py-2 text-xs max-w-sm" style={{ color: '#9a968f' }}>{order.reason || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
