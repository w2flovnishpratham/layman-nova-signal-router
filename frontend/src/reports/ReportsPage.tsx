import { Button } from '@/components/ui/button'
import { NativeSelect } from '@/components/ui/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Skeleton } from '@/components/ui/skeleton'
import { AlertTriangle, ChevronRight, Download, TrendingDown, TrendingUp } from 'lucide-react'
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import {
  getReport,
  reportExportUrl,
  rupees,
  type DailySession,
  type Report,
  type ReportMode,
  type TradeDetail,
  type TradeOrigin,
} from './reportsApi'

const WEEKDAYS = ['M', 'T', 'W', 'T', 'F', 'S', 'S']

function currentIstMonth(): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
  }).formatToParts(new Date())
  const year = parts.find((part) => part.type === 'year')?.value
  const month = parts.find((part) => part.type === 'month')?.value
  return `${year}-${month}`
}

function monthBounds(month: string): { start: string; end: string } {
  const [year, monthNumber] = month.split('-').map(Number)
  const endDay = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate()
  return {
    start: `${month}-01`,
    end: `${month}-${String(endDay).padStart(2, '0')}`,
  }
}

function monthLabel(month: string): string {
  const [year, monthNumber] = month.split('-').map(Number)
  return new Intl.DateTimeFormat('en-IN', { month: 'long', year: 'numeric', timeZone: 'UTC' })
    .format(new Date(Date.UTC(year, monthNumber - 1, 1)))
}

