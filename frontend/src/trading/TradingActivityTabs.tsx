import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ServerEvent } from '../types'
import { AutomationsPage } from '../automations/AutomationsPage'
import { getPreferences } from '../settings/settingsApi'
import { notifyForServerEvent } from './browserNotifications'
import {
  acknowledgeHistoricalAlerts,
  getTradingActivity,
  getTradingAlerts,
  getTradingEngineLog,
  getTradingExecutions,
  type TerminalAlerts,
  type TerminalFeed,
  type TerminalRow,
} from './terminalApi'

export type TradingTerminalTab = 'activity' | 'engine' | 'executions' | 'alerts' | 'automation'

const TABS: Array<{ key: TradingTerminalTab; label: string }> = [
  { key: 'activity', label: 'Signal & Order Activity' },
  { key: 'engine', label: 'Engine Log' },
  { key: 'executions', label: 'Executions' },
  { key: 'alerts', label: 'Alerts' },
  { key: 'automation', label: 'Automation Settings' },
]
const TERMINAL_TABS = new Set(TABS.map((item) => item.key))

interface Props {
  mode?: 'paper' | 'live' | null
  runId?: string | null
}

export function TradingActivityTabs({ mode = null, runId = null }: Props) {
  const initial = initialDeepLink()
  const [tab, setTab] = useState<TradingTerminalTab>(initial.tab)
  const [focusedEventId, setFocusedEventId] = useState(initial.eventId)
  const [rows, setRows] = useState<TerminalRow[]>([])
  const [alerts, setAlerts] = useState<TerminalAlerts | null>(null)
  const [selectedActivity, setSelectedActivity] = useState<TerminalRow | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [logPaused, setLogPaused] = useState(false)
  const [notificationStatus, setNotificationStatus] = useState('')
  const cursors = useRef<Partial<Record<TradingTerminalTab, string | null>>>({})
  const notificationPreferences = useRef<Record<string, boolean>>({})
  const tableWrap = useRef<HTMLDivElement | null>(null)

  const load = useCallback(async (
    active: TradingTerminalTab,
    incremental = false,
  ) => {
    if (active === 'automation') {
      setLoading(false)
      return
    }
    if (!incremental) setLoading(true)
    try {
      const options = {
        mode,
        runId,
        cursor: incremental ? cursors.current[active] : null,
        limit: 100,
      }
      let result: TerminalFeed
      if (active === 'alerts') {
        const alertResult = await getTradingAlerts(options)
        setAlerts((current) => mergeAlerts(current, alertResult, incremental))
        result = alertResult
      } else {
        result = active === 'activity'
          ? await getTradingActivity(options)
          : active === 'engine'
            ? await getTradingEngineLog(options)
            : await getTradingExecutions(options)
      }
      setRows((current) => (
        incremental && result.reconciliation_status === 'CURRENT'
          ? mergeRows(current, result.items)
          : result.items
      ))
      cursors.current[active] = result.next_cursor
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Terminal feed unavailable.')
    } finally {
      setLoading(false)
    }
  }, [mode, runId])

  useEffect(() => {
    cursors.current[tab] = null
    // The selected feed is an external server resource; changing tabs must
    // immediately begin reconciliation for that resource.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(tab)
  }, [load, tab])

  useEffect(() => {
    void getPreferences()
      .then((preferences) => {
        notificationPreferences.current = preferences.notification_preferences
      })
      .catch(() => {
        notificationPreferences.current = {}
      })
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => void load(tab, true), 15_000)
    const hydrateDelta = (raw: Event) => {
      const event = (raw as CustomEvent<ServerEvent>).detail
      if (event) {
        const result = notifyForServerEvent(event, notificationPreferences.current)
        if (result === 'unavailable') {
          setNotificationStatus('Browser notification permission is unavailable or denied.')
        }
      }
      void load(tab, true)
    }
    window.addEventListener('nova:terminal-delta', hydrateDelta)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('nova:terminal-delta', hydrateDelta)
    }
  }, [load, tab])

  useEffect(() => {
    if (tab !== 'engine' || logPaused || !tableWrap.current) return
    window.requestAnimationFrame(() => {
      scrollTerminalToLatest(tableWrap.current)
    })
  }, [logPaused, rows, tab])

  useEffect(() => {
    if (!focusedEventId) return
    const match = rows.find((row) => rowMatchesFocus(row, focusedEventId))
    if (match) {
      window.requestAnimationFrame(() => {
        document.querySelector(`[data-terminal-event="${cssEscape(match.id)}"]`)
          ?.scrollIntoView({ block: 'center' })
      })
    }
  }, [focusedEventId, rows, tab])

  const columns = useMemo(() => columnsFor(tab), [tab])
  const drawerActivity = selectedActivity ?? (
    tab === 'activity' && focusedEventId
      ? rows.find((row) => rowMatchesFocus(row, focusedEventId)) ?? null
      : null
  )
  const historicalAlertIds = tab === 'alerts'
    ? (alerts?.historical_items ?? []).filter((row) => !row.acknowledged).map((row) => row.id)
    : []

  function selectTab(next: TradingTerminalTab) {
    cursors.current[next] = null
    setRows([])
    setAlerts(null)
    setSelectedActivity(null)
    setTab(next)
    setFocusedEventId(null)
    updateDeepLink(next, null)
  }

  function onTableScroll() {
    if (tab !== 'engine' || !tableWrap.current) return
    const element = tableWrap.current
    setLogPaused(element.scrollHeight - element.scrollTop - element.clientHeight > 32)
  }

  function jumpToLatest() {
    setLogPaused(false)
    scrollTerminalToLatest(tableWrap.current)
  }

  async function acknowledgeHistorical() {
    try {
      await acknowledgeHistoricalAlerts(historicalAlertIds)
      await load('alerts')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not acknowledge historical alerts.')
    }
  }

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
            onClick={() => selectTab(item.key)}
          >
            {item.label}
            {item.key === 'alerts' && alerts?.unacknowledged_count ? (
              <span className="terminal-alert-count">{alerts.unacknowledged_count}</span>
            ) : null}
          </button>
        ))}
      </div>

      {notificationStatus ? <p className="terminal-notification-state">{notificationStatus}</p> : null}
      {tab === 'automation' ? (
        <div className="terminal-automation"><AutomationsPage /></div>
      ) : loading && rows.length === 0 ? (
        <p className="terminal-feed-state">Loading {TABS.find((item) => item.key === tab)?.label.toLowerCase()}â€¦</p>
      ) : error ? (
        <p className="terminal-feed-state is-error">{error}<button type="button" onClick={() => void load(tab)}>Retry</button></p>
      ) : rows.length === 0 ? (
        <p className="terminal-feed-state">No persisted events yet.</p>
      ) : (
        <>
          {tab === 'alerts' ? (
            <div className="terminal-alert-summary">
              <span>{alerts?.active_items.length ?? 0} active conditions</span>
              <span>{alerts?.historical_items.length ?? 0} historical alerts</span>
              {historicalAlertIds.length ? (
                <button type="button" onClick={() => void acknowledgeHistorical()}>
                  Acknowledge historical
                </button>
              ) : null}
            </div>
          ) : null}
          {tab === 'engine' && logPaused ? (
            <div className="terminal-log-paused">
              Paused while you read history.
              <button type="button" onClick={jumpToLatest}>Jump to latest</button>
            </div>
          ) : null}
          <div className="terminal-table-wrap" ref={tableWrap} onScroll={onTableScroll}>
            <table className="terminal-table">
              <thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.id}
                    data-terminal-event={row.id}
                    className={focusedEventId && rowMatchesFocus(row, focusedEventId) ? 'is-focused' : ''}
                    onClick={() => {
                      if (tab === 'activity') {
                        setSelectedActivity(row)
                        setFocusedEventId(row.id)
                        updateDeepLink(tab, row.id)
                      }
                    }}
                  >
                    {columns.map((column) => (
                      <td key={column.key}>
                        {displayValue(column.key, row[column.key])}
                        {tab === 'executions' && isCopyField(column.key) && row[column.key] ? (
                          <button
                            type="button"
                            className="terminal-copy"
                            aria-label={`Copy ${column.label}`}
                            onClick={(event) => {
                              event.stopPropagation()
                              void navigator.clipboard?.writeText(String(row[column.key]))
                            }}
                          >
                            Copy
                          </button>
                        ) : null}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {drawerActivity ? (
        <aside className="terminal-lifecycle-drawer" aria-label="Activity lifecycle">
          <div>
            <strong>Activity lifecycle</strong>
            <button type="button" onClick={() => setSelectedActivity(null)}>Close</button>
          </div>
          <p>{String(drawerActivity.correlation_id ?? drawerActivity.id)}</p>
          {(drawerActivity.lifecycle ?? []).map((stage, index) => (
            <article key={`${stage.stage}-${stage.occurred_at}-${index}`}>
              <span>{displayValue('occurred_at', stage.occurred_at)}</span>
              <strong>{stage.stage}</strong>
              <small>{stage.message || stage.status || 'Persisted'}</small>
            </article>
          ))}
        </aside>
      ) : null}
    </section>
  )
}

function initialDeepLink(): { tab: TradingTerminalTab; eventId: string | null } {
  const params = new URLSearchParams(window.location.search)
  const requested = params.get('tab') as TradingTerminalTab | null
  return {
    tab: requested && TERMINAL_TABS.has(requested) ? requested : 'activity',
    eventId: params.get('event'),
  }
}

function updateDeepLink(tab: TradingTerminalTab, eventId: string | null): void {
  const url = new URL(window.location.href)
  url.searchParams.set('tab', tab)
  if (eventId) url.searchParams.set('event', eventId)
  else url.searchParams.delete('event')
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}`)
}

function mergeRows(current: TerminalRow[], incoming: TerminalRow[]): TerminalRow[] {
  const merged = new Map(current.map((row) => [row.id, row]))
  incoming.forEach((row) => merged.set(row.id, row))
  return [...merged.values()].sort((left, right) => (
    String(left.occurred_at ?? '').localeCompare(String(right.occurred_at ?? ''))
  )).slice(-300)
}

function mergeAlerts(
  current: TerminalAlerts | null,
  incoming: TerminalAlerts,
  incremental: boolean,
): TerminalAlerts {
  if (!incremental || !current || incoming.reconciliation_status !== 'CURRENT') return incoming
  const historical = mergeRows(
    current.historical_items,
    incoming.historical_items,
  ) as TerminalAlerts['historical_items']
  return {
    ...incoming,
    active_items: incoming.active_items,
    historical_items: historical,
    items: [...incoming.active_items, ...historical],
  }
}

function columnsFor(tab: TradingTerminalTab): Array<{ key: string; label: string }> {
  if (tab === 'engine') return [
    { key: 'occurred_at', label: 'Time' },
    { key: 'level', label: 'Level' },
    { key: 'event_type', label: 'Event' },
    { key: 'message', label: 'Message' },
  ]
  if (tab === 'executions') return [
    { key: 'occurred_at', label: 'Time' },
    { key: 'mode', label: 'Mode' },
    { key: 'instrument', label: 'Instrument' },
    { key: 'action', label: 'Action' },
    { key: 'requested_qty', label: 'Requested' },
    { key: 'filled_qty', label: 'Filled' },
    { key: 'average_price', label: 'Avg fill' },
    { key: 'charges', label: 'Charges' },
    { key: 'slippage', label: 'Slippage' },
    { key: 'status', label: 'Status' },
    { key: 'operation_id', label: 'Operation ID' },
    { key: 'broker_order_id', label: 'Broker ID' },
  ]
  if (tab === 'alerts') return [
    { key: 'occurred_at', label: 'Time' },
    { key: 'severity', label: 'Severity' },
    { key: 'category', label: 'Condition' },
    { key: 'message', label: 'Message' },
    { key: 'active', label: 'Type' },
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

function isCopyField(key: string): boolean {
  return ['operation_id', 'order_id', 'broker_order_id', 'broker_correlation_id'].includes(key)
}

function displayValue(key: string, value: unknown): string {
  if (key === 'occurred_at') {
    if (!value) return 'â€”'
    const date = new Date(String(value))
    return Number.isNaN(date.getTime()) ? 'â€”' : date.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' })
  }
  if (key === 'acknowledged') return value ? 'Acknowledged' : 'New'
  if (key === 'active') return value ? 'Active' : 'Historical'
  if (value === null || value === undefined || value === '') return 'â€”'
  if (typeof value === 'number') return value.toLocaleString('en-IN')
  return String(value)
}

function cssEscape(value: string): string {
  return typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(value) : value.replaceAll('"', '\\"')
}

function rowMatchesFocus(row: TerminalRow, focusId: string): boolean {
  return [
    row.id,
    row.correlation_id,
    row.signal_id,
    row.order_id,
    row.broker_order_id,
    row.operation_id,
  ].some((value) => typeof value === 'string' && value === focusId)
}

function scrollTerminalToLatest(element: HTMLDivElement | null): void {
  if (!element) return
  if (typeof element.scrollTo === 'function') {
    element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
  } else {
    element.scrollTop = element.scrollHeight
  }
}
