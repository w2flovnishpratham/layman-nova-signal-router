import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { getLogs } from '../api/dashboard'
import { LogBundle } from '../types'

const tabs = [
  ['webhook_events', 'Webhook'],
  ['order_events', 'Orders'],
  ['audit_events', 'Audit'],
  ['error_events', 'Errors'],
] as const

export default function LogsPage() {
  const [logs, setLogs] = useState<LogBundle | null>(null)
  const [active, setActive] = useState<keyof LogBundle>('webhook_events')
  const [loading, setLoading] = useState(true)

  const loadData = () => {
    getLogs()
      .then((response) => setLogs(response.data))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadData()
    const id = window.setInterval(loadData, 5000)
    return () => window.clearInterval(id)
  }, [])

  const rows = logs?.[active] || []

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Logs</h1>
        <button onClick={loadData} className="btn-ghost flex items-center gap-2">
          <RefreshCw size={15} /> Refresh
        </button>
      </div>

      <div className="flex gap-2 mb-4">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setActive(key)}
            className={active === key ? 'btn-primary text-sm' : 'btn-ghost text-sm'}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="card">
        {loading ? (
          <p className="text-gray-500 text-sm">Loading logs...</p>
        ) : rows.length === 0 ? (
          <p className="text-gray-500 text-sm">No log entries yet.</p>
        ) : (
          <div className="space-y-3">
            {rows.slice().reverse().map((row, index) => (
              <pre key={index} className="overflow-x-auto rounded bg-gray-950 border border-gray-800 p-3 text-xs text-gray-300">
                {JSON.stringify(row, null, 2)}
              </pre>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
