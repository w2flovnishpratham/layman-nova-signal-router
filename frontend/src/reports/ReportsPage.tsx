import { AlertTriangle, Download, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getReport, reportCsvUrl, rupees, type Metric, type Report } from './reportsApi'

function istToday(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
}

function daysAgo(days: number): string {
  const d = new Date(Date.now() - days * 86_400_000)
  return d.toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
}

/** Renders the value, or the server's reason why there isn't one. */
function Stat({ label, metric, format }: { label: string; metric: Metric; format: (v: number) => string }) {
  return (
    <div className="nova-report-stat">
      <span>{label}</span>
      {metric.value === null ? (
        <strong className="nova-report-unknown" title={metric.reason ?? undefined}>Not available</strong>
      ) : (
        <strong>{format(metric.value)}</strong>
      )}
      {metric.value === null && metric.reason ? <small>{metric.reason}</small> : null}
    </div>
  )
}

export function ReportsPage() {
  const [start, setStart] = useState(daysAgo(29))
  const [end, setEnd] = useState(istToday())
  const [data, setData] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await getReport(start, end))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the report.')
    } finally {
      setLoading(false)
    }
  }, [start, end])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  return (
    <div className="nova-signals">
      <header className="nova-signals-head">
        <div>
          <h1>Reports</h1>
          <p>Paper performance computed from closed trades NOVA tracked end to end.</p>
        </div>
        <div className="nova-report-range">
          <button type="button" className="conv-pill" onClick={() => { setStart(istToday()); setEnd(istToday()) }}>Today</button>
          <button type="button" className="conv-pill" onClick={() => { setStart(daysAgo(29)); setEnd(istToday()) }}>Last 30 days</button>
          <label className="sr-only" htmlFor="report-start">Start date</label>
          <input id="report-start" type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
          <label className="sr-only" htmlFor="report-end">End date</label>
          <input id="report-end" type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
          <a className="conv-pill" href={reportCsvUrl(start, end)} download>
            <Download size={13} /> CSV
          </a>
        </div>
      </header>

      {loading ? (
        <p className="nova-signals-state" role="status"><Loader2 size={16} /> Loading report…</p>
      ) : error ? (
        <p className="nova-signals-state" role="alert">
          <AlertTriangle size={16} /> {error}
          <button type="button" className="conv-pill" onClick={() => void load()}>Retry</button>
        </p>
      ) : !data ? null : data.totals.trades === 0 ? (
        <p className="nova-signals-state" role="status">
          No closed trades between {data.period.start} and {data.period.end}.
        </p>
      ) : (
        <>
          <section className="nova-report-stats" aria-label="Summary">
            <div className="nova-report-stat">
              <span>Net P&amp;L</span>
              <strong className={data.totals.net_pnl >= 0 ? 'nova-sig-ok' : 'nova-sig-bad'}>
                {rupees(data.totals.net_pnl)}
              </strong>
            </div>
            <div className="nova-report-stat"><span>Trades</span><strong>{data.totals.trades}</strong></div>
            <div className="nova-report-stat"><span>Winners</span><strong>{data.totals.winning_trades}</strong></div>
            <div className="nova-report-stat"><span>Losers</span><strong>{data.totals.losing_trades}</strong></div>
            <Stat label="Win rate" metric={data.win_rate} format={(v) => `${v}%`} />
            <Stat label="Profit factor" metric={data.profit_factor} format={(v) => v.toFixed(2)} />
            <Stat label="Average winner" metric={data.average_winner} format={rupees} />
            <Stat label="Average loser" metric={data.average_loser} format={rupees} />
            <Stat label="Max drawdown" metric={data.max_drawdown} format={rupees} />
            <div className="nova-report-stat"><span>Gross profit</span><strong>{rupees(data.totals.gross_profit)}</strong></div>
            <div className="nova-report-stat"><span>Gross loss</span><strong>{rupees(data.totals.gross_loss)}</strong></div>
          </section>

          <p className="nova-risk-note">
            Max drawdown is measured on closed-trade equity — the running sum of realised
            P&amp;L. NOVA does not store an intraday equity curve, so none is shown.
          </p>

          <div className="nova-sig-table-wrap">
            <table className="nova-sig-table">
              <caption className="sr-only">Closed trades</caption>
              <thead>
                <tr>
                  <th scope="col">Closed</th>
                  <th scope="col">Symbol</th>
                  <th scope="col">Side</th>
                  <th scope="col">Qty</th>
                  <th scope="col">Entry</th>
                  <th scope="col">Exit</th>
                  <th scope="col">Realised</th>
                </tr>
              </thead>
              <tbody>
                {data.trades.map((trade, i) => (
                  <tr key={`${trade.closed_at}-${i}`}>
                    <td>{trade.closed_at ? new Date(trade.closed_at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) : '—'}</td>
                    <td>{trade.symbol ?? '—'}</td>
                    <td>{trade.option_side ?? '—'}</td>
                    <td>{trade.qty}</td>
                    <td>{trade.entry_price ?? '—'}</td>
                    <td>{trade.exit_price ?? '—'}</td>
                    <td className={trade.realized_pnl >= 0 ? 'nova-sig-ok' : 'nova-sig-bad'}>{rupees(trade.realized_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
