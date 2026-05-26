import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, BellRing, CheckCircle2, ClipboardList, Database, RefreshCw, Search, ShieldCheck } from 'lucide-react'
import { getLogs } from '../api/dashboard'
import { LogBundle } from '../types'

type LogSource = keyof LogBundle
type RawLog = Record<string, unknown>
type Severity = 'success' | 'warning' | 'danger' | 'info' | 'neutral'
type FilterKey = 'all' | 'webhook' | 'orders' | 'risk' | 'errors' | 'audit'

type ActivityItem = {
  id: string
  source: LogSource
  timestamp: string | null
  title: string
  message: string
  severity: Severity
  signalId: string
  symbol: string
  qty: string
  orderId: string
  status: string
  raw: RawLog
  sortValue: number
}

const logSources: LogSource[] = ['webhook_events', 'order_events', 'audit_events', 'error_events']

const filters: { key: FilterKey; label: string }[] = [
  { key: 'all',     label: 'All'     },
  { key: 'webhook', label: 'Webhook' },
  { key: 'orders',  label: 'Orders'  },
  { key: 'risk',    label: 'Risk'    },
  { key: 'audit',   label: 'Audit'   },
  { key: 'errors',  label: 'Errors'  },
]

const SOURCE_PILL: Record<LogSource, React.CSSProperties> = {
  webhook_events: { background: 'rgba(59,130,246,0.12)', color: '#93c5fd', border: '1px solid rgba(59,130,246,0.25)' },
  order_events:   { background: 'rgba(152,233,77,0.1)',  color: '#98e94d', border: '1px solid rgba(152,233,77,0.22)' },
  audit_events:   { background: '#1b1a17',               color: '#9a968f', border: '1px solid #2b2a26' },
  error_events:   { background: 'rgba(239,68,68,0.1)',   color: '#f87171', border: '1px solid rgba(239,68,68,0.25)' },
}

