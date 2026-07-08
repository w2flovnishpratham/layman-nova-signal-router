import { AlertTriangle, Database, FlaskConical, Loader2, RefreshCw, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  V2PaperStatusDisabledError,
  getV2PaperFanoutBundle,
  type V2PaperFanoutBundle,
  type V2PaperFanoutCounts,
  type V2PaperFanoutJob,
  type V2PaperOpenPositionSummary,
  type V2PaperStrategyInstance,
} from '../api'
import { MotionPulseText, MotionSpinner } from '../components/MotionPrimitives'
import '../dashboard/dashboard.css'
import './v2PaperStatus.css'

type PanelState = 'loading' | 'ready' | 'disabled' | 'error'

export function V2PaperStatusPanel({ enabled }: { enabled: boolean }) {
  const [data, setData] = useState<V2PaperFanoutBundle | null>(null)
  const [state, setState] = useState<PanelState>(enabled ? 'loading' : 'disabled')
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async (soft = false) => {
    if (!enabled) {
      setData(null)
      setState('disabled')
      setError('')
      return
    }
    if (soft) setRefreshing(true)
    try {
      const next = await getV2PaperFanoutBundle(20)
      setData(next)
      setState('ready')
      setError('')
    } catch (err) {
      setData(null)
      if (err instanceof V2PaperStatusDisabledError) {
        setState('disabled')
        setError('')
      } else {
        setState('error')
        setError('Could not load V2 paper status.')
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
    getV2PaperFanoutBundle(20)
      .then((next) => {
        if (!mounted) return
        setData(next)
        setState('ready')
        setError('')
      })
      .catch((err) => {
        if (!mounted) return
        setData(null)
        if (err instanceof V2PaperStatusDisabledError) {
          setState('disabled')
          setError('')
        } else {
          setState('error')
          setError('Could not load V2 paper status.')
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
        <MotionPulseText className="text-sm text-white/60 font-medium">Loading V2 paper status</MotionPulseText>
      </div>
    )
  }

  if (state === 'disabled') {
    return (
      <div className="nv-dash v2-paper-status">
        <PanelHead refreshing={false} onRefresh={() => void load(true)} refreshDisabled={!enabled} />
        <div className="v2-paper-disabled" role="status">
          <AlertTriangle size={24} />
          <div>
            <h2>V2 Paper Status is disabled.</h2>
            <p>
              This panel is an internal-only build feature. Set VITE_ENABLE_V2_PAPER_STATUS=true for an internal
              staging/dev build, and keep DEBUG_ENABLED, MULTI_STRATEGY_FANOUT, and V2_PAPER_RUNNER_DEBUG enabled
              only in a safe backend environment.
            </p>
          </div>
        </div>
      </div>
    )
  }

  if (state === 'error' && !data) {
    return (
      <div className="nv-dash v2-paper-status">
        <PanelHead refreshing={refreshing} onRefresh={() => void load(true)} refreshDisabled={false} />
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

  return (
    <div className="nv-dash v2-paper-status">
      <PanelHead refreshing={refreshing} onRefresh={() => void load(true)} />
      <FlagStrip data={data} />
      <CountGrid counts={data.status.counts ?? {}} />
      <JobsPanel jobs={data.jobs.jobs ?? data.status.recent_jobs ?? []} />
      <InstancesPanel instances={data.instances.instances ?? []} />
      <FailuresPanel jobs={data.jobs.jobs ?? data.status.recent_jobs ?? []} />
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
        <h1>V2 Paper Status</h1>
        <p><ShieldCheck size={13} /> Read-only internal inspector</p>
      </div>
      <button
        className="secondary-button nv-refresh"
        type="button"
        onClick={onRefresh}
        aria-label="Refresh V2 paper status"
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

function FlagStrip({ data }: { data: V2PaperFanoutBundle }) {
  const flags = data.status.flags ?? {}
  const safety = data.status.safety ?? {}
  return (
    <section className="v2-paper-strip" aria-label="V2 paper flags and safety">
      <Flag label="DEBUG" on={flags.debug_enabled} />
      <Flag label="FANOUT" on={flags.multi_strategy_fanout} />
      <Flag label="PAPER DEBUG" on={flags.v2_paper_runner_debug} />
      <Flag label="READ ONLY" on={safety.read_only !== false} />
      <Flag label="REAL ORDERS" on={safety.real_orders_supported === true} invert />
    </section>
  )
}

function Flag({ label, on, invert = false }: { label: string; on?: boolean; invert?: boolean }) {
  const ok = invert ? !on : !!on
  return (
    <span className={`v2-paper-flag ${ok ? 'ok' : 'warn'}`}>
      <span>{label}</span>
      <strong>{ok ? 'OK' : 'OFF'}</strong>
    </span>
  )
}

function CountGrid({ counts }: { counts: V2PaperFanoutCounts }) {
  const cards = [
    ['Paper instances', counts.paper_instances],
    ['Real order instances', counts.real_order_instances],
    ['V2 pending jobs', counts.v2_pending_jobs],
    ['Completed paper jobs', counts.v2_completed_paper_jobs],
    ['Failed paper jobs', counts.v2_failed_paper_jobs],
    ['Signal-only instances', counts.signal_only_instances],
  ] as const

  return (
    <div className="nv-kpi-grid v2-paper-count-grid">
      {cards.map(([label, value]) => (
        <div className="nv-kpi-card v2-paper-count" key={label}>
          <span className="nv-kpi-label"><Database size={14} /> {label}</span>
          <span className="v2-paper-count-value">{formatCount(value)}</span>
        </div>
      ))}
    </div>
  )
}

function JobsPanel({ jobs }: { jobs: V2PaperFanoutJob[] }) {
  return (
    <section className="nv-panel">
      <div className="nv-panel-head">
        <h2><FlaskConical size={15} /> Recent V2 paper jobs</h2>
        <span className="nv-panel-note">{jobs.length} row{jobs.length === 1 ? '' : 's'}</span>
      </div>
      {jobs.length ? <JobsTable jobs={jobs} /> : <EmptyState label="No recent V2 paper jobs." />}
    </section>
  )
}

function JobsTable({ jobs }: { jobs: V2PaperFanoutJob[] }) {
  return (
    <div className="nv-table-wrap">
      <table className="nv-table v2-paper-table">
        <thead>
          <tr>
            <th>Status</th>
            <th>Mode</th>
            <th>Strategy</th>
            <th>Instance</th>
            <th className="num">Attempts</th>
            <th>Created</th>
            <th>Updated</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job, index) => (
            <tr key={job.id ?? `${job.status}-${index}`}>
              <td><StatusPill status={job.status} /></td>
              <td>{safeText(job.execution_mode)}</td>
              <td className="nv-td-symbol">{safeText(job.strategy_code)}</td>
              <td><code>{shortId(job.instance_id)}</code></td>
              <td className="num">{formatAttempts(job)}</td>
              <td className="nv-td-time">{formatTime(job.created_at)}</td>
              <td className="nv-td-time">{formatTime(job.updated_at)}</td>
              <td className="v2-paper-error-cell">{jobError(job)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function InstancesPanel({ instances }: { instances: V2PaperStrategyInstance[] }) {
  return (
    <section className="nv-panel">
      <div className="nv-panel-head">
        <h2><ShieldCheck size={15} /> Paper strategy instances</h2>
        <span className="nv-panel-note">{instances.length} row{instances.length === 1 ? '' : 's'}</span>
      </div>
      {instances.length ? <InstancesTable instances={instances} /> : <EmptyState label="No paper strategy instances." />}
    </section>
  )
}

function InstancesTable({ instances }: { instances: V2PaperStrategyInstance[] }) {
  return (
    <div className="nv-table-wrap">
      <table className="nv-table v2-paper-table">
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Status</th>
            <th>Mode</th>
            <th>Open</th>
            <th>Position summary</th>
          </tr>
        </thead>
        <tbody>
          {instances.map((instance, index) => (
            <tr key={instance.id ?? `${instance.strategy_code}-${index}`}>
              <td className="nv-td-symbol">{safeText(instance.strategy_code)}</td>
              <td><StatusPill status={instance.status} /></td>
              <td>{safeText(instance.execution_mode)}</td>
              <td>{instance.has_open_position ? 'Yes' : 'No'}</td>
              <td className="v2-paper-summary-cell">{positionSummary(instance.open_position_summary)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function FailuresPanel({ jobs }: { jobs: V2PaperFanoutJob[] }) {
  const failures = useMemo(() => jobs.filter(isFailureJob), [jobs])
  return (
    <section className="nv-panel">
      <div className="nv-panel-head">
        <h2><AlertTriangle size={15} /> Recent failures</h2>
        <span className="nv-panel-note">{failures.length} item{failures.length === 1 ? '' : 's'}</span>
      </div>
      {failures.length ? (
        <div className="v2-paper-failure-list">
          {failures.map((job, index) => (
            <div className="v2-paper-failure-row" key={job.id ?? `${job.status}-${index}`}>
              <StatusPill status={job.status} />
              <span>{safeText(job.strategy_code)}</span>
              <code>{shortId(job.instance_id)}</code>
              <strong>{jobError(job)}</strong>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState label="No recent failed or dead-letter paper jobs." />
      )}
    </section>
  )
}

function StatusPill({ status }: { status?: string | null }) {
  const value = safeText(status)
  return <span className={`v2-paper-status-pill ${statusTone(value)}`}>{value}</span>
}

function EmptyState({ label }: { label: string }) {
  return <div className="nv-chart-empty">{label}</div>
}

function statusTone(status: string): string {
  const lowered = status.toLowerCase()
  if (lowered.includes('completed') || lowered === 'active') return 'ok'
  if (lowered.includes('failed') || lowered.includes('dead')) return 'bad'
  if (lowered.includes('pending') || lowered.includes('locked')) return 'warn'
  return 'muted'
}

function isFailureJob(job: V2PaperFanoutJob): boolean {
  const status = String(job.status ?? '').toLowerCase()
  return Boolean(status.includes('failed') || status.includes('dead') || job.error_code || job.last_error || job.dead_letter_reason)
}

function jobError(job: V2PaperFanoutJob): string {
  return safeText(job.dead_letter_reason ?? job.error_code ?? job.last_error ?? job.result_status)
}

function formatAttempts(job: V2PaperFanoutJob): string {
  const attempts = job.attempts ?? 0
  const max = job.max_attempts
  return max == null ? String(attempts) : `${attempts}/${max}`
}

function positionSummary(summary?: V2PaperOpenPositionSummary): string {
  if (!summary?.has_open_position) return 'No open position'
  const parts = [
    summary.trading_symbol ?? summary.symbol,
    summary.option_side,
    summary.strike,
    summary.expiry,
    summary.qty != null ? `qty ${summary.qty}` : null,
    summary.entry_price != null ? `entry ${summary.entry_price}` : null,
  ].filter((value): value is string | number => value !== null && value !== undefined && value !== '')
  return parts.length ? parts.join(' | ') : 'Open position'
}

function formatCount(value: number | undefined): string {
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
