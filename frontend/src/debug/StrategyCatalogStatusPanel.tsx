import { AlertTriangle, BookOpen, Boxes, Database, Layers3, Loader2, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  StrategyCatalogStatusDisabledError,
  archivePaperStrategyInstance,
  createPaperStrategyInstance,
  getStrategyInstanceDetails,
  getStrategyCatalogStatusBundle,
  pausePaperStrategyInstance,
  restorePaperStrategyInstance,
  resumePaperStrategyInstance,
  type StrategyCatalogItem,
  type StrategyInstanceDetailsResponse,
  type StrategyInstanceHistoryItem,
  type StrategyCatalogStatusBundle,
  type UserStrategyInstanceStatus,
  updateStrategyInstanceSettings,
} from '../api'
import { MotionPulseText, MotionSpinner } from '../components/MotionPrimitives'
import '../dashboard/dashboard.css'
import './strategyCatalogStatus.css'

type PanelState = 'loading' | 'ready' | 'disabled' | 'error'
type EditableSidePreference = 'BOTH' | 'CE' | 'PE'

const SIDE_OPTIONS: EditableSidePreference[] = ['BOTH', 'CE', 'PE']

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
  const [detailsInstanceId, setDetailsInstanceId] = useState<string | null>(null)
  // Lifecycle controls render only when BOTH build flags are on.
  const lifecycleControls = enabled && mutationEnabled

  const load = useCallback(async (soft = false) => {
    if (!enabled) {
      setData(null)
      setState('disabled')
      setError('')
      setDetailsInstanceId(null)
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
      <InstancesPanel
        instances={instances}
        lifecycleControls={lifecycleControls}
        onDetails={setDetailsInstanceId}
        onMutated={() => void load(true)}
      />
      {detailsInstanceId ? (
        <InstanceDetailsDrawer instanceId={detailsInstanceId} onClose={() => setDetailsInstanceId(null)} />
      ) : null}
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
  onDetails,
  onMutated,
}: {
  instances: UserStrategyInstanceStatus[]
  lifecycleControls: boolean
  onDetails: (instanceId: string) => void
  onMutated: () => void
}) {
  const [showArchived, setShowArchived] = useState(false)
  const archivedCount = instances.filter((instance) => instance.status === 'archived').length
  const visibleInstances = showArchived ? instances : instances.filter((instance) => instance.status !== 'archived')

  return (
    <section className="nv-panel">
      <div className="nv-panel-head">
        <h2><Boxes size={15} /> My Strategy Instances</h2>
        <div className="strategy-panel-tools">
          {archivedCount ? (
            <label className="strategy-archive-toggle">
              <input
                checked={showArchived}
                type="checkbox"
                onChange={(event) => setShowArchived(event.target.checked)}
              />
              <span>Show archived</span>
            </label>
          ) : null}
          <span className="nv-panel-note">
            {visibleInstances.length} row{visibleInstances.length === 1 ? '' : 's'}
          </span>
        </div>
      </div>
      {visibleInstances.length ? (
        <InstancesTable
          instances={visibleInstances}
          lifecycleControls={lifecycleControls}
          onDetails={onDetails}
          onMutated={onMutated}
        />
      ) : (
        <EmptyState label="No strategy instances." />
      )}
    </section>
  )
}

function _lifecycleEligible(instance: UserStrategyInstanceStatus): boolean {
  const mode = instance.execution_mode
  if (mode !== 'paper_live_data' && mode !== 'signal_only') return false
  return instance.status === 'active' || instance.status === 'paused' || instance.status === 'archived'
}

function InstancesTable({
  instances,
  lifecycleControls,
  onDetails,
  onMutated,
}: {
  instances: UserStrategyInstanceStatus[]
  lifecycleControls: boolean
  onDetails: (instanceId: string) => void
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
            <th>Details</th>
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
              <td>
                {instance.id ? (
                  <button
                    className="secondary-button strategy-lifecycle-button"
                    type="button"
                    onClick={() => onDetails(String(instance.id))}
                  >
                    Details
                  </button>
                ) : (
                  <span className="strategy-lifecycle-muted">-</span>
                )}
              </td>
              {lifecycleControls ? (
                <td>
                  <InstanceLifecycleCell instance={instance} onMutated={onMutated} />
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function InstanceDetailsDrawer({
  instanceId,
  onClose,
}: {
  instanceId: string
  onClose: () => void
}) {
  const [details, setDetails] = useState<StrategyInstanceDetailsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadDetails = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setDetails(await getStrategyInstanceDetails(instanceId))
    } catch (err) {
      setDetails(null)
      setError(err instanceof Error ? err.message : 'Could not load instance details.')
    } finally {
      setLoading(false)
    }
  }, [instanceId])

  useEffect(() => {
    void loadDetails()
  }, [loadDetails])

  const instance = details?.instance
  const history = details?.history ?? []

  return (
    <div className="strategy-details-backdrop" role="presentation">
      <section className="strategy-details-drawer" role="dialog" aria-modal="true" aria-labelledby="strategy-details-title">
        <div className="strategy-details-head">
          <div>
            <h2 id="strategy-details-title"><Boxes size={15} /> Instance details</h2>
            <span>{shortId(instanceId)}</span>
          </div>
          <div className="strategy-details-actions">
            <button className="secondary-button strategy-lifecycle-button" type="button" disabled={loading} onClick={() => void loadDetails()}>
              {loading ? <MotionSpinner><Loader2 size={12} /></MotionSpinner> : <RefreshCw size={12} />}
              Refresh
            </button>
            <button className="secondary-button strategy-lifecycle-button" type="button" onClick={onClose}>
              <X size={12} />
              Close
            </button>
          </div>
        </div>

        {error ? <p className="strategy-lifecycle-error">{error}</p> : null}
        {loading && !instance ? (
          <div className="nv-dash-state strategy-details-loading">
            <MotionSpinner><Loader2 size={22} /></MotionSpinner>
            <MotionPulseText className="text-sm text-white/60 font-medium">Loading details</MotionPulseText>
          </div>
        ) : null}

        {instance ? (
          <>
            <div className="strategy-details-grid">
              <DetailItem label="Strategy" value={instance.strategy_name ?? instance.strategy_code} />
              <DetailItem label="Code" value={instance.strategy_code} />
              <DetailItem label="Version" value={instance.strategy_version ?? instance.payload_version} />
              <DetailItem label="Label" value={instance.instance_label} />
              <DetailItem label="Status" value={statusLabel(instance.status)} />
              <DetailItem label="Mode" value={modeLabel(instance.execution_mode)} />
              <DetailItem label="Lots" value={instance.lots} />
              <DetailItem label="Side" value={instance.side_preference} />
              <DetailItem label="Created" value={formatTime(instance.created_at)} />
              <DetailItem label="Updated" value={formatTime(instance.updated_at)} />
            </div>

            <div className="strategy-details-section">
              <div className="nv-panel-head">
                <h3>History</h3>
                <span className="nv-panel-note">{history.length} row{history.length === 1 ? '' : 's'}</span>
              </div>
              {history.length ? (
                <ol className="strategy-details-history">
                  {history.map((item, index) => (
                    <HistoryRow item={item} key={`${item.event_type ?? 'event'}-${item.created_at ?? index}`} />
                  ))}
                </ol>
              ) : (
                <EmptyState label="No history rows." />
              )}
            </div>
          </>
        ) : null}
      </section>
    </div>
  )
}

function DetailItem({ label, value }: { label: string; value?: string | number | null }) {
  return (
    <div className="strategy-details-item">
      <span>{label}</span>
      <strong>{safeText(value)}</strong>
    </div>
  )
}

function HistoryRow({ item }: { item: StrategyInstanceHistoryItem }) {
  const changed = formatHistoryFields(item.changed_fields)
  const before = formatHistoryValues(item.old_values)
  const after = formatHistoryValues(item.new_values)
  return (
    <li>
      <div>
        <strong>{safeText(item.summary ?? item.event_type)}</strong>
        <span>{formatTime(item.created_at)}</span>
      </div>
      {changed ? <p>Changed: {changed}</p> : null}
      {item.previous_status || item.new_status ? (
        <p>{statusLabel(item.previous_status)}{' -> '}{statusLabel(item.new_status)}</p>
      ) : null}
      {before ? <p>Before: {before}</p> : null}
      {after ? <p>After: {after}</p> : null}
      {item.actor_type ? <p>Actor: {statusLabel(item.actor_type)}</p> : null}
    </li>
  )
}

function InstanceLifecycleCell({
  instance,
  onMutated,
}: {
  instance: UserStrategyInstanceStatus
  onMutated: () => void
}) {
  if (!instance.id || !_lifecycleEligible(instance)) {
    return <span className="strategy-lifecycle-muted">-</span>
  }

  if (instance.status === 'active') {
    return (
      <div className="strategy-lifecycle-stack">
        <LifecycleConfirmButton
          label="Pause"
          onRun={() => pausePaperStrategyInstance(String(instance.id))}
          onDone={onMutated}
        />
        <span className="strategy-lifecycle-note">Pause to edit</span>
      </div>
    )
  }

  if (instance.status === 'archived') {
    return (
      <div className="strategy-lifecycle-stack">
        <LifecycleConfirmButton
          label="Restore"
          onRun={() => restorePaperStrategyInstance(String(instance.id))}
          onDone={onMutated}
        />
      </div>
    )
  }

  return (
    <div className="strategy-lifecycle-stack">
      <LifecycleConfirmButton
        label="Resume"
        onRun={() => resumePaperStrategyInstance(String(instance.id))}
        onDone={onMutated}
      />
      <LifecycleConfirmButton
        label="Archive"
        onRun={() => archivePaperStrategyInstance(String(instance.id))}
        onDone={onMutated}
      />
      <InstanceSettingsEditControl instance={instance} onDone={onMutated} />
    </div>
  )
}

function InstanceSettingsEditControl({
  instance,
  onDone,
}: {
  instance: UserStrategyInstanceStatus
  onDone: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [label, setLabel] = useState('')
  const [lots, setLots] = useState('1')
  const [side, setSide] = useState<EditableSidePreference>('BOTH')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  function openEditor() {
    setLabel(instance.instance_label ?? '')
    setLots(String(instance.lots ?? 1))
    setSide(sidePreferenceValue(instance.side_preference))
    setMessage('')
    setEditing(true)
  }

  async function confirmEdit() {
    const parsedLots = Number.parseInt(lots, 10)
    if (!Number.isFinite(parsedLots) || parsedLots < 1 || parsedLots > 20) {
      setMessage('Lots must be between 1 and 20.')
      return
    }

    setBusy(true)
    setMessage('')
    try {
      await updateStrategyInstanceSettings(String(instance.id), {
        instanceLabel: label.trim() || null,
        lots: parsedLots,
        sidePreference: side,
      })
      setEditing(false)
      onDone()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Request failed.')
    } finally {
      setBusy(false)
    }
  }

  if (!editing) {
    return (
      <div className="strategy-lifecycle-cell">
        <button className="secondary-button strategy-lifecycle-button" type="button" onClick={openEditor}>
          Edit
        </button>
        {message ? <span className="strategy-lifecycle-error">{message}</span> : null}
      </div>
    )
  }

  return (
    <div className="strategy-settings-editor">
      <label>
        <span>Label</span>
        <input value={label} maxLength={120} onChange={(event) => setLabel(event.target.value)} />
      </label>
      <label>
        <span>Lots</span>
        <input
          value={lots}
          inputMode="numeric"
          min={1}
          max={20}
          type="number"
          onChange={(event) => setLots(event.target.value)}
        />
      </label>
      <label>
        <span>Side</span>
        <select value={side} onChange={(event) => setSide(sidePreferenceValue(event.target.value))}>
          {SIDE_OPTIONS.map((option) => (
            <option value={option} key={option}>{option}</option>
          ))}
        </select>
      </label>
      <span className="strategy-lifecycle-note">Paper only. No orders will be placed.</span>
      <div className="strategy-lifecycle-cell">
        <button className="secondary-button strategy-lifecycle-button" type="button" disabled={busy} onClick={() => void confirmEdit()}>
          {busy ? <MotionSpinner><Loader2 size={12} /></MotionSpinner> : null}
          Confirm edit
        </button>
        <button className="secondary-button strategy-lifecycle-button" type="button" disabled={busy} onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
      {message ? <span className="strategy-lifecycle-error">{message}</span> : null}
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

function sidePreferenceValue(value?: string | null): EditableSidePreference {
  if (value === 'CE' || value === 'PE' || value === 'BOTH') return value
  return 'BOTH'
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
  if (lowered === 'coming_soon' || lowered === 'paused' || lowered === 'archived') return 'warn'
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

function formatHistoryFields(value?: string[] | null): string {
  return value?.length ? value.map((field) => statusLabel(field)).join(', ') : ''
}

function formatHistoryValues(value?: Record<string, string | number | boolean | null>): string {
  const entries = Object.entries(value ?? {})
  if (!entries.length) return ''
  return entries.map(([key, item]) => `${statusLabel(key)}: ${safeText(item)}`).join(', ')
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

function safeText(value?: string | number | boolean | null): string {
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}