const SOURCE_LABEL: Record<LogSource, string> = {
  webhook_events: 'WEBHOOK',
  order_events:   'ORDER',
  audit_events:   'AUDIT',
  error_events:   'ERROR',
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function text(value: unknown, fallback = '-'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}

function firstText(row: RawLog, keys: string[], fallback = '-'): string {
  const metadata = asRecord(row.metadata)
  const request  = asRecord(row.request)
  const response = asRecord(row.response)
  for (const key of keys) {
    const value = row[key] ?? metadata[key] ?? request[key] ?? response[key]
    if (value !== null && value !== undefined && value !== '') return text(value)
  }
  return fallback
}

function formatType(value: unknown, fallback: string) {
  const raw = text(value, fallback)
  if (raw === '-') return fallback
  return raw.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
}

function formatTime(value: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleString()
}

function sortTime(value: string | null) {
  if (!value) return 0
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 0 : date.getTime()
}

function isBadStatus(value: string) {
  return /reject|fail|invalid|error|unauthor|blocked/i.test(value)
}

function severityFor(row: RawLog, source: LogSource): Severity {
  const status = firstText(row, ['status', 'phase', 'event_type', 'severity', 'error', 'reason', 'message'], '')
  if (source === 'error_events' || isBadStatus(status)) return /blocked|warning/i.test(status) ? 'warning' : 'danger'
  if (/ENGINE_STOPPED|EMERGENCY|GLOBAL_KILL|ALLOW_ENTRY_DISABLED/i.test(status)) return 'warning'
  if (/placed|success|started|connected|updated|saved|refreshed|enabled|TRADED/i.test(status)) return 'success'
  if (source === 'webhook_events') return 'info'
  return 'neutral'
}

function titleFor(row: RawLog, source: LogSource) {
  const eventType = text(row.event_type, '')
  const phase     = text(row.phase, '')
  const action    = firstText(row, ['action', 'normalized_action'], '')

  if (source === 'webhook_events') {
    if (eventType === 'WEBHOOK_RAW_REQUEST') return 'Raw webhook received'
    if (eventType === 'WEBHOOK_NORMALIZED')  return `${action || 'TradingView'} alert normalized`
    return formatType(eventType, 'Webhook event')
  }
  if (source === 'order_events') {
    if (phase === 'before_request') return `${action || 'Dhan'} order sending`
    if (phase === 'after_response') return `${action || 'Dhan'} order response`
    if (phase === 'blocked')        return `${action || 'Trade'} blocked`
    return formatType(row.status || phase, 'Order event')
  }
  if (source === 'error_events') return formatType(eventType, 'Backend error')
  return formatType(eventType, 'Audit event')
}

function messageFor(row: RawLog, source: LogSource) {
  const interpreted = asRecord(row.interpreted_error)
  const response    = asRecord(row.response)
  const metadata    = asRecord(row.metadata)
  const message =
    row.message ?? row.reason ?? row.error ??
    interpreted.message ?? response.message ?? metadata.message ?? ''

  if (message) return text(message)

  if (source === 'order_events') {
    const status = firstText(row, ['status', 'phase'], '-')
    const symbol = firstText(row, ['trading_symbol', 'normalized_symbol', 'symbol'], '-')
    return `${status} for ${symbol}`
  }
  if (source === 'webhook_events') {
    const symbol = firstText(row, ['normalized_symbol', 'symbol'], '-')
    const qty    = firstText(row, ['normalized_qty', 'qty'], '-')
    return symbol === '-' ? 'TradingView webhook reached the backend.' : `${symbol} alert received with quantity ${qty}.`
  }
  return 'Runtime event recorded.'
}

function normalizeLogs(logs: LogBundle | null): ActivityItem[] {
  if (!logs) return []
  return logSources.flatMap((source) =>
    (logs[source] || []).map((row, index) => {
      const timestamp = text(row.timestamp, '') || null
      const signalId  = firstText(row, ['signal_id'], '-')
      const symbol    = firstText(row, ['trading_symbol', 'normalized_symbol', 'symbol'], '-')
      const qty       = firstText(row, ['qty', 'normalized_qty', 'quantity'], '-')
      const orderId   = firstText(row, ['order_id', 'orderId'], '-')
      const status    = firstText(row, ['status', 'phase', 'severity'], '-')
      return {
        id: `${source}-${timestamp || 'no-time'}-${index}`,
        source,
        timestamp,
        title: titleFor(row, source),
        message: messageFor(row, source),
        severity: severityFor(row, source),
        signalId,
        symbol,
        qty,
        orderId,
        status,
        raw: row,
        sortValue: sortTime(timestamp),
      }
    }),
  )
}

function matchesFilter(item: ActivityItem, filter: FilterKey) {
  const eventType = text(item.raw.event_type, '')
  if (filter === 'all')     return true
  if (filter === 'webhook') return item.source === 'webhook_events'
  if (filter === 'orders')  return item.source === 'order_events'
  if (filter === 'risk')    return /ENGINE|EMERGENCY|GLOBAL|ALLOW_|PANIC|RUNTIME|FRESH|SEEN|RISK/i.test(eventType)
  if (filter === 'errors')  return item.source === 'error_events' || item.severity === 'danger'
  return item.source === 'audit_events'
}

function statusStyle(severity: Severity): React.CSSProperties {
  if (severity === 'success') return { border: '1px solid rgba(152,233,77,0.18)', background: 'rgba(152,233,77,0.05)', color: '#c8f09a' }
  if (severity === 'warning') return { border: '1px solid rgba(245,158,11,0.22)', background: 'rgba(245,158,11,0.05)', color: '#fde68a' }
  if (severity === 'danger')  return { border: '1px solid rgba(239,68,68,0.22)',  background: 'rgba(239,68,68,0.05)',  color: '#fca5a5' }
  if (severity === 'info')    return { border: '1px solid rgba(59,130,246,0.2)',  background: 'rgba(59,130,246,0.05)', color: '#bfdbfe' }
  return { border: '1px solid #222222', background: '#151513', color: '#d8d3c8' }
}

function EventIcon({ severity }: { severity: Severity }) {
  if (severity === 'success') return <CheckCircle2 size={18} />
  if (severity === 'danger' || severity === 'warning') return <AlertTriangle size={18} />
  return <ClipboardList size={18} />
}

function Stat({ label, value, icon }: { label: string; value: number | string; icon: ReactNode }) {
  return (
    <div className="rounded-xl p-4" style={{ border: '1px solid #222222', background: '#151513' }}>
      <div className="flex items-center gap-2" style={{ color: '#9a968f' }}>
        {icon}
        <p className="text-sm">{label}</p>
      </div>
      <p className="mt-2 break-words text-xl font-semibold" style={{ color: '#f4f1ea' }}>{value}</p>
    </div>
  )
}

export default function LogsPage() {
  const [logs, setLogs]     = useState<LogBundle | null>(null)
  const [active, setActive] = useState<FilterKey>('all')
  const [query, setQuery]   = useState('')
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

  const allItems = useMemo(
    () => normalizeLogs(logs).sort((a, b) => b.sortValue - a.sortValue),
    [logs],
  )

  const filteredItems = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return allItems
      .filter((item) => matchesFilter(item, active))
      .filter((item) => {
        if (!needle) return true
        return (
          JSON.stringify(item.raw).toLowerCase().includes(needle) ||
          item.title.toLowerCase().includes(needle) ||
          item.message.toLowerCase().includes(needle)
        )
      })
  }, [active, allItems, query])

  const orderCount  = allItems.filter((item) => item.source === 'order_events').length
  const alertCount  = allItems.filter((item) => item.source === 'webhook_events' && item.raw.event_type === 'WEBHOOK_NORMALIZED').length
  const errorCount  = allItems.filter((item) => item.source === 'error_events' || item.severity === 'danger').length
  const latestTime  = allItems[0]?.timestamp ? formatTime(allItems[0].timestamp) : '-'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold" style={{ color: '#f4f1ea' }}>Activity</h1>
          <p className="mt-1 text-sm" style={{ color: '#9a968f' }}>
            Searchable audit trail for alerts, orders, errors, and engine actions.
          </p>
        </div>
        <button onClick={loadData} className="btn-ghost flex items-center gap-2 self-start">
          <RefreshCw size={16} /> Refresh
        </button>
      </div>

      {/* Stats */}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <Stat label="Latest Event" value={latestTime}   icon={<Database size={16} />} />
        <Stat label="Alerts"       value={alertCount}   icon={<BellRing size={16} />} />
        <Stat label="Orders"       value={orderCount}   icon={<ShieldCheck size={16} />} />
        <Stat label="Errors"       value={errorCount}   icon={<AlertTriangle size={16} />} />
      </section>

      {/* Tab bar + search */}
      <section className="space-y-3">
        <div className="-mb-px" style={{ borderBottom: '1px solid #222222' }}>
          <nav className="flex gap-0">
            {filters.map((filter) => (
              <button
                key={filter.key}
                onClick={() => setActive(filter.key)}
                className="px-4 py-2.5 text-xs font-medium transition-colors border-b-2"
                style={
                  active === filter.key
                    ? { borderBottomColor: '#98e94d', color: '#98e94d' }
                    : { borderBottomColor: 'transparent', color: '#9a968f' }
                }
              >
                {filter.label}
                {filter.key === 'errors' && errorCount > 0 && (
                  <span
                    className="ml-1.5 rounded-full text-xs px-1.5 py-0.5 font-medium"
                    style={{ background: 'rgba(239,68,68,0.15)', color: '#f87171' }}
                  >
                    {errorCount}
                  </span>
                )}
              </button>
            ))}
          </nav>
        </div>

        <label className="relative block w-full lg:max-w-sm">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
            size={15}
            style={{ color: '#77736c' }}
          />
          <input
            className="input text-sm"
            style={{ paddingLeft: '2.25rem' }}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search symbol, order ID, reason…"
          />
        </label>
      </section>

      {/* Log list */}
      <section className="space-y-2.5">
        {loading ? (
          <div
            className="rounded-xl p-5 text-sm"
            style={{ border: '1px solid #222222', background: '#151513', color: '#9a968f' }}
          >
            Loading activity...
          </div>
        ) : filteredItems.length === 0 ? (
          <div
            className="rounded-xl p-5 text-sm"
            style={{ border: '1px solid #222222', background: '#151513', color: '#9a968f' }}
          >
            No activity entries match this view.
          </div>
        ) : (
          filteredItems.map((item) => (
            <article key={item.id} className="rounded-xl p-4" style={statusStyle(item.severity)}>
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="flex min-w-0 gap-3">
                  <div className="mt-0.5 shrink-0">
                    <EventIcon severity={item.severity} />
                  </div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className="rounded-md px-2 py-0.5 text-xs font-semibold tracking-wide"
                        style={SOURCE_PILL[item.source]}
                      >
                        {SOURCE_LABEL[item.source]}
                      </span>
                      <h2 className="text-sm font-semibold">{item.title}</h2>
                    </div>
                    <p className="mt-1 text-sm" style={{ color: '#bcb5aa' }}>{item.message}</p>
                  </div>
                </div>
                <p className="shrink-0 text-xs" style={{ color: '#9a968f' }}>
                  {formatTime(item.timestamp)}
                </p>
              </div>

              {/* Metadata grid */}
              <div className="mt-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-5">
                {(
                  [
                    { label: 'Status',   value: item.status,   mono: false },
                    { label: 'Signal',   value: item.signalId, mono: true  },
                    { label: 'Symbol',   value: item.symbol,   mono: false },
                    { label: 'Qty',      value: item.qty,      mono: false },
                    { label: 'Order ID', value: item.orderId,  mono: true  },
                  ] as { label: string; value: string; mono: boolean }[]
                ).map(({ label, value, mono }) => (
                  <div key={label}>
                    <p style={{ color: '#77736c' }}>{label}</p>
                    <p className={`font-medium ${mono ? 'font-mono text-xs break-all' : ''}`}>{value}</p>
                  </div>
                ))}
              </div>

              {/* Raw event */}
              <details className="mt-4">
                <summary className="cursor-pointer text-sm" style={{ color: '#9a968f' }}>
                  Raw event
                </summary>
                <pre
                  className="mt-3 overflow-x-auto rounded-lg p-3 text-xs"
                  style={{ border: '1px solid #222222', background: '#090908', color: '#bcb5aa' }}
                >
                  {JSON.stringify(item.raw, null, 2)}
                </pre>
              </details>
            </article>
          ))
        )}
      </section>
    </div>
  )
}
