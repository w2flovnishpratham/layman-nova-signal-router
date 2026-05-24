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
      <h1 className="text-2xl font-bold mb-6">Orders</h1>
      <div className="card overflow-x-auto">
        {loading ? (
          <p className="text-gray-500 text-sm">Loading orders...</p>
        ) : orders.length === 0 ? (
          <p className="text-gray-500 text-sm">No order events yet.</p>
        ) : (
          <table className="w-full text-sm text-left">
            <thead>
              <tr className="text-gray-500 border-b border-gray-800">
                <th className="pb-2 pr-4">Time</th>
                <th className="pb-2 pr-4">Format</th>
                <th className="pb-2 pr-4">Phase</th>
                <th className="pb-2 pr-4">Action</th>
                <th className="pb-2 pr-4">Side</th>
                <th className="pb-2 pr-4">Mode</th>
                <th className="pb-2 pr-4">Symbol</th>
                <th className="pb-2 pr-4">Option</th>
                <th className="pb-2 pr-4">Qty</th>
                <th className="pb-2 pr-4">Status</th>
                <th className="pb-2 pr-4">Order ID</th>
                <th className="pb-2">Reason</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id} className="border-b border-gray-800/50 hover:bg-gray-800/30 align-top">
                  <td className="py-2 pr-4 text-gray-400 text-xs whitespace-nowrap">
                    {order.created_at ? new Date(order.created_at).toLocaleString() : '-'}
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs">{order.payload_format || '-'}</td>
                  <td className="py-2 pr-4 font-mono text-xs">{order.phase || '-'}</td>
                  <td className="py-2 pr-4">{order.normalized_action || order.action || '-'}</td>
                  <td className="py-2 pr-4">{order.normalized_side || order.side || '-'}</td>
                  <td className="py-2 pr-4">
                    <span className={order.dhan_mode === 'REAL' ? 'text-red-400' : 'text-green-400'}>
                      {order.dhan_mode || '-'}
                    </span>
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs">{order.trading_symbol || order.normalized_symbol || order.security_id || '-'}</td>
                  <td className="py-2 pr-4 text-xs">
                    {order.normalized_expiry || '-'} {order.normalized_strike ?? ''} {order.normalized_option_side || ''}
                  </td>
                  <td className="py-2 pr-4">{order.normalized_qty ?? order.qty ?? '-'}</td>
                  <td className="py-2 pr-4">
                    <span className={badge(order)}>{order.status || (order.blocked ? 'BLOCKED' : order.phase)}</span>
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs text-gray-400">{order.order_id || '-'}</td>
                  <td className="py-2 text-xs text-gray-400 max-w-sm">{order.reason || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
