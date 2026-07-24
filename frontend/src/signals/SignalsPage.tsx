import { AlertTriangle, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { SIGNAL_STATUSES, getSignals, type SignalStatus, type SignalsPage as Page } from './signalsApi'

function statusTone(status: string): string {
  if (status === 'accepted' || status === 'queued') return 'nova-sig-ok'
  if (status === 'rejected') return 'nova-sig-bad'
  return 'nova-sig-neutral'
}

function when(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

export function SignalsPage() {
  const [page, setPage] = useState<Page | null>(null)
  const [status, setStatus] = useState<SignalStatus>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async (nextStatus: SignalStatus) => {
    setLoading(true)
    setError('')
    try {
      setPage(await getSignals({ status: nextStatus, limit: 50 }))
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
    void load(status)
  }, [load, status])

  return (
    <div className="nova-signals">
      <header className="nova-signals-head">
        <div>
          <h1>Signals</h1>
          <p>Every alert this account received, and what the router did with it.</p>
        </div>
        <div className="nova-signals-filters" role="group" aria-label="Filter by status">
          {(['all', ...SIGNAL_STATUSES] as SignalStatus[]).map((s) => (
            <button
              key={s}
              type="button"
              className="conv-pill"
              aria-pressed={status === s}
              onClick={() => setStatus(s)}
            >
              {s}
            </button>
          ))}
        </div>
      </header>

      {page?.counts && Object.keys(page.counts).length > 0 ? (
        <div className="nova-signals-counts">
          {Object.entries(page.counts).map(([k, v]) => (
            <div key={k} className="nova-signals-count">
              <span>{k}</span><strong>{v}</strong>
            </div>
          ))}
          <span className="nova-signals-window">last {page.counts_window_hours ?? 24}h</span>
        </div>
      ) : null}

      {loading ? (
        <p className="nova-signals-state" role="status"><Loader2 size={16} /> Loading signals…</p>
      ) : error ? (
        <p className="nova-signals-state" role="alert"><AlertTriangle size={16} /> {error}
          <button type="button" className="conv-pill" onClick={() => void load(status)}>Retry</button>
        </p>
      ) : !page?.items.length ? (
        <p className="nova-signals-state" role="status">
          No signals recorded yet for this account.
        </p>
      ) : (
        <div className="nova-signals-table-wrap">
          <table className="nova-signals-table">
            <thead>
              <tr>
                <th scope="col">Received</th>
                <th scope="col">Strategy</th>
                <th scope="col">Event</th>
                <th scope="col">Status</th>
                <th scope="col">Signature</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => (
                <tr key={item.id}>
                  <td>{when(item.received_at)}</td>
                  <td>{item.strategy_name ?? '—'}</td>
                  <td className="nova-signals-event">{item.event_id}</td>
                  <td>
                    <span className={statusTone(item.processed_status)}>{item.processed_status}</span>
                    {item.error ? <span className="nova-signals-err"> · {item.error}</span> : null}
                  </td>
                  {/* Non-colour label as well as tone. */}
                  <td>{item.signature_ok ? 'verified' : 'unverified'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
