import { Check, Loader2, Lock, Play, Settings2, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getEngineStrategies, setEngineSelection, type EngineStrategy } from '../api'
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

/** Owner-scoped engine strategy picker. Reads the unified eligibility API,
 * persists the selected strategy instance (execution authority remains the
 * per-instance private credential — selection never reroutes signals), and
 * links each strategy to its existing lifecycle controls. */
export function EngineStrategyPicker({ onManage }: { onManage: (instanceId: string) => void }) {
  const [strategies, setStrategies] = useState<EngineStrategy[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const { strategies: rows, selected_instance_id } = await getEngineStrategies()
    setStrategies(rows)
    setSelectedId(selected_instance_id)
  }, [])

  useEffect(() => {
    let active = true
    getEngineStrategies()
      .then(({ strategies: rows, selected_instance_id }) => {
        if (!active) return
        setStrategies(rows)
        setSelectedId(selected_instance_id)
      })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : 'Could not load strategies.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  async function select(instanceId: string) {
    setBusy(instanceId); setError('')
    try {
      await setEngineSelection(instanceId)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not select strategy.')
    } finally {
      setBusy('')
    }
  }

  if (loading) return <div className="ps-page-state"><Loader2 className="ps-spin" size={20} /> Loading engine strategies…</div>

  const ready = strategies.filter((s) => s.selectable)
  const pending = strategies.filter((s) => !s.selectable)
  const selected = strategies.find((s) => s.instance_id === selectedId) ?? null

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
        <div className="ps-card-head"><div><span>Engine</span><h2>Currently selected</h2></div></div>
        {selected ? (
          <div className={`ps-picker-item ${selected.selectable ? 'ready' : 'pending'}`}>
            <div><strong>{selected.display_name}</strong><span>{sourceLabel(selected)} · {selected.selectable ? 'Ready for paper' : blockerText(selected.blocking_reason)}</span></div>
            <button type="button" className="secondary-button" onClick={() => onManage(selected.instance_id)}><Settings2 size={14} /> Manage</button>
          </div>
        ) : <div className="ps-empty-small"><Lock size={22} /><strong>No strategy selected</strong><span>Select a verified strategy below to point the engine at it.</span></div>}
      </section>

      <section className="ps-card">
        <div className="ps-card-head"><div><span>Ready to run</span><h2>Selectable</h2></div><strong>{ready.length}</strong></div>
        {ready.length ? (
          <ul className="ps-picker-list">
            {ready.map((strategy) => {
              const isSelected = strategy.instance_id === selectedId
              return (
                <li key={strategy.instance_id} className="ps-picker-item ready">
                  <div><strong>{strategy.display_name}</strong><span>{sourceLabel(strategy)} · <span className="ps-picker-ready"><Check size={12} /> READY</span></span></div>
                  <div className="ps-actions">
                    {isSelected
                      ? <span className="ps-credential-status"><Check size={14} /> Selected</span>
                      : <button type="button" className="ps-primary" disabled={busy === strategy.instance_id} onClick={() => void select(strategy.instance_id)}><Play size={14} /> Select strategy</button>}
                    <button type="button" className="secondary-button" onClick={() => onManage(strategy.instance_id)}>Manage</button>
                  </div>
                </li>
              )
            })}
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
