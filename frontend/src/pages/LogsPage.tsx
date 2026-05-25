import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, BellRing, CheckCircle2, ClipboardList, Database, RefreshCw, Search, ShieldCheck } from 'lucide-react'
import { getLogs } from '../api/dashboard'
import { LogBundle } from '../types'

type LogSource = keyof LogBundle
type RawLog = Record<string, unknown>
type Severity = 'success' | 'warning' | 'danger' | 'info' | 'neutral'
type FilterKey = 'important' | 'orders' | 'alerts' | 'errors' | 'engine' | 'audit'

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
  { key: 'important', label: 'Important' },
  { key: 'orders', label: 'Orders' },
  { key: 'alerts', label: 'Alerts' },
  { key: 'errors', label: 'Errors' },
  { key: 'engine', label: 'Engine' },
  { key: 'audit', label: 'Audit' },
]

const sourceLabels: Record<LogSource, string> = {
  webhook_events: 'Alert',
  order_events: 'Order',
  audit_events: 'Audit',
  error_events: 'Error',
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
  const request = asRecord(row.request)
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
  return raw
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, (char) => char.toUpperCase())
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
  const phase = text(row.phase, '')
  const action = firstText(row, ['action', 'normalized_action'], '')

  if (source === 'webhook_events') {
    if (eventType === 'WEBHOOK_RAW_REQUEST') return 'Raw webhook received'
    if (eventType === 'WEBHOOK_NORMALIZED') return `${action || 'TradingView'} alert normalized`
    return formatType(eventType, 'Webhook event')
  }

  if (source === 'order_events') {
    if (phase === 'before_request') return `${action || 'Dhan'} order sending`
    if (phase === 'after_response') return `${action || 'Dhan'} order response`
    if (phase === 'blocked') return `${action || 'Trade'} blocked`
    return formatType(row.status || phase, 'Order event')
  }

  if (source === 'error_events') return formatType(eventType, 'Backend error')
  return formatType(eventType, 'Audit event')
}

function messageFor(row: RawLog, source: LogSource) {
  const interpreted = asRecord(row.interpreted_error)
  const response = asRecord(row.response)
  const metadata = asRecord(row.metadata)
  const message =
    row.message ??
    row.reason ??
    row.error ??
    interpreted.message ??
    response.message ??
    metadata.message ??
    ''

  if (message) return text(message)

  if (source === 'order_events') {
    const status = firstText(row, ['status', 'phase'], '-')
    const symbol = firstText(row, ['trading_symbol', 'normalized_symbol', 'symbol'], '-')
    return `${status} for ${symbol}`
  }

  if (source === 'webhook_events') {
    const symbol = firstText(row, ['normalized_symbol', 'symbol'], '-')
    const qty = firstText(row, ['normalized_qty', 'qty'], '-')
    return symbol === '-' ? 'TradingView webhook reached the backend.' : `${symbol} alert received with quantity ${qty}.`
  }

  return 'Runtime event recorded.'
}

