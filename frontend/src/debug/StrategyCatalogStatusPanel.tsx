import { AlertTriangle, BookOpen, Boxes, Database, Layers3, Loader2, RefreshCw, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  StrategyCatalogStatusDisabledError,
  createPaperStrategyInstance,
  getStrategyCatalogStatusBundle,
  pausePaperStrategyInstance,
  resumePaperStrategyInstance,
  type StrategyCatalogItem,
  type StrategyCatalogStatusBundle,
  type UserStrategyInstanceStatus,
} from '../api'
import { MotionPulseText, MotionSpinner } from '../components/MotionPrimitives'
import '../dashboard/dashboard.css'
import './strategyCatalogStatus.css'

type PanelState = 'loading' | 'ready' | 'disabled' | 'error'

export function StrategyCatalogStatusPanel({
  enabled,
  mutationEnabled = false,
}: {
  enabled: boolean
  mutationEnabled?: boolean
}) {
  const [data, setData] = useState<StrategyCatalogStatusBundle | null>(null)
  const [state, setState] = useState<PanelState>(enabled ? 'loading' : 'disabled')
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  // Lifecycle controls render only when BOTH build flags are on.
  const lifecycleControls = enabled && mutationEnabled

  const load = useCallback(async (soft = false) => {
    if (!enabled) {
      setData(null)
      setState('disabled')
      setError('')
      return
    }
    if (soft) setRefreshing(true)
    try {
      const next = await getStrategyCatalogStatusBundle()
      setData(next)
      setState('ready')
      setError('')
    } catch (err) {
      setData(null)
      if (err instanceof StrategyCatalogStatusDisabledError) {
        setState('disabled')
        setError('')
      } else {
        setState('error')
        setError('Could not load strategy catalog status.')
      }
    } finally {
      setRefreshing(false)
    }
  }, [enabled])

  useEffect(() => {
    if (!enabled) {
      setData(null)
      setState('disabled')
      setError('')
      return
    }
    let mounted = true
    getStrategyCatalogStatusBundle()
      .then((next) => {
        if (!mounted) return
        setData(next)
        setState('ready')
        setError('')
      })
      .catch((err) => {
        if (!mounted) return
        setData(null)
        if (err instanceof StrategyCatalogStatusDisabledError) {
          setState('disabled')
          setError('')
        } else {
          setState('error')
          setError('Could not load strategy catalog status.')
        }
      })
    return () => {
      mounted = false
    }
  }, [enabled])

  if (state === 'loading') {
    return (
      <div className="nv-dash-state">
        <MotionSpinner className="text-cyan-300">
          <Loader2 size={34} />
        </MotionSpinner>
        <MotionPulseText className="text-sm text-white/60 font-medium">Loading strategy status</MotionPulseText>
      </div>
    )
  }

  if (state === 'disabled') {
    return (
      <div className="nv-dash strategy-status">
        <PanelHead refreshing={false} onRefresh={() => void load(true)} refreshDisabled={!enabled} />
        <div className="strategy-status-disabled" role="status">
          <AlertTriangle size={24} />
          <div>
            <h2>Strategy Catalog Status is disabled.</h2>
            <p>
              This internal-only view requires VITE_ENABLE_STRATEGY_CATALOG_STATUS=true in the frontend build.
            </p>
          </div>
        </div>
      </div>
    )
  }

  if (state === 'error' && !data) {
    return (
      <div className="nv-dash strategy-status">
        <PanelHead refreshing={refreshing} onRefresh={() => void load(true)} />
        <div className="nv-dash-state">
          <p className="nv-dash-error">{error}</p>
          <button className="secondary-button" type="button" onClick={() => void load(true)}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>
    )
  }

  if (!data) return null

  const catalog = data.catalog.catalog ?? []
  const instances = data.instances.instances ?? []

  return (
    <div className="nv-dash strategy-status">
      <PanelHead refreshing={refreshing} onRefresh={() => void load(true)} />
      <StatusStrip
        catalogConfigured={data.catalog.database_configured}
        instancesConfigured={data.instances.database_configured}
        lifecycleControls={lifecycleControls}
      />
      <StrategyKpis catalog={catalog} instances={instances} />
      <CatalogPanel catalog={catalog} lifecycleControls={lifecycleControls} onMutated={() => void load(true)} />
      <InstancesPanel instances={instances} lifecycleControls={lifecycleControls} onMutated={() => void load(true)} />
    </div>
  )
}

