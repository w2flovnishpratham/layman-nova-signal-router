import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { getLiveFlow } from '../api/dashboard'
import LiveFlowTimeline from '../components/LiveFlowTimeline'
import { LiveFlowStep } from '../types'

export default function LiveFlowPage() {
  const [steps, setSteps] = useState<LiveFlowStep[]>([])
  const [loading, setLoading] = useState(true)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const loadData = () => {
    getLiveFlow().then(r => { setSteps(r.data); setLastUpdated(new Date()) }).finally(() => setLoading(false))
  }
  useEffect(() => {
    loadData()
    const id = window.setInterval(loadData, 3000)
    return () => window.clearInterval(id)
  }, [])

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