function monthOptions(): string[] {
  const [year, monthNumber] = currentIstMonth().split('-').map(Number)
  return Array.from({ length: 25 }, (_, index) => {
    const date = new Date(Date.UTC(year, monthNumber - 1 - index, 1))
    return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`
  })
}

function formatSessionDate(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: 'short', timeZone: 'UTC' })
    .format(new Date(`${value}T12:00:00Z`))
}

function metricText(value: number | null, suffix = ''): string {
  return value === null ? '—' : `${value}${suffix}`
}

function drawdownText(value: number | null): string {
  if (value === null) return '—'
  return value > 0 ? `-${rupees(value)}` : rupees(0)
}

function Segment<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: Array<{ value: T; label: string }>
  onChange: (value: T) => void
}) {
  return (
    <div className="nova-report-segment" role="group" aria-label={label}>
      {options.map((option) => (
        <Button
          variant="unstyled"
          type="button"
          key={option.value}
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </Button>
      ))}
    </div>
  )
}

function Calendar({
  month,
  sessions,
}: {
  month: string
  sessions: DailySession[]
}) {
  const [year, monthNumber] = month.split('-').map(Number)
  const dayCount = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate()
  const leading = (new Date(Date.UTC(year, monthNumber - 1, 1)).getUTCDay() + 6) % 7
  const sessionByDate = new Map(sessions.map((session) => [session.date, session]))
  const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
  const cells = Array.from({ length: Math.ceil((leading + dayCount) / 7) * 7 }, (_, index) => index - leading + 1)

  return (
    <TooltipProvider delay={120}>
      <div className="nova-pnl-calendar">
        <div className="nova-pnl-weekdays">
          {WEEKDAYS.map((day, index) => <span key={`${day}-${index}`}>{day}</span>)}
        </div>
        <div className="nova-pnl-days">
          {cells.map((day, index) => {
            if (day < 1 || day > dayCount) return <span className="is-empty" key={`empty-${index}`} />
            const date = `${month}-${String(day).padStart(2, '0')}`
            const session = sessionByDate.get(date)
            const state = !session ? 'neutral' : session.net_pnl > 0 ? 'profit' : session.net_pnl < 0 ? 'loss' : 'neutral'
            const description = session
              ? `${formatSessionDate(date)}. ${rupees(session.net_pnl, true)} realized. ${session.trades} closed trades, ${session.wins} wins, ${session.losses} losses. ${session.strategy_mix.join(', ')}. ${session.mode}.`
              : `${formatSessionDate(date)}. No closed trades.`
            return (
              <Tooltip key={date}>
                <TooltipTrigger render={(
                  <div
                    className={`nova-pnl-day is-${state}${date === today ? ' is-today' : ''}`}
                    aria-label={description}
                    tabIndex={session ? 0 : -1}
                  >
                    <span>{day}</span>
                    {session ? <small>{rupees(session.net_pnl, true)}</small> : null}
                  </div>
                )} />
                <TooltipContent className={`nova-pnl-tooltip is-${state}`} side="top" sideOffset={8}>
                  <div className="nova-pnl-tooltip-head">
                    <strong>{formatSessionDate(date)}</strong>
                    {session ? <span>{session.mode}</span> : null}
                  </div>
                  {session ? (
                    <>
                      <div className="nova-pnl-tooltip-value">
                        {session.net_pnl >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
                        <strong>{rupees(session.net_pnl, true)}</strong>
                        <span>realized</span>
                      </div>
                      <div className="nova-pnl-tooltip-stats">
                        <span><strong>{session.trades}</strong>Trades</span>
                        <span><strong>{session.wins}</strong>Wins</span>
                        <span><strong>{session.losses}</strong>Losses</span>
                      </div>
                      <p>{session.strategy_mix.join(' · ')}</p>
                    </>
                  ) : (
                    <p className="is-empty">No closed trades</p>
                  )}
                </TooltipContent>
              </Tooltip>
            )
          })}
        </div>
      </div>
    </TooltipProvider>
  )
}

const REPORT_COLUMNS = ['Date', 'Strategy Mix', 'Trades', 'Win Rate', 'Max DD', 'Net P&L', 'Mode']

function istTime(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Asia/Kolkata',
  })
}

function money(value: number | null | undefined, signed = false): string {
  if (value == null) return '—'
  return rupees(value, signed)
}

/** STOP_LOSS -> "Stop loss", EOD_SQUAREOFF -> "Eod squareoff". */
function humanLabel(value: string | null): string {
  if (!value) return '—'
  const spaced = value.replaceAll('_', ' ').toLowerCase()
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

function TradeDetailTable({ trades }: { trades: TradeDetail[] }) {
  if (!trades.length) return <p className="nova-trade-detail-empty">No closed trades recorded for this session.</p>
  return (
    <div className="nova-trade-detail">
      <div className="nova-trade-detail-head">
        <h3>Trades <span>{trades.length}</span></h3>
      </div>
      <div className="nova-trade-detail-scroll">
        <Table variant="unstyled" className="nova-trade-table">
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Contract</TableHead>
              <TableHead className="num">Qty</TableHead>
              <TableHead className="num">Buy</TableHead>
              <TableHead className="num">Sell</TableHead>
              <TableHead className="num">Charges</TableHead>
              <TableHead className="num">Net P&amp;L</TableHead>
              <TableHead className="num">%</TableHead>
              <TableHead>Exit reason</TableHead>
              <TableHead>Source</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {trades.map((trade) => (
              <TableRow key={trade.id} className={`nova-trade-row is-${trade.result}`}>
                <TableCell className="nova-trade-time">
                  <strong>{istTime(trade.closed_at)}</strong>
                  <small>in {istTime(trade.opened_at)}</small>
                </TableCell>
                <TableCell className="nova-trade-contract">
                  <strong>{trade.symbol ?? '—'}</strong>
                  {trade.strategy ? <small>{trade.strategy}</small> : null}
                </TableCell>
                <TableCell className="num">{trade.qty || '—'}</TableCell>
                <TableCell className="num">{money(trade.entry_price)}</TableCell>
                <TableCell className="num">{money(trade.exit_price)}</TableCell>
                <TableCell className="num">{money(trade.charges)}</TableCell>
                <TableCell className={`num ${(trade.realized_pnl ?? 0) >= 0 ? 'nova-sig-ok' : 'nova-sig-bad'}`}>
                  {money(trade.realized_pnl, true)}
                </TableCell>
                <TableCell className={`num ${(trade.pnl_pct ?? 0) >= 0 ? 'nova-sig-ok' : 'nova-sig-bad'}`}>
                  {trade.pnl_pct == null ? '—' : `${trade.pnl_pct > 0 ? '+' : ''}${trade.pnl_pct.toFixed(2)}%`}
                </TableCell>
                <TableCell><span className="nova-trade-tag">{humanLabel(trade.exit_trigger)}</span></TableCell>
                <TableCell><span className="nova-trade-tag is-muted">{humanLabel(trade.origin)}</span></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

/** Loading state for the report body, shaped like the same page once filled.
 *
 * Everything that is page furniture rather than fetched data — the stat
 * captions, the panel headings, the table's column headers, the calendar's
 * weekday row, the legend — renders for real immediately. Only the values
 * still in flight are placeholders, so nothing shifts when the data lands
 * and the page never misrepresents what it is about to show. */
function ReportBodySkeleton({ month }: { month: string }) {
  const monthShort = monthLabel(month).split(' ')[0]
  return (
    <div role="status" aria-busy="true" aria-label="Loading report">
      <section className="nova-report-stats" aria-label="Monthly summary">
        {[`Net P&L (${monthShort})`, 'Sessions', 'Win Rate', 'Profit Factor', 'Max Drawdown'].map((caption) => (
          <div className="nova-report-stat" key={caption}>
            <span>{caption}</span>
            <Skeleton className="mt-1 h-5 w-24" />
          </div>
        ))}
      </section>

      <div className="nova-report-layout">
        <section className="nova-report-panel nova-report-sessions">
          <div className="nova-report-panel-head">
            <h2>Daily Session Reports</h2>
            <span>{monthLabel(month)}</span>
          </div>
          <div className="nova-report-table-wrap">
            <Table variant="unstyled" className="nova-report-table">
              <TableHeader>
                <TableRow>
                  {REPORT_COLUMNS.map((column) => <TableHead key={column}>{column}</TableHead>)}
                </TableRow>
              </TableHeader>
              <TableBody>
                {Array.from({ length: 6 }, (_, row) => (
                  <TableRow key={row}>
                    {REPORT_COLUMNS.map((column) => (
                      <TableCell key={column}>
                        <Skeleton className="h-3.5" style={{ width: column === 'Strategy Mix' ? '9rem' : '3.5rem' }} />
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>

        <aside className="nova-report-aside">
          <section className="nova-report-panel">
            <div className="nova-report-panel-head"><h2>P&amp;L Calendar — {monthShort}</h2></div>
            <div className="nova-pnl-calendar">
              <div className="nova-pnl-weekdays">
                {WEEKDAYS.map((day, index) => <span key={`${day}-${index}`}>{day}</span>)}
              </div>
              <div className="nova-pnl-days">
                {Array.from({ length: 35 }, (_, index) => (
                  <Skeleton key={index} className="rounded-[7px]" style={{ aspectRatio: '1 / 1' }} />
                ))}
              </div>
            </div>
            <div className="nova-pnl-legend">
              <span className="is-loss" /> Loss <span className="is-neutral" /> No closed trades <span className="is-profit" /> Profit
            </div>
          </section>

          <section className="nova-report-panel">
            <div className="nova-report-panel-head"><h2>By Strategy — {monthShort}</h2></div>
            <div className="nova-report-strategies">
              {Array.from({ length: 3 }, (_, index) => (
                <div className="nova-report-strategy" key={index}>
                  <span><Skeleton className="h-3.5 w-28" /></span>
                  <div><i style={{ width: '0%' }} /></div>
                </div>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </div>
  )
}

export function ReportsPage({ initialMode }: { initialMode?: ReportMode | null }) {
  const search = new URLSearchParams(window.location.search)
  const [month, setMonth] = useState(search.get('month') ?? currentIstMonth())
  const [mode, setMode] = useState<ReportMode>(
    search.get('mode') === 'live' ? 'live' : initialMode === 'live' ? 'live' : 'paper',
  )
  const [origin, setOrigin] = useState<TradeOrigin>(
    search.get('origin') === 'manual' ? 'manual' : search.get('origin') === 'automated' ? 'automated' : 'all',
  )
  const [data, setData] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  // One session expanded at a time -- the detail table is wide, and stacking
  // several open at once buries the summary rows it hangs off.
  const [expandedSession, setExpandedSession] = useState<string | null>(null)
  const bounds = useMemo(() => monthBounds(month), [month])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const report = await getReport(bounds.start, bounds.end, mode, origin)
      setData(report)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the report.')
    } finally {
      setLoading(false)
    }
  }, [bounds.end, bounds.start, mode, origin])

  useEffect(() => {
    const query = new URLSearchParams({ mode, origin, month })
    window.history.replaceState(null, '', `${window.location.pathname}?${query}`)
    void load()
  }, [load, mode, month, origin])

  const maxStrategyPnl = Math.max(1, ...(data?.by_strategy.map((strategy) => Math.abs(strategy.realized_pnl)) ?? []))

  return (
    <div className="nova-signals nova-reports">
      <header className="nova-signals-head nova-reports-head">
        <div>
          <h1>Reports</h1>
          <p>Daily session reports and monthly summaries from finalized closed trades.</p>
        </div>
        <div className="nova-report-actions">
          <div className="nova-report-month">
            <NativeSelect aria-label="Report month" value={month} onChange={(event) => setMonth(event.target.value)}>
              {monthOptions().map((value) => <option key={value} value={value}>{monthLabel(value)}</option>)}
            </NativeSelect>
          </div>
          <a className="conv-pill" href={reportExportUrl('csv', bounds.start, bounds.end, mode, origin)} download>
            <Download size={13} /> Download CSV
          </a>
          <a className="conv-pill" href={reportExportUrl('pdf', bounds.start, bounds.end, mode, origin)} download>
            <Download size={13} /> Download PDF
          </a>
        </div>
      </header>

      <div className="nova-report-filters">
        <Segment
          label="Execution mode"
          value={mode}
          options={[{ value: 'paper', label: 'Paper' }, { value: 'live', label: 'Live' }]}
          onChange={setMode}
        />
        <Segment
          label="Trade origin"
          value={origin}
          options={[
            { value: 'all', label: 'All Trades' },
            { value: 'automated', label: 'Automated Only' },
            { value: 'manual', label: 'Manual Only' },
          ]}
          onChange={setOrigin}
        />
      </div>

      {loading ? (
        <ReportBodySkeleton month={month} />
      ) : error ? (
        <p className="nova-signals-state" role="alert">
          <AlertTriangle size={16} /> {error}
          <Button variant="unstyled" type="button" className="conv-pill" onClick={() => void load()}>Retry</Button>
        </p>
      ) : !data ? null : (
        <>
          <section className="nova-report-stats" aria-label="Monthly summary">
            <div className="nova-report-stat">
              <span>Net P&amp;L ({monthLabel(month).split(' ')[0]})</span>
              <strong className={data.totals.net_pnl >= 0 ? 'nova-sig-ok' : 'nova-sig-bad'}>{rupees(data.totals.net_pnl, true)}</strong>
            </div>
            <div className="nova-report-stat"><span>Sessions</span><strong>{data.totals.sessions}</strong></div>
            <div className="nova-report-stat"><span>Win Rate</span><strong>{metricText(data.win_rate.value, '%')}</strong></div>
            <div className="nova-report-stat"><span>Profit Factor</span><strong>{data.profit_factor.value === null ? '—' : data.profit_factor.value.toFixed(2)}</strong></div>
            <div className="nova-report-stat"><span>Max Drawdown</span><strong className="nova-report-dd">{drawdownText(data.max_drawdown.value)}</strong></div>
          </section>

          {data.totals.trades === 0 ? (
            <p className="nova-signals-state" role="status">
              {origin === 'manual' ? 'No manual trades recorded for this period.' : origin === 'automated' ? 'No automated strategy trades recorded for this period.' : 'No trades recorded for the selected month.'}
            </p>
          ) : (
            <div className="nova-report-layout">
              <section className="nova-report-panel nova-report-sessions" aria-labelledby="daily-reports-title">
                <div className="nova-report-panel-head">
                  <h2 id="daily-reports-title">Daily Session Reports</h2>
                  <span>{monthLabel(month)}</span>
                </div>
                <div className="nova-report-table-wrap">
                  <Table variant="unstyled" className="nova-report-table">
                    <TableHeader>
                      <TableRow>
                        <TableHead aria-label="Expand" /><TableHead>Date</TableHead><TableHead>Strategy Mix</TableHead><TableHead>Trades</TableHead><TableHead>Win Rate</TableHead><TableHead>Max DD</TableHead><TableHead>Net P&amp;L</TableHead><TableHead>Mode</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.daily_sessions.map((session) => {
                        const expanded = expandedSession === session.date
                        const canExpand = (session.trades_detail?.length ?? 0) > 0
                        return (
                        <Fragment key={session.date}>
                        <TableRow
                          className={`nova-report-session-row${canExpand ? ' is-expandable' : ''}${expanded ? ' is-expanded' : ''}`}
                          onClick={canExpand ? () => setExpandedSession(expanded ? null : session.date) : undefined}
                          aria-expanded={canExpand ? expanded : undefined}
                        >
                          <TableCell className="nova-report-expand">
                            {canExpand ? <ChevronRight size={13} className={expanded ? 'is-open' : ''} aria-hidden /> : null}
                          </TableCell>
                          <TableCell>{formatSessionDate(session.date)}</TableCell>
                          <TableCell>{session.strategy_mix.join(' · ')}</TableCell>
                          <TableCell>{session.trades}</TableCell>
                          <TableCell>{metricText(session.win_rate.value, '%')}</TableCell>
                          <TableCell className="nova-report-dd">{drawdownText(session.max_drawdown.value)}</TableCell>
                          <TableCell className={session.net_pnl >= 0 ? 'nova-sig-ok' : 'nova-sig-bad'}>{rupees(session.net_pnl, true)}</TableCell>
                          <TableCell className="nova-report-mode">{session.mode}</TableCell>
                        </TableRow>
                        {expanded ? (
                          <TableRow className="nova-report-detail-row">
                            <TableCell colSpan={8}>
                              <TradeDetailTable trades={session.trades_detail} />
                            </TableCell>
                          </TableRow>
                        ) : null}
                        </Fragment>
                        )
                      })}
                    </TableBody>
                  </Table>
                </div>
              </section>

              <aside className="nova-report-aside">
                <section className="nova-report-panel">
                  <div className="nova-report-panel-head"><h2>P&amp;L Calendar — {monthLabel(month).split(' ')[0]}</h2></div>
                  <Calendar
                    month={month}
                    sessions={data.daily_sessions}
                  />
                  <div className="nova-pnl-legend"><span className="is-loss" /> Loss <span className="is-neutral" /> No closed trades <span className="is-profit" /> Profit</div>
                </section>

                <section className="nova-report-panel">
                  <div className="nova-report-panel-head"><h2>By Strategy — {monthLabel(month).split(' ')[0]}</h2></div>
                  <TooltipProvider delay={120}>
                    <div className="nova-report-strategies">
                      {data.by_strategy.map((strategy) => (
                        <div className="nova-report-strategy" key={strategy.display_name}>
                          <span>
                            <Tooltip>
                              <TooltipTrigger render={(
                                <b
                                  className="nova-strategy-name"
                                  aria-label={`${strategy.display_name}. ${strategy.closed_trades} closed trades. ${metricText(strategy.win_rate.value, '%')} win rate.`}
                                  tabIndex={0}
                                >
                                  {strategy.display_name}
                                </b>
                              )} />
                              <TooltipContent className={`nova-pnl-tooltip nova-strategy-tooltip ${strategy.realized_pnl >= 0 ? 'is-profit' : 'is-loss'}`} side="top" sideOffset={8}>
                                <div className="nova-pnl-tooltip-head">
                                  <strong>{strategy.display_name}</strong>
                                  <span>{monthLabel(month).split(' ')[0]}</span>
                                </div>
                                <div className="nova-pnl-tooltip-value">
                                  {strategy.realized_pnl >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
                                  <strong>{rupees(strategy.realized_pnl, true)}</strong>
                                  <span>realized</span>
                                </div>
                                <div className="nova-pnl-tooltip-stats">
                                  <span><strong>{strategy.closed_trades}</strong>Trades</span>
                                  <span><strong>{metricText(strategy.win_rate.value, '%')}</strong>Win rate</span>
                                  <span><strong>{strategy.realized_pnl < 0 ? '-' : ''}{strategy.contribution_percentage}%</strong>Contribution</span>
                                </div>
                              </TooltipContent>
                            </Tooltip>
                            <small className={`nova-report-contribution${strategy.realized_pnl < 0 ? ' is-loss' : ''}`}>
                              {strategy.realized_pnl < 0 ? '-' : ''}{strategy.contribution_percentage}%
                            </small>
                          </span>
                          <div><i className={strategy.realized_pnl >= 0 ? 'is-profit' : 'is-loss'} style={{ width: `${Math.max(4, Math.abs(strategy.realized_pnl) / maxStrategyPnl * 100)}%` }} /></div>
                          <strong className={strategy.realized_pnl >= 0 ? 'nova-sig-ok' : 'nova-sig-bad'}>{rupees(strategy.realized_pnl, true)}</strong>
                        </div>
                      ))}
                    </div>
                  </TooltipProvider>
                </section>
              </aside>
            </div>
          )}
        </>
      )}
    </div>
  )
}