function PanelHead({
  refreshing,
  refreshDisabled = false,
  onRefresh,
}: {
  refreshing: boolean
  refreshDisabled?: boolean
  onRefresh: () => void
}) {
  return (
    <div className="nv-dash-head">
      <div>
        <h1>Strategy Catalog</h1>
        <p><ShieldCheck size={13} /> Read-only internal strategy visibility</p>
      </div>
      <button
        className="secondary-button nv-refresh"
        type="button"
        onClick={onRefresh}
        aria-label="Refresh strategy catalog status"
        disabled={refreshDisabled}
      >
        {refreshing ? (
          <MotionSpinner>
            <Loader2 size={14} />
          </MotionSpinner>
        ) : (
          <RefreshCw size={14} />
        )}
        {refreshing ? 'Refreshing' : 'Refresh'}
      </button>
    </div>
  )
}

function StatusStrip({
  catalogConfigured,
  instancesConfigured,
  lifecycleControls,
}: {
  catalogConfigured?: boolean
  instancesConfigured?: boolean
  lifecycleControls: boolean
}) {
  return (
    <section className="strategy-status-strip" aria-label="Strategy status safety">
      <Flag label="CATALOG DB" on={catalogConfigured !== false} />
      <Flag label="INSTANCES DB" on={instancesConfigured !== false} />
      <Flag label="READ ONLY" on={!lifecycleControls} />
      <Flag label="PAPER LIFECYCLE" on={lifecycleControls} />
      <Flag label="EXECUTION" on={false} invert />
    </section>
  )
}

function Flag({ label, on, invert = false }: { label: string; on?: boolean; invert?: boolean }) {
  const ok = invert ? !on : !!on
  return (
    <span className={`strategy-status-flag ${ok ? 'ok' : 'warn'}`}>
      <span>{label}</span>
      <strong>{ok ? 'OK' : 'OFF'}</strong>
    </span>
  )
}

function StrategyKpis({
  catalog,
  instances,
}: {
  catalog: StrategyCatalogItem[]
  instances: UserStrategyInstanceStatus[]
}) {
  const counts = useMemo(() => ({
    catalog: catalog.length,
    active: catalog.filter((row) => row.status === 'active').length,
    beta: catalog.filter((row) => row.status === 'beta').length,
    comingSoon: catalog.filter((row) => row.status === 'coming_soon').length,
    instances: instances.length,
    paper: instances.filter((row) => row.execution_mode === 'paper_live_data').length,
  }), [catalog, instances])

  const cards = [
    ['Catalog rows', counts.catalog, <BookOpen size={14} />],
    ['Active', counts.active, <ShieldCheck size={14} />],
    ['Beta', counts.beta, <Layers3 size={14} />],
    ['Coming soon', counts.comingSoon, <Database size={14} />],
    ['My instances', counts.instances, <Boxes size={14} />],
    ['Paper labels', counts.paper, <Database size={14} />],
  ] as const

  return (
    <div className="nv-kpi-grid strategy-status-count-grid">
      {cards.map(([label, value, icon]) => (
        <div className="nv-kpi-card strategy-status-count" key={label}>
          <span className="nv-kpi-label">{icon} {label}</span>
          <span className="strategy-status-count-value">{value}</span>
        </div>
      ))}
    </div>
  )
}

function CatalogPanel({
  catalog,
  lifecycleControls,
  onMutated,
}: {
  catalog: StrategyCatalogItem[]
  lifecycleControls: boolean
  onMutated: () => void
}) {
  return (
    <section className="nv-panel">
      <div className="nv-panel-head">
        <h2><BookOpen size={15} /> Strategy Catalog</h2>
        <span className="nv-panel-note">{catalog.length} row{catalog.length === 1 ? '' : 's'}</span>
      </div>
      {catalog.length ? (
        <CatalogTable catalog={catalog} lifecycleControls={lifecycleControls} onMutated={onMutated} />
      ) : (
        <EmptyState label="No catalog rows." />
      )}
    </section>
  )
}

