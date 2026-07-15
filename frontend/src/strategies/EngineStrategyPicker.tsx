import { Check, Loader2, Lock, Play, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getEngineStrategies, type EngineStrategy } from '../api'
import { blockerText } from './strategyBlockers'

const SOURCE_LABELS: Record<string, string> = {
  PERSONAL_TRADINGVIEW: 'Personal TradingView',
  NOVA_SHARED: 'NOVA catalog',
  NOVA_HOSTED_PERSONAL: 'NOVA hosted',
}

function sourceLabel(strategy: EngineStrategy): string {
  if (strategy.setup_type === 'NOVA_MANAGED_TRADINGVIEW') return 'NOVA-managed'
  if (strategy.setup_type === 'USER_MANAGED_TRADINGVIEW') return 'Premium'
  return SOURCE_LABELS[strategy.source_type] ?? strategy.source_type
}

/** Owner-scoped engine strategy picker. Reads the unified eligibility API and
 * links each ready strategy to its existing lifecycle controls — it never
 * introduces a second activation path. */
export function EngineStrategyPicker({ onManage }: { onManage: (instanceId: string) => void }) {
  const [strategies, setStrategies] = useState<EngineStrategy[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    getEngineStrategies()
      .then((rows) => { if (active) setStrategies(rows) })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : 'Could not load strategies.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  if (loading) return <div className="ps-page-state"><Loader2 className="ps-spin" size={20} /> Loading engine strategies…</div>

  const ready = strategies.filter((s) => s.selectable)
  const pending = strategies.filter((s) => !s.selectable)

  return (
    <div className="ps-page">
      <div className="ps-heading">
        <div>
          <span className="ps-eyebrow"><ShieldCheck size={13} /> Verified strategies only</span>
          <h1>Engine strategy picker</h1>
          <p>Only strategies that cleared genuine HOLD, paper entry and paper exit are selectable.</p>
        </div>
      </div>
      {error ? <div className="ps-message error" role="alert">{error}</div> : null}

      <section className="ps-card">
        <div className="ps-card-head"><div><span>Ready to run</span><h2>Selectable</h2></div><strong>{ready.length}</strong></div>
        {ready.length ? (
          <ul className="ps-picker-list">
            {ready.map((strategy) => (
              <li key={strategy.instance_id} className="ps-picker-item ready">
                <div><strong>{strategy.display_name}</strong><span>{sourceLabel(strategy)} · <span className="ps-picker-ready"><Check size={12} /> READY</span></span></div>
                <button type="button" className="ps-primary" onClick={() => onManage(strategy.instance_id)}><Play size={14} /> Select &amp; manage</button>
              </li>
            ))}
          </ul>
        ) : <div className="ps-empty-small"><Lock size={22} /><strong>No verified strategies yet</strong><span>Finish TradingView setup and paper verification to unlock a strategy.</span></div>}
      </section>

      {pending.length ? (
        <section className="ps-card">
          <div className="ps-card-head"><div><span>Setup in progress</span><h2>Not selectable</h2></div><strong>{pending.length}</strong></div>
          <ul className="ps-picker-list">
            {pending.map((strategy) => (
              <li key={strategy.instance_id} className="ps-picker-item pending">
                <div><strong>{strategy.display_name}</strong><span>{sourceLabel(strategy)} · {blockerText(strategy.blocking_reason)}</span></div>
                <button type="button" className="secondary-button" onClick={() => onManage(strategy.instance_id)}>Continue setup</button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}
