import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { getLiveFlow } from '../api/dashboard'
import { usePolling } from '../hooks/usePolling'
import LiveFlowTimeline from '../components/LiveFlowTimeline'
import { LiveFlowStep } from '../types'

export default function LiveFlowPage() {
  const [steps, setSteps] = useState<LiveFlowStep[]>([])
  const [loading, setLoading] = useState(true)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const loadData = () => {
    getLiveFlow()
      .then(r => { setSteps(r.data); setLastUpdated(new Date()) })
      .catch((err) => console.warn('LiveFlowPage: failed to refresh:', err?.message ?? err))
      .finally(() => setLoading(false))
  }
  // Was 3s — bumped to 5s. LiveFlow timeline doesn't need sub-second freshness.
  usePolling(loadData, 5000)

  return (
    <div className="max-w-xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold" style={{ color: '#f4f1ea' }}>Live Flow</h1>
          {lastUpdated && (
            <p className="text-xs mt-0.5" style={{ color: '#77736c' }}>
              Updated {lastUpdated.toLocaleTimeString()} · auto-refreshes every 3s
            </p>
          )}
        </div>
        <button onClick={loadData} className="btn-ghost flex items-center gap-1.5 text-sm">
          <RefreshCw size={13} /> Refresh
        </button>
      </div>
      <div className="card">
        {loading ? <p className="text-sm" style={{ color: '#77736c' }}>Loading flow…</p> : <LiveFlowTimeline steps={steps} />}
      </div>
    </div>
  )
}
