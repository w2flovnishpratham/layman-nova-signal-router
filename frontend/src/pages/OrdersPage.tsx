import { useEffect, useState } from 'react'
import { getOrders } from '../api/dashboard'
import { OrderEvent } from '../types'

const badge = (order: OrderEvent) => {
  if (order.blocked) return 'badge-red'
  if (order.success) return 'badge-green'
  if (order.phase === 'before_request') return 'badge-yellow'
  return 'badge-gray'
}

export default function OrdersPage() {
  const [orders, setOrders] = useState<OrderEvent[]>([])
  const [loading, setLoading] = useState(true)

  const loadData = () => {
    getOrders().then(r => setOrders(r.data)).finally(() => setLoading(false))
  }
  useEffect(() => {
    loadData()
    const id = window.setInterval(loadData, 5000)
    return () => window.clearInterval(id)
  }, [])

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold" style={{ color: 'var(--c-text-1)' }}>Orders</h1>
        <p className="mt-1 text-sm" style={{ color: 'var(--c-text-3)' }}>All order events routed through the signal engine.</p>
      </div>

      <div className="card overflow-x-auto">
        {loading ? (
          <p className="text-sm" style={{ color: 'var(--c-text-4)' }}>Loading orders…</p>
        ) : orders.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--c-text-4)' }}>No order events yet.</p>
        ) : (
          <table className="w-full text-sm text-left">
            <thead>
              <tr style={{ borderBottom: '1px solid var(--c-table-border)' }}>
                {['Time','Format','Phase','Action','Side','Mode','Symbol','Option','Qty','Status','Order ID','Reason'].map(h => (
                  <th key={h} className="pb-2 pr-4 text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--c-table-head)' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {orders.map(order => (
                <tr
                  key={order.id}
                  className="align-top"
                  style={{ borderBottom: '1px solid var(--c-table-border)' }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--c-table-hover)')}
                  onMouseLeave={e => (e.currentTarget.style.background = '')}
                >
                  <td className="py-2 pr-4 text-xs whitespace-nowrap font-mono" style={{ color: 'var(--c-text-4)' }}>
                    {order.created_at ? new Date(order.created_at).toLocaleString() : '-'}
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs" style={{ color: 'var(--c-text-3)' }}>{order.payload_format || '-'}</td>
                  <td className="py-2 pr-4 font-mono text-xs" style={{ color: 'var(--c-text-3)' }}>{order.phase || '-'}</td>
                  <td className="py-2 pr-4" style={{ color: 'var(--c-text-1)' }}>{order.normalized_action || order.action || '-'}</td>
                  <td className="py-2 pr-4" style={{ color: 'var(--c-text-1)' }}>{order.normalized_side || order.side || '-'}</td>
                  <td className="py-2 pr-4">
                    <span className={order.dhan_mode === 'REAL' ? 'badge-red-solid' : 'badge-green-solid'}>
                      {order.dhan_mode || '-'}
                    </span>
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs" style={{ color: 'var(--c-text-2)' }}>
                    {order.trading_symbol || order.normalized_symbol || order.security_id || '-'}
                  </td>
                  <td className="py-2 pr-4 text-xs" style={{ color: 'var(--c-text-3)' }}>
                    {order.normalized_expiry || '-'} {order.normalized_strike ?? ''} {order.normalized_option_side || ''}
                  </td>
                  <td className="py-2 pr-4" style={{ color: 'var(--c-text-1)' }}>{order.normalized_qty ?? order.qty ?? '-'}</td>
                  <td className="py-2 pr-4">
                    <span className={badge(order)}>{order.status || (order.blocked ? 'BLOCKED' : order.phase)}</span>
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs" style={{ color: 'var(--c-text-4)' }}>{order.order_id || '-'}</td>
                  <td className="py-2 text-xs max-w-sm" style={{ color: 'var(--c-text-3)' }}>{order.reason || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