function normalizeLogs(logs: LogBundle | null): ActivityItem[] {
  if (!logs) return []
  return logSources.flatMap((source) =>
    (logs[source] || []).map((row, index) => {
      const timestamp = text(row.timestamp, '') || null
      const signalId = firstText(row, ['signal_id'], '-')
      const symbol = firstText(row, ['trading_symbol', 'normalized_symbol', 'symbol'], '-')
      const qty = firstText(row, ['qty', 'normalized_qty', 'quantity'], '-')
      const orderId = firstText(row, ['order_id', 'orderId'], '-')
      const status = firstText(row, ['status', 'phase', 'severity'], '-')
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

function isImportant(item: ActivityItem) {
  const eventType = text(item.raw.event_type, '')
  if (item.source === 'error_events' || item.source === 'order_events') return true
  if (item.source === 'webhook_events') return eventType === 'WEBHOOK_NORMALIZED'
  return /ENGINE|DHAN_|WEBHOOK_SECRET|RISK|EMERGENCY|GLOBAL|ALLOW_|PANIC|BLOCK|FAILED|ERROR|DUPLICATE|SIGNAL|FRESH|SEEN/i.test(eventType)
}

function matchesFilter(item: ActivityItem, filter: FilterKey) {
  const eventType = text(item.raw.event_type, '')
  if (filter === 'important') return isImportant(item)
  if (filter === 'orders') return item.source === 'order_events'
  if (filter === 'alerts') return item.source === 'webhook_events' || /WEBHOOK|SIGNAL/i.test(eventType)
  if (filter === 'errors') return item.source === 'error_events' || item.severity === 'danger'
  if (filter === 'engine') return /ENGINE|EMERGENCY|GLOBAL|ALLOW_|PANIC|RUNTIME|FRESH|SEEN/i.test(eventType)
  return item.source === 'audit_events'
}

function statusClass(severity: Severity) {
  if (severity === 'success') return 'border-emerald-800 bg-emerald-950/30 text-emerald-200'
  if (severity === 'warning') return 'border-amber-800 bg-amber-950/30 text-amber-200'
  if (severity === 'danger') return 'border-red-800 bg-red-950/30 text-red-200'
  if (severity === 'info') return 'border-sky-800 bg-sky-950/30 text-sky-200'
  return 'border-slate-800 bg-slate-900 text-slate-200'
}

function EventIcon({ severity }: { severity: Severity }) {
  if (severity === 'success') return <CheckCircle2 size={18} />
  if (severity === 'danger' || severity === 'warning') return <AlertTriangle size={18} />
  return <ClipboardList size={18} />
}

function Stat({ label, value, icon }: { label: string; value: number | string; icon: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="flex items-center gap-2 text-slate-400">
        {icon}
        <p className="text-sm">{label}</p>
      </div>
      <p className="mt-2 break-words text-xl font-semibold">{value}</p>
    </div>
  )
}

export default function LogsPage() {
  const [logs, setLogs] = useState<LogBundle | null>(null)
  const [active, setActive] = useState<FilterKey>('important')
  const [query, setQuery] = useState('')
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

  const allItems = useMemo(() => normalizeLogs(logs).sort((a, b) => b.sortValue - a.sortValue), [logs])
  const filteredItems = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return allItems
      .filter((item) => matchesFilter(item, active))
      .filter((item) => {
        if (!needle) return true
        return JSON.stringify(item.raw).toLowerCase().includes(needle) || item.title.toLowerCase().includes(needle) || item.message.toLowerCase().includes(needle)
      })
  }, [active, allItems, query])

  const orderCount = allItems.filter((item) => item.source === 'order_events').length
  const alertCount = allItems.filter((item) => item.source === 'webhook_events' && item.raw.event_type === 'WEBHOOK_NORMALIZED').length
  const errorCount = allItems.filter((item) => item.source === 'error_events' || item.severity === 'danger').length
  const latestTime = allItems[0]?.timestamp ? formatTime(allItems[0].timestamp) : '-'

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Activity</h1>
          <p className="mt-1 text-sm text-slate-400">Searchable audit trail for alerts, orders, errors, and engine actions.</p>
        </div>
        <button onClick={loadData} className="btn-ghost flex items-center gap-2 self-start">
          <RefreshCw size={16} /> Refresh
        </button>
      </div>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <Stat label="Latest Event" value={latestTime} icon={<Database size={16} />} />
        <Stat label="Alerts" value={alertCount} icon={<BellRing size={16} />} />
        <Stat label="Orders" value={orderCount} icon={<ShieldCheck size={16} />} />
        <Stat label="Errors" value={errorCount} icon={<AlertTriangle size={16} />} />
      </section>

      <section className="space-y-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-2">
            {filters.map((filter) => (
              <button
                key={filter.key}
                onClick={() => setActive(filter.key)}
                className={active === filter.key ? 'btn-primary text-sm' : 'btn-ghost text-sm'}
              >
                {filter.label}
              </button>
            ))}
          </div>
          <label className="relative w-full lg:max-w-sm">
            <Search className="pointer-events-none absolute left-3 top-2.5 text-slate-500" size={18} />
            <input
              className="input pl-10"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search symbol, order ID, reason"
            />
          </label>
        </div>
      </section>

      <section className="space-y-3">
        {loading ? (
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-5 text-sm text-slate-400">Loading activity...</div>
        ) : filteredItems.length === 0 ? (
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-5 text-sm text-slate-400">No activity entries match this view.</div>
        ) : (
          filteredItems.map((item) => (
            <article key={item.id} className={`rounded-lg border p-4 ${statusClass(item.severity)}`}>
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="flex min-w-0 gap-3">
                  <div className="mt-0.5 shrink-0">
                    <EventIcon severity={item.severity} />
                  </div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-base font-semibold">{item.title}</h2>
                      <span className="rounded-full border border-slate-700 bg-slate-950/70 px-2 py-0.5 text-xs text-slate-300">
                        {sourceLabels[item.source]}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-slate-300">{item.message}</p>
                  </div>
                </div>
                <p className="shrink-0 text-xs text-slate-400">{formatTime(item.timestamp)}</p>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-5">
                <div>
                  <p className="text-slate-500">Status</p>
                  <p className="font-medium">{item.status}</p>
                </div>
                <div>
                  <p className="text-slate-500">Signal</p>
                  <p className="break-all font-mono text-xs">{item.signalId}</p>
                </div>
                <div>
                  <p className="text-slate-500">Symbol</p>
                  <p>{item.symbol}</p>
                </div>
                <div>
                  <p className="text-slate-500">Qty</p>
                  <p>{item.qty}</p>
                </div>
                <div>
                  <p className="text-slate-500">Order ID</p>
                  <p className="break-all font-mono text-xs">{item.orderId}</p>
                </div>
              </div>

              <details className="mt-4">
                <summary className="cursor-pointer text-sm text-slate-400">Raw event</summary>
                <pre className="mt-3 overflow-x-auto rounded-md border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300">
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