function CatalogTable({
  catalog,
  lifecycleControls,
  onMutated,
}: {
  catalog: StrategyCatalogItem[]
  lifecycleControls: boolean
  onMutated: () => void
}) {
  return (
    <div className="nv-table-wrap">
      <table className="nv-table strategy-status-table">
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Code</th>
            <th>Status</th>
            <th>Source</th>
            <th>Versions</th>
            <th>Updated</th>
            {lifecycleControls ? <th>Paper lifecycle</th> : null}
          </tr>
        </thead>
        <tbody>
          {catalog.map((strategy, index) => (
            <tr key={strategy.id ?? `${strategy.code}-${index}`}>
              <td>
                <strong>{safeText(strategy.name)}</strong>
                {strategy.description ? <span className="strategy-status-description">{strategy.description}</span> : null}
              </td>
              <td className="nv-td-symbol">{safeText(strategy.code)}</td>
              <td><StatusPill status={strategy.status} /></td>
              <td>{sourceLabel(strategy.source_type)}</td>
              <td>{versionSummary(strategy)}</td>
              <td className="nv-td-time">{formatTime(strategy.updated_at)}</td>
              {lifecycleControls ? (
                <td>
                  {strategy.code && (strategy.status === 'active' || strategy.status === 'beta') ? (
                    <LifecycleConfirmButton
                      label="Create paper instance"
                      onRun={() => createPaperStrategyInstance({ catalogCode: String(strategy.code) })}
                      onDone={onMutated}
                    />
                  ) : (
                    <span className="strategy-lifecycle-muted">-</span>
                  )}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function InstancesPanel({
  instances,
  lifecycleControls,
  onMutated,
}: {
  instances: UserStrategyInstanceStatus[]
  lifecycleControls: boolean
  onMutated: () => void
}) {
  return (
    <section className="nv-panel">
      <div className="nv-panel-head">
        <h2><Boxes size={15} /> My Strategy Instances</h2>
        <span className="nv-panel-note">{instances.length} row{instances.length === 1 ? '' : 's'}</span>
      </div>
      {instances.length ? (
        <InstancesTable instances={instances} lifecycleControls={lifecycleControls} onMutated={onMutated} />
      ) : (
        <EmptyState label="No strategy instances." />
      )}
    </section>
  )
}

function _lifecycleEligible(instance: UserStrategyInstanceStatus): boolean {
  const mode = instance.execution_mode
  if (mode !== 'paper_live_data' && mode !== 'signal_only') return false
  return instance.status === 'active' || instance.status === 'paused'
}

function InstancesTable({
  instances,
  lifecycleControls,
  onMutated,
}: {
  instances: UserStrategyInstanceStatus[]
  lifecycleControls: boolean
  onMutated: () => void
}) {
  return (
    <div className="nv-table-wrap">
      <table className="nv-table strategy-status-table">
        <thead>
          <tr>
            <th>Instance</th>
            <th>Strategy</th>
            <th>Status</th>
            <th>Mode</th>
            <th className="num">Lots</th>
            <th>Side</th>
            <th>Version</th>
            <th>Updated</th>
            {lifecycleControls ? <th>Paper lifecycle</th> : null}
          </tr>
        </thead>
        <tbody>
          {instances.map((instance, index) => (
            <tr key={instance.id ?? `${instance.strategy_code}-${index}`}>
              <td>
                <strong>{safeText(instance.instance_label ?? shortId(instance.id))}</strong>
                <code>{shortId(instance.id)}</code>
              </td>
              <td className="nv-td-symbol">{safeText(instance.strategy_code ?? instance.strategy_name)}</td>
              <td><StatusPill status={instance.status} /></td>
              <td><ModePill mode={instance.execution_mode} /></td>
              <td className="num">{formatCount(instance.lots)}</td>
              <td>{safeText(instance.side_preference)}</td>
              <td>{safeText(instance.strategy_version ?? instance.payload_version)}</td>
              <td className="nv-td-time">{formatTime(instance.updated_at)}</td>
              {lifecycleControls ? (
                <td>
                  {instance.id && _lifecycleEligible(instance) ? (
                    instance.status === 'active' ? (
                      <LifecycleConfirmButton
                        label="Pause"
                        onRun={() => pausePaperStrategyInstance(String(instance.id))}
                        onDone={onMutated}
                      />
                    ) : (
                      <LifecycleConfirmButton
                        label="Resume"
                        onRun={() => resumePaperStrategyInstance(String(instance.id))}
                        onDone={onMutated}
                      />
                    )
                  ) : (
                    <span className="strategy-lifecycle-muted">-</span>
                  )}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Two-step confirm control. Always states the paper-only guarantee before
 * anything is sent; refetches (no optimistic update) when finished. */
function LifecycleConfirmButton({
  label,
  onRun,
  onDone,
}: {
  label: string
  onRun: () => Promise<unknown>
  onDone: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  async function run() {
    setBusy(true)
    setMessage('')
    try {
      await onRun()
      setConfirming(false)
      onDone()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Request failed.')
    } finally {
      setBusy(false)
    }
  }

  if (!confirming) {
    return (
      <div className="strategy-lifecycle-cell">
        <button className="secondary-button strategy-lifecycle-button" type="button" onClick={() => setConfirming(true)}>
          {label}
        </button>
        {message ? <span className="strategy-lifecycle-error">{message}</span> : null}
      </div>
    )
  }

  return (
    <div className="strategy-lifecycle-cell">
      <span className="strategy-lifecycle-note">Paper only. No orders will be placed.</span>
      <button className="secondary-button strategy-lifecycle-button" type="button" disabled={busy} onClick={() => void run()}>
        {busy ? <MotionSpinner><Loader2 size={12} /></MotionSpinner> : null}
        Confirm {label.toLowerCase()}
      </button>
      <button className="secondary-button strategy-lifecycle-button" type="button" disabled={busy} onClick={() => setConfirming(false)}>
        Cancel
      </button>
      {message ? <span className="strategy-lifecycle-error">{message}</span> : null}
    </div>
  )
}

function StatusPill({ status }: { status?: string | null }) {
  const value = safeText(status)
  return <span className={`strategy-status-pill ${statusTone(value)}`}>{statusLabel(value)}</span>
}

function ModePill({ mode }: { mode?: string | null }) {
  const value = safeText(mode)
  return <span className={`strategy-status-pill ${modeTone(value)}`}>{modeLabel(value)}</span>
}

function EmptyState({ label }: { label: string }) {
  return <div className="nv-chart-empty">{label}</div>
}

function versionSummary(strategy: StrategyCatalogItem): string {
  const versions = strategy.versions ?? []
  if (!versions.length) return '-'
  return versions
    .map((version) => {
      const name = safeText(version.version)
      const status = version.status ? ` ${statusLabel(version.status)}` : ''
      const payload = version.payload_version ? ` (${version.payload_version})` : ''
      return `${name}${status}${payload}`
    })
    .join(', ')
}

function sourceLabel(value?: string | null): string {
  const source = safeText(value)
  return source.replaceAll('_', ' ')
}

function statusLabel(value?: string | null): string {
  return safeText(value).replaceAll('_', ' ')
}

function modeLabel(value?: string | null): string {
  if (value === 'paper_live_data') return 'paper'
  if (value === 'real_orders') return 'live'
  if (value === 'signal_only') return 'signal-only'
  return statusLabel(value)
}

function statusTone(status: string): string {
  const lowered = status.toLowerCase()
  if (lowered === 'active' || lowered === 'beta') return 'ok'
  if (lowered === 'coming_soon' || lowered === 'paused') return 'warn'
  if (lowered === 'disabled' || lowered === 'deprecated' || lowered === 'error') return 'bad'
  return 'muted'
}

function modeTone(mode: string): string {
  if (mode === 'paper_live_data' || mode === 'signal_only') return 'ok'
  if (mode === 'real_orders') return 'warn'
  return 'muted'
}

function formatCount(value?: number | null): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '0'
}

function shortId(value?: string | null): string {
  if (!value) return '-'
  const text = String(value)
  if (text.length <= 12) return text
  return `${text.slice(0, 8)}...${text.slice(-4)}`
}

function formatTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

function safeText(value?: string | number | null): string {
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}
