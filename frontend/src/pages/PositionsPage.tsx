import { useEffect, useState } from 'react'
import { getPositions } from '../api/dashboard'
import { OpenPosition } from '../types'

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
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold mb-6">Position</h1>
      <div className="card">
        {loading ? (
          <p className="text-gray-500 text-sm">Loading position...</p>
        ) : !position?.has_open_position ? (
          <p className="text-gray-500 text-sm">No open position.</p>
        ) : (
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-gray-500">Strategy</p>
              <p className="text-gray-100 font-medium">{position.strategy_code}</p>
            </div>
            <div>
              <p className="text-gray-500">Trading Symbol</p>
              <p className="text-gray-100 font-medium">{position.trading_symbol}</p>
            </div>
            <div>
              <p className="text-gray-500">Security ID</p>
              <p className="text-gray-100 font-mono">{position.security_id}</p>
            </div>
            <div>
              <p className="text-gray-500">Qty</p>
              <p className="text-gray-100 font-medium">{position.qty}</p>
            </div>
            <div>
              <p className="text-gray-500">Entry Order ID</p>
              <p className="text-gray-100 font-mono text-xs">{position.entry_order_id}</p>
            </div>
            <div>
              <p className="text-gray-500">Entry Price</p>
              <p className="text-gray-100 font-medium">
                {position.entry_price == null ? '-' : position.entry_price.toFixed(2)}
              </p>
            </div>
            <div className="col-span-2">
              <p className="text-gray-500">Opened At</p>
              <p className="text-gray-100">{position.opened_at ? new Date(position.opened_at).toLocaleString() : '-'}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
