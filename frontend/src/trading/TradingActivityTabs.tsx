import { useCallback, useEffect, useMemo, useState } from 'react'
import { AutomationsPage } from '../automations/AutomationsPage'
import {
  acknowledgeVisibleAlerts,
  getTradingActivity,
  getTradingAlerts,
  getTradingEngineLog,
  getTradingExecutions,
  type TerminalAlerts,
  type TerminalRow,
} from './terminalApi'

type Tab = 'activity' | 'engine' | 'executions' | 'alerts' | 'automation'

const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'activity', label: 'Signal & Order Activity' },
  { key: 'engine', label: 'Engine Log' },
  { key: 'executions', label: 'Executions' },
  { key: 'alerts', label: 'Alerts' },
  { key: 'automation', label: 'Automation Settings' },
]

export function TradingActivityTabs() {
  const [tab, setTab] = useState<Tab>('activity')
  const [rows, setRows] = useState<TerminalRow[]>([])
  const [alerts, setAlerts] = useState<TerminalAlerts | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (active: Tab) => {
    if (active === 'automation') {
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      if (active === 'alerts') {
        const result = await getTradingAlerts()
        setAlerts(result)
        setRows(result.items)
      } else {
        const result = active === 'activity'
          ? await getTradingActivity()
          : active === 'engine'
            ? await getTradingEngineLog()
            : await getTradingExecutions()
        setRows(result.items)
      }
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Terminal feed unavailable.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(tab)
    const timer = window.setInterval(() => void load(tab), 15_000)
    const hydrateDelta = () => void load(tab)
    window.addEventListener('nova:terminal-delta', hydrateDelta)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('nova:terminal-delta', hydrateDelta)
    }
  }, [load, tab])

  const columns = useMemo(() => columnsFor(tab), [tab])
  const visibleAlertIds = tab === 'alerts'
    ? rows.filter((row) => !row.acknowledged).map((row) => row.id)
    : []

  return (
    <section className="terminal-feed">
      <div className="terminal-tabs" role="tablist" aria-label="Trading terminal views">
        {TABS.map((item) => (
          <button
            type="button"
            role="tab"
            key={item.key}
            aria-selected={tab === item.key}
            className={tab === item.key ? 'is-active' : ''}
            onClick={() => setTab(item.key)}
          >
            {item.label}
            {item.key === 'alerts' && alerts?.unacknowledged_count ? (
              <span className="terminal-alert-count">{alerts.unacknowledged_count}</span>
            ) : null}
          </button>
        ))}
      </div>

      {tab === 'automation' ? (
        <div className="terminal-automation"><AutomationsPage /></div>
      ) : loading && rows.length === 0 ? (
        <p className="terminal-feed-state">Loading {TABS.find((item) => item.key === tab)?.label.toLowerCase()}…</p>
      ) : error ? (
        <p className="terminal-feed-state is-error">{error}<button type="button" onClick={() => void load(tab)}>Retry</button></p>
      ) : rows.length === 0 ? (
        <p className="terminal-feed-state">No persisted events yet.</p>
      ) : (
        <>
          {tab === 'alerts' && visibleAlertIds.length ? (
            <div className="terminal-alert-actions">
              <button type="button" onClick={() => void acknowledgeVisibleAlerts(visibleAlertIds).then(() => load('alerts'))}>
                Acknowledge visible
              </button>
            </div>
          ) : null}
          <div className="terminal-table-wrap">
            <table className="terminal-table">
              <thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    {columns.map((column) => <td key={column.key}>{displayValue(column.key, row[column.key])}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

function columnsFor(tab: Tab): Array<{ key: string; label: string }> {
  if (tab === 'engine') return [
    { key: 'occurred_at', label: 'Time' },
    { key: 'level', label: 'Level' },
    { key: 'event_type', label: 'Event' },
    { key: 'message', label: 'Message' },
  ]
  if (tab === 'executions') return [
    { key: 'occurred_at', label: 'Time' },
    { key: 'source', label: 'Source' },
    { key: 'instrument', label: 'Instrument' },
    { key: 'action', label: 'Action' },
    { key: 'qty', label: 'Qty' },
    { key: 'average_price', label: 'Price' },
    { key: 'status', label: 'Status' },
    { key: 'pnl', label: 'P&L' },
  ]
  if (tab === 'alerts') return [
    { key: 'occurred_at', label: 'Time' },
    { key: 'severity', label: 'Severity' },
    { key: 'category', label: 'Condition' },
    { key: 'message', label: 'Message' },
    { key: 'acknowledged', label: 'State' },
  ]
  return [
    { key: 'occurred_at', label: 'Time' },
    { key: 'strategy', label: 'Strategy' },
    { key: 'signal', label: 'Signal' },
    { key: 'instrument', label: 'Instrument' },
    { key: 'source', label: 'Source' },
    { key: 'lots', label: 'Lots / Qty' },
    { key: 'price', label: 'Price' },
    { key: 'status', label: 'Status' },
    { key: 'pnl', label: 'P&L' },
  ]
}

function displayValue(key: string, value: unknown): string {
  if (key === 'occurred_at') {
    if (!value) return '—'
    const date = new Date(String(value))
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' })
  }
  if (key === 'acknowledged') return value ? 'Acknowledged' : 'New'
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return value.toLocaleString('en-IN')
  return String(value)
}
