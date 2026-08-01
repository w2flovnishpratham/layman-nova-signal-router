import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Button } from '@/components/ui/button'
import { AlertTriangle, ChevronLeft, ChevronRight } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { PageSkeleton } from '../components/PageSkeleton'
import { SIGNAL_STATUSES, getSignals, type SignalStatus, type SignalsPage as Page } from './signalsApi'

const PAGE_SIZE = 10

function statusTone(status: string): string {
  if (status === 'received') return 'nova-sig-info'
  if (status === 'queued') return 'nova-sig-warn'
  if (status === 'accepted' || status === 'fanned_out') return 'nova-sig-ok'
  if (status === 'rejected') return 'nova-sig-bad'
  return 'nova-sig-neutral'
}

function when(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function summaryText(summary: Record<string, unknown>, key: string): string {
  const value = summary[key]
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

function actionLabel(summary: Record<string, unknown>): string {
  const action = summaryText(summary, 'action').toUpperCase()
  const side = summaryText(summary, 'side').toUpperCase()
  const optionSide = summaryText(summary, 'option_side').toUpperCase()
  if (action === 'ENTRY' && side) return `${side}${optionSide ? ` ${optionSide}` : ''}`
  return action || side || '—'
}

function actionTone(action: string): string {
  if (action.startsWith('BUY') || action === 'ENTRY') return 'is-buy'
  if (action.startsWith('SELL')) return 'is-sell'
  if (action === 'EXIT') return 'is-exit'
  return 'is-neutral'
}

function instrumentLabel(summary: Record<string, unknown>): string {
  const parts = ['symbol', 'expiry', 'strike', 'option_side']
    .map((key) => summaryText(summary, key))
    .filter(Boolean)
  return parts.join(' ') || '—'
}

function providerLabel(provider: string): string {
  if (provider === 'tradingview') return 'TV'
  if (provider === 'user') return 'USER'
  return provider.slice(0, 6).toUpperCase()
}

export function SignalsPage() {
  const [page, setPage] = useState<Page | null>(null)
  const [status, setStatus] = useState<SignalStatus>('all')
  const [pageIndex, setPageIndex] = useState(0)
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const cursor = cursors[pageIndex] ?? null

  const load = useCallback(async (nextStatus: SignalStatus, nextCursor: string | null) => {
    setLoading(true)
    setError('')
    try {
      setPage(await getSignals({ status: nextStatus, cursor: nextCursor, limit: PAGE_SIZE }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load signals.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // Fetch on mount and whenever the status filter changes. `load` is async and
    // only settles state around an await, so no synchronous cascade occurs.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(status, cursor)
  }, [cursor, load, status])

  function selectStatus(nextStatus: SignalStatus) {
    setStatus(nextStatus)
    setPageIndex(0)
    setCursors([null])
  }

  function nextPage() {
    if (!page?.next_cursor) return
    setCursors((current) => [...current.slice(0, pageIndex + 1), page.next_cursor])
    setPageIndex((current) => current + 1)
  }

  const counts = page?.counts ?? {}
  const total = Number(counts.total ?? 0)
  const processed = Number(counts.accepted ?? 0) + Number(counts.queued ?? 0) + Number(counts.fanned_out ?? 0)
  const rejected = Number(counts.rejected ?? 0)
  const filteredTotal = status === 'all' ? total : Number(counts[status] ?? 0)
  const shownFrom = page?.items.length ? pageIndex * PAGE_SIZE + 1 : 0
  const shownTo = pageIndex * PAGE_SIZE + (page?.items.length ?? 0)

  return (
    <div className="nova-signals nova-signals-page">
      <header className="nova-signals-head">
        <div>
          <h1>Signals</h1>
          <p>Every alert this account received, and what the router did with it.</p>
        </div>
      </header>

      {page?.counts && Object.keys(page.counts).length > 0 ? (
        <div className="nova-signals-summary" aria-label="Signal summary">
          <article><span>Received ({page.counts_window_hours ?? 24}h)</span><strong>{total}</strong></article>
          <article className="is-positive"><span>Processed</span><strong>{processed}</strong></article>
          <article className="is-negative"><span>Rejected</span><strong>{rejected}</strong></article>
        </div>
      ) : null}

      {loading ? (
        <PageSkeleton label="Loading signals" variant="table" />
      ) : error ? (
        <p className="nova-signals-state" role="alert"><AlertTriangle size={16} /> {error}
          <Button variant="unstyled" type="button" className="conv-pill" onClick={() => void load(status, cursor)}>Retry</Button>
        </p>
      ) : page && !page.available ? (
        <p className="nova-signals-state" role="status">Signal history unavailable — database not configured.</p>
      ) : page ? (
        <section className="nova-signals-panel" aria-label="Signal records">
          <div className="nova-signals-tabs" role="group" aria-label="Filter by status">
            {(['all', ...SIGNAL_STATUSES] as SignalStatus[]).map((s) => (
              <Button
                variant="unstyled"
                key={s}
                type="button"
                aria-pressed={status === s}
                onClick={() => selectStatus(s)}
              >
                {s}
              </Button>
            ))}
          </div>

          {page.items.length ? <>
            <div className="nova-signals-table-scroll">
              <Table variant="unstyled" className="nova-signals-table nova-signals-rich-table">
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">Time</TableHead>
                  <TableHead scope="col">Strategy</TableHead>
                  <TableHead scope="col">Alert</TableHead>
                  <TableHead scope="col">Action</TableHead>
                  <TableHead scope="col">Instrument</TableHead>
                  <TableHead scope="col">Status</TableHead>
                  <TableHead scope="col">Signature</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page.items.map((item) => {
                  const action = actionLabel(item.summary)
                  return (
                    <TableRow key={item.id}>
                      <TableCell className="nova-signals-time">{when(item.received_at)}</TableCell>
                      <TableCell>
                        <div className="nova-signals-strategy">
                          <span>{providerLabel(item.provider)}</span>
                          <strong title={item.strategy_name ?? undefined}>{item.strategy_name ?? '—'}</strong>
                        </div>
                      </TableCell>
                      <TableCell className="nova-signals-event">{summaryText(item.summary, 'alert') || item.event_id}</TableCell>
                      <TableCell><span className={`nova-signals-action ${actionTone(action)}`}>{action}</span></TableCell>
                      <TableCell className="nova-signals-instrument">{instrumentLabel(item.summary)}</TableCell>
                      <TableCell>
                        <span className={`nova-signals-status ${statusTone(item.processed_status)}`}>{item.processed_status}</span>
                        {item.error ? <span className="nova-signals-err"> · {item.error}</span> : null}
                      </TableCell>
                      <TableCell>
                        <span className={item.signature_ok ? 'nova-sig-ok' : 'nova-sig-bad'}>
                          {item.signature_ok ? 'verified' : 'unverified'}
                        </span>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
              </Table>
            </div>

            <footer className="nova-signals-pagination">
              <span>
                Showing {shownFrom}–{shownTo}{filteredTotal ? ` of ${filteredTotal}` : ''} signals
              </span>
              <div>
                <Button
                  variant="unstyled"
                  type="button"
                  aria-label="Previous page"
                  disabled={loading || pageIndex === 0}
                  onClick={() => setPageIndex((current) => Math.max(0, current - 1))}
                >
                  <ChevronLeft size={14} />
                </Button>
                <span aria-current="page">{pageIndex + 1}</span>
                <Button
                  variant="unstyled"
                  type="button"
                  aria-label="Next page"
                  disabled={loading || !page.next_cursor}
                  onClick={nextPage}
                >
                  <ChevronRight size={14} />
                </Button>
              </div>
            </footer>
          </> : (
            <p className="nova-signals-state" role="status">
              No signals recorded yet for this account.
            </p>
          )}
        </section>
      ) : null}
    </div>
  )
}
