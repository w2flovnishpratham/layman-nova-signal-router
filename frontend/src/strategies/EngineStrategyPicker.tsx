import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/card'
import { toast } from '@/components/ui/toast'
import { Check, Lock, Play, Settings2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getEngineStrategies, setEngineSelection, type EngineStrategy } from '../api'
import { Skeleton } from '@/components/ui/skeleton'
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
    setBusy(instanceId)
    try {
      await setEngineSelection(instanceId)
      await load()
      toast.add({ title: 'Engine strategy selected.', type: 'success' })
    } catch (reason) {
      toast.add({
        title: reason instanceof Error ? reason.message : 'Could not select strategy.',
        type: 'error',
      })
    } finally {
      setBusy('')
    }
  }

  // The note and the section headings are fixed; the counts and the cards
  // themselves are what the request supplies.
  if (loading) return (
    <div className="ps-picker" role="status" aria-busy="true" aria-label="Loading engine strategies">
      <p className="ps-note">Only strategies that cleared genuine HOLD and the server&apos;s Paper-readiness checks are selectable.</p>
      {['Currently selected', 'Selectable'].map((heading) => (
        <section className="ps-picker-section" key={heading}>
          <h3>{heading}</h3>
          <div className="ps-strategy-grid">
            {Array.from({ length: heading === 'Currently selected' ? 1 : 2 }, (_, index) => (
              <div className="ps-strategy-card" key={index}>
                <Skeleton className="h-4 w-36" />
                <Skeleton className="mt-2 h-3 w-24" />
                <Skeleton className="mt-3 h-5 w-28 rounded-full" />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  )

  const ready = strategies.filter((s) => s.selectable)
  const pending = strategies.filter((s) => !s.selectable)
  const selected = strategies.find((s) => s.instance_id === selectedId) ?? null

  return (
    <div className="ps-picker">
      <p className="ps-note">Only strategies that cleared genuine HOLD and the server's Paper-readiness checks are selectable.</p>
      {error ? <div className="ps-message error" role="alert">{error}</div> : null}

      <section className="ps-picker-section">
        <h3>Currently selected</h3>
        {selected ? (
          <Card className={selected.selectable ? 'ps-strategy-card selected' : 'ps-strategy-card'}>
            <CardHeader>
              <CardTitle>{selected.display_name}</CardTitle>
              <CardDescription>{sourceLabel(selected)}</CardDescription>
            </CardHeader>
            <CardContent>
              {selected.selectable
                ? <Badge variant="outline" className="ps-badge-ready"><Check size={12} /> Ready for paper</Badge>
                : <Badge variant="secondary">{blockerText(selected.blocking_reason)}</Badge>}
            </CardContent>
            <CardFooter>
              <Button variant="unstyled" type="button" className="secondary-button" onClick={() => onManage(selected.instance_id)}><Settings2 size={14} /> Manage</Button>
            </CardFooter>
          </Card>
        ) : <div className="ps-empty-small"><Lock size={22} /><strong>No strategy selected</strong><span>Select a verified strategy below to point the engine at it.</span></div>}
      </section>

      <section className="ps-picker-section">
        <h3>Selectable <span className="ps-count">{ready.length}</span></h3>
        {ready.length ? (
          <div className="ps-strategy-grid">
            {ready.map((strategy) => {
              const isSelected = strategy.instance_id === selectedId
              return (
                <Card key={strategy.instance_id} className={isSelected ? 'ps-strategy-card selected' : 'ps-strategy-card'}>
                  <CardHeader>
                    <CardTitle>{strategy.display_name}</CardTitle>
                    <CardDescription>{sourceLabel(strategy)}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <Badge variant="outline" className="ps-badge-ready"><Check size={12} /> READY</Badge>
                  </CardContent>
                  <CardFooter>
                    {isSelected
                      ? <span className="ps-credential-status"><Check size={14} /> Selected</span>
                      : <Button variant="unstyled" type="button" className="ps-primary" disabled={busy === strategy.instance_id} onClick={() => void select(strategy.instance_id)}><Play size={14} /> Select strategy</Button>}
                    <Button variant="unstyled" type="button" className="secondary-button" onClick={() => onManage(strategy.instance_id)}>Manage</Button>
                  </CardFooter>
                </Card>
              )
            })}
          </div>
        ) : <div className="ps-empty-small"><Lock size={22} /><strong>No verified strategies yet</strong><span>Finish TradingView setup and paper verification to unlock a strategy.</span></div>}
      </section>

      {pending.length ? (
        <section className="ps-picker-section">
          <h3>Setup in progress <span className="ps-count">{pending.length}</span></h3>
          <div className="ps-strategy-grid">
            {pending.map((strategy) => (
              <Card key={strategy.instance_id} className="ps-strategy-card pending">
                <CardHeader>
                  <CardTitle>{strategy.display_name}</CardTitle>
                  <CardDescription>{sourceLabel(strategy)}</CardDescription>
                </CardHeader>
                <CardContent>
                  <Badge variant="secondary">{blockerText(strategy.blocking_reason)}</Badge>
                </CardContent>
                <CardFooter>
                  <Button variant="unstyled" type="button" className="secondary-button" onClick={() => onManage(strategy.instance_id)}>Continue setup</Button>
                </CardFooter>
              </Card>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  )
}
