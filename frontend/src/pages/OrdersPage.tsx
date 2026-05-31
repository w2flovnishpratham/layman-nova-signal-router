import { useState } from 'react'
import { getOrders } from '../api/dashboard'
import { usePolling } from '../hooks/usePolling'
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
    getOrders()
      .then(r => setOrders(r.data))
      .catch((err) => console.warn('OrdersPage: failed to refresh:', err?.message ?? err))
      .finally(() => setLoading(false))
  }
  usePolling(loadData, 5000)

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold" style={{ color: '#f4f1ea' }}>Orders</h1>
        <p className="mt-1 text-sm" style={{ color: '#9a968f' }}>All order events routed through the signal engine.</p>
      </div>

      <div className="card overflow-x-auto">
        {loading ? (
          <p className="text-sm" style={{ color: '#77736c' }}>Loading orders…</p>
        ) : orders.length === 0 ? (
          <p className="text-sm" style={{ color: '#77736c' }}>No order events yet.</p>
        ) : (
          <table className="w-full text-sm text-left">
            <thead>
              <tr style={{ borderBottom: '1px solid #24231f' }}>
                {['Time','Format','Phase','Action','Side','Mode','Symbol','Option','Qty','Status','Order ID','Reason'].map(h => (
                  <th key={h} className="pb-2 pr-4 text-xs font-semibold uppercase tracking-wider" style={{ color: '#77736c' }}>
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
                  style={{ borderBottom: '1px solid #24231f' }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#151513')}
                  onMouseLeave={e => (e.currentTarget.style.background = '')}
                >
                  <td className="py-2 pr-4 text-xs whitespace-nowrap font-mono" style={{ color: '#77736c' }}>
                    {order.created_at ? new Date(order.created_at).toLocaleString() : '-'}
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs" style={{ color: '#9a968f' }}>{order.payload_format || '-'}</td>
                  <td className="py-2 pr-4 font-mono text-xs" style={{ color: '#9a968f' }}>{order.phase || '-'}</td>
                  <td className="py-2 pr-4" style={{ color: '#f4f1ea' }}>{order.normalized_action || order.action || '-'}</td>
                  <td className="py-2 pr-4" style={{ color: '#f4f1ea' }}>{order.normalized_side || order.side || '-'}</td>
                  <td className="py-2 pr-4">
                    <span className={order.dhan_mode === 'REAL' ? 'badge-red-solid' : 'badge-green-solid'}>
                      {order.dhan_mode || '-'}
                    </span>
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs" style={{ color: '#d8d3c8' }}>
                    {order.trading_symbol || order.normalized_symbol || order.security_id || '-'}
                  </td>
                  <td className="py-2 pr-4 text-xs" style={{ color: '#9a968f' }}>
                    {order.normalized_expiry || '-'} {order.normalized_strike ?? ''} {order.normalized_option_side || ''}
                  </td>
                  <td className="py-2 pr-4" style={{ color: '#f4f1ea' }}>{order.normalized_qty ?? order.qty ?? '-'}</td>
                  <td className="py-2 pr-4">
                    <span className={badge(order)}>{order.status || (order.blocked ? 'BLOCKED' : order.phase)}</span>
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs" style={{ color: '#77736c' }}>{order.order_id || '-'}</td>
                  <td className="py-2 text-xs max-w-sm" style={{ color: '#9a968f' }}>{order.reason || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
