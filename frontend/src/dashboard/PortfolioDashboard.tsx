import { motion } from 'framer-motion'
import { Download, Loader2, RefreshCw, ShieldAlert } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { RuntimeStatus } from '../api'
import { MotionProgressFill, MotionSpinner } from '../components/MotionPrimitives'
import { formatCurrency } from '../lib/format'
import { reportCsvUrl } from '../reports/reportsApi'
import { getRiskOverview, type RiskOverview, type RiskStrategyRow } from '../risk/riskApi'
import { getSignals, type SignalsPage } from '../signals/signalsApi'
import type { SystemHealth } from '../types'
import { getWebhooksOverview, type WebhooksOverview } from '../webhooks/webhooksApi'
import { DailyPnlBars, EquityCurveChart } from './charts'
import { getPortfolioAnalytics, type PortfolioAnalytics } from './portfolioApi'
import './dashboard.css'

const cardVariants = {
  hidden: { opacity: 0, y: 10 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { duration: 0.38, delay: i * 0.035, ease: [0.16, 1, 0.3, 1] as const },
  }),
}

type ExportRange = 'today' | '7d' | '30d'
type EquityRange = '1w' | '1m' | '3m' | 'all'

export interface DashboardPreviewBundle {
  data: PortfolioAnalytics
  risk: RiskOverview
  signals: SignalsPage
  webhooks: WebhooksOverview
}

interface Props {
  runtime: RuntimeStatus | null
  health: SystemHealth | null
  onKill: () => void
  onManageStrategies: () => void
  onViewHealth: () => void
  preview?: DashboardPreviewBundle
}

interface StrategyPerformanceRow {
  id: string
  name: string
  mode: string
  selected: boolean
  orders: number | null
  lossPct: number | null
  maxLots: number | null
  pnl: number | null
}

export function PortfolioDashboard({
  runtime,
  health,
  onKill,
  onManageStrategies,
  onViewHealth,
  preview,
}: Props) {
  const [data, setData] = useState<PortfolioAnalytics | null>(preview?.data ?? null)
  const [risk, setRisk] = useState<RiskOverview | null>(preview?.risk ?? null)
  const [signals, setSignals] = useState<SignalsPage | null>(preview?.signals ?? null)
  const [webhooks, setWebhooks] = useState<WebhooksOverview | null>(preview?.webhooks ?? null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(!preview)
  const [refreshing, setRefreshing] = useState(false)
  const [exportRange, setExportRange] = useState<ExportRange>('today')
  const [equityRange, setEquityRange] = useState<EquityRange>('1m')
  const [viewMode, setViewMode] = useState<'paper' | 'live'>(() => {
    const requested = new URLSearchParams(window.location.search).get('mode')
    return requested === 'paper' || requested === 'live' ? requested : 'live'
  })
  const [tradeOrigin, setTradeOrigin] = useState<'all' | 'automated' | 'manual'>(() => {
    const requested = new URLSearchParams(window.location.search).get('tradeOrigin')
    return requested === 'automated' || requested === 'manual' ? requested : 'all'
  })

  // Changing this only changes which book is displayed — it never arms or
  // stops the engine, and never touches execution/runtime mode.
  const setViewModeAndUrl = useCallback((next: 'paper' | 'live') => {
    setViewMode(next)
    const url = new URL(window.location.href)
    url.searchParams.set('mode', next)
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}`)
  }, [])

  // Reporting-only: never starts/stops an engine, changes execution mode, or
  // mutates stored P&L — it only changes which trades are aggregated for display.
  const setTradeOriginAndUrl = useCallback((next: 'all' | 'automated' | 'manual') => {
    setTradeOrigin(next)
    const url = new URL(window.location.href)
    url.searchParams.set('tradeOrigin', next)
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}`)
  }, [])

  const load = useCallback(async (soft = false, forceWalletRefresh = false) => {
    if (preview) return
    if (soft) setRefreshing(true)

    const [portfolioResult, riskResult, signalResult, webhookResult] = await Promise.allSettled([
      getPortfolioAnalytics(viewMode, { force: forceWalletRefresh, tradeOrigin }),
      getRiskOverview(),
      getSignals({ limit: 1 }),
      getWebhooksOverview(),
    ])

    if (portfolioResult.status === 'fulfilled') {
      setData(portfolioResult.value)
      setError('')
    } else {
      setError(
        portfolioResult.reason instanceof Error
          ? portfolioResult.reason.message
          : 'Could not load portfolio analytics',
      )
    }
    if (riskResult.status === 'fulfilled') setRisk(riskResult.value)
    if (signalResult.status === 'fulfilled') setSignals(signalResult.value)
    if (webhookResult.status === 'fulfilled') setWebhooks(webhookResult.value)

    setLoading(false)
    setRefreshing(false)
  }, [preview, viewMode, tradeOrigin])

  useEffect(() => {
    if (preview) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
    const timer = window.setInterval(() => void load(true), 15_000)
    return () => window.clearInterval(timer)
  }, [load, preview])

  const filteredEquity = useMemo(
    () => (data ? filterEquity(data, equityRange) : []),
    [data, equityRange],
  )

  if (loading && !data) {
    return (
      <div className="nv-dash-state">
        <MotionSpinner className="text-purple-400">
          <Loader2 size={32} />
        </MotionSpinner>
        <span>Loading portfolio truth…</span>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="nv-dash-state">
        <p className="nv-dash-error">{error}</p>
        <button className="secondary-button" type="button" onClick={() => void load()}>
          <RefreshCw size={14} /> Retry
        </button>
      </div>
    )
  }

  if (!data) return null

  const { kpis, wallet } = data
  const sessionPnl = wallet.session_pnl ?? kpis.realized_pnl
  const balanceSource = viewMode === 'live' ? (wallet.balance_source ?? 'current') : 'current'
  const equityValue = balanceSource === 'none' ? '—' : moneyOrUnavailable(wallet.equity)
  const equityNote = balanceSource === 'last_known'
    ? `Last known Dhan balance${wallet.last_known_at ? ` · ${new Date(wallet.last_known_at).toLocaleString()}` : ''}`
    : balanceSource === 'none'
      ? 'Connect to Dhan to view your Live balance'
      : `${moneyOrUnavailable(wallet.available_balance)} available`
  const signalCounts = signals?.counts ?? {}
  const acceptedSignals = count(signalCounts, 'accepted') + count(signalCounts, 'queued')
  const rejectedSignals = count(signalCounts, 'rejected')
  const signalTotal = Object.values(signalCounts).reduce((sum, value) => sum + Number(value || 0), 0)
  const openPosition = runtime?.position.has_open_position ? runtime.position : data.open_position
  const riskLoss = getLossUtilisation(risk)
  const daily = data.daily_pnl.slice(-14)
  const bestDay = daily.length ? Math.max(...daily.map((point) => point.pnl)) : null
  const worstDay = daily.length ? Math.min(...daily.map((point) => point.pnl)) : null
  const greenDays = daily.filter((point) => point.pnl > 0).length
  const averageDay = daily.length ? daily.reduce((sum, point) => sum + point.pnl, 0) / daily.length : null
  const growth = filteredEquity.length > 1
    ? filteredEquity[filteredEquity.length - 1].equity - filteredEquity[0].equity
    : kpis.realized_pnl
  const growthPct = wallet.starting_balance
    ? (growth / wallet.starting_balance) * 100
    : null
  const exportDates = getExportDates(exportRange)
  const mode = viewMode
  const engineMode = runtime?.engine.mode
  const engineState = runtime?.engine.state
  const strategyRows = buildStrategyRows(runtime, risk)

  return (
    <div className="nv-dash">
      <header className="nv-dash-head">
        <div>
          <h1>Portfolio Dashboard</h1>
          <p>
            Viewing: {titleCase(mode)} data · {formatSessionDate(data.generated_at)} ·{' '}
            {health?.market === 'open' ? 'Market open until 15:30 IST' : 'Market closed'}
            {engineMode && engineState ? ` · Engine: ${titleCase(engineMode)} — ${engineState}` : ''}
          </p>
        </div>
        <div className="nv-dash-actions">
          <div className="nv-segmented" aria-label="Dashboard view mode" role="group">
            {(['paper', 'live'] as const).map((option) => (
              <button
                key={option}
                type="button"
                className={option === viewMode ? 'active' : ''}
                onClick={() => setViewModeAndUrl(option)}
                aria-pressed={option === viewMode}
              >
                {titleCase(option)}
              </button>
            ))}
          </div>
          <div className="nv-segmented" aria-label="Trade origin filter" role="group">
            {(['all', 'automated', 'manual'] as const).map((option) => (
              <button
                key={option}
                type="button"
                className={option === tradeOrigin ? 'active' : ''}
                onClick={() => setTradeOriginAndUrl(option)}
                aria-pressed={option === tradeOrigin}
              >
                {option === 'all' ? 'All Trades' : titleCase(option)}
              </button>
            ))}
          </div>
          <select
            className="nv-range-select"
            aria-label="Export period"
            value={exportRange}
            onChange={(event) => setExportRange(event.target.value as ExportRange)}
          >
            <option value="today">Today</option>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
          </select>
          <a
            className="nv-export-button"
            href={reportCsvUrl(exportDates.start, exportDates.end, mode, tradeOrigin)}
            download
          >
            <Download size={14} />
            Export Report
          </a>
          <button
            className="nv-icon-refresh"
            type="button"
            onClick={() => void load(true, true)}
            aria-label="Refresh dashboard"
            title="Refresh dashboard"
          >
            <RefreshCw size={15} className={refreshing ? 'is-spinning' : ''} />
          </button>
        </div>
      </header>

      {error ? <p className="nv-soft-error" role="status">{error} Showing the last successful snapshot.</p> : null}
      {balanceSource === 'last_known' ? (
        <p className="nv-soft-error" role="status">
          Current balance could not be refreshed. Update your Dhan Access Token to refresh the account.
        </p>
      ) : null}
      {balanceSource === 'none' ? (
        <p className="nv-soft-error" role="status">Connect to Dhan to view your Live balance.</p>
      ) : null}

      <section className="nv-kpi-grid nv-kpi-grid-six" aria-label="Portfolio summary">
        <MetricCard
          i={0}
          label="Session P&L"
          value={signedCurrency(sessionPnl)}
          valueTone={sessionPnl >= 0 ? 'pos' : 'neg'}
          note={`${kpis.total_trades} trades · ${formatPercent(kpis.win_rate)} win`}
        />
        <MetricCard
          i={1}
          label={balanceSource === 'last_known' ? 'Last known Dhan balance' : 'Account equity'}
          value={equityValue}
          note={equityNote}
        />
        <MetricCard
          i={2}
          label="Open positions"
          value={openPosition ? '1' : '0'}
          note={openPositionNote(openPosition, runtime)}
          noteTone={openPosition ? 'pos' : undefined}
        />
        <MetricCard
          i={3}
          label="Signals (24h)"
          value={signals?.available === false ? 'Unavailable' : String(signalTotal)}
          note={signals ? `${acceptedSignals} accepted · ${rejectedSignals} rejected` : 'Signal feed not available'}
        />
        <MetricCard
          i={4}
          label="Profit factor"
          value={Number.isFinite(kpis.profit_factor) ? kpis.profit_factor.toFixed(2) : '—'}
          valueTone={kpis.profit_factor >= 1 ? 'pos' : kpis.total_trades ? 'neg' : undefined}
          note={`${formatHold(kpis.avg_hold_minutes)} avg hold`}
        />
        <MetricCard
          i={5}
          label="Daily loss used"
          value={riskLoss.pct == null ? (riskLoss.unlimited ? 'No limit' : 'Unavailable') : `${riskLoss.pct.toFixed(1)}%`}
          valueTone={riskLoss.pct != null && riskLoss.pct >= 70 ? 'warn' : undefined}
          note={riskLoss.remaining == null ? 'Risk service did not return a limit' : `${formatCurrency(riskLoss.remaining)} remaining`}
        />
      </section>

      <div className="nv-dashboard-grid nv-dashboard-grid-charts">
        <motion.section className="nv-panel nv-equity-panel" custom={1} variants={cardVariants} initial="hidden" animate="show">
          <div className="nv-panel-head">
            <div className="nv-panel-title-row">
              <h2>Equity Curve</h2>
              <span>{titleCase(mode)} account · tracked closed trades</span>
            </div>
            <div className="nv-segmented" aria-label="Equity range">
              {(['1w', '1m', '3m', 'all'] as const).map((range) => (
                <button
                  key={range}
                  type="button"
                  className={range === equityRange ? 'active' : ''}
                  onClick={() => setEquityRange(range)}
                >
                  {range === 'all' ? 'All' : range.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
          <div className="nv-equity-summary">
            <strong>{moneyOrUnavailable(wallet.equity)}</strong>
            <span className={growth >= 0 ? 'pos' : 'neg'}>
              {signedCurrency(growth)}
              {growthPct == null ? '' : ` (${growthPct >= 0 ? '+' : ''}${growthPct.toFixed(1)}%)`}
              {' '}in range
            </span>
          </div>
          <EquityCurveChart points={filteredEquity} />
        </motion.section>

        <motion.section className="nv-panel nv-daily-panel" custom={2} variants={cardVariants} initial="hidden" animate="show">
          <div className="nv-panel-head">
            <h2>Daily P&L — last 14 sessions</h2>
          </div>
          <DailyPnlBars data={daily} />
          <div className="nv-daily-stats">
            <SmallStat label="Best day" value={bestDay == null ? '—' : signedCurrency(bestDay)} tone="pos" />
            <SmallStat label="Worst day" value={worstDay == null ? '—' : signedCurrency(worstDay)} tone="neg" />
            <SmallStat label="Green days" value={`${greenDays} / ${daily.length}`} />
            <SmallStat label="Avg / day" value={averageDay == null ? '—' : signedCurrency(averageDay)} tone={averageDay != null && averageDay >= 0 ? 'pos' : 'neg'} />
          </div>
        </motion.section>
      </div>

      <div className="nv-dashboard-grid nv-dashboard-grid-lower">
        <motion.section className="nv-panel nv-strategy-panel" custom={3} variants={cardVariants} initial="hidden" animate="show">
          <div className="nv-panel-head">
            <h2>Strategy Performance</h2>
            <button type="button" className="nv-panel-link" onClick={onManageStrategies}>Manage strategies →</button>
          </div>
          <StrategyPerformance rows={strategyRows} mode={mode} />
        </motion.section>

        <motion.aside className="nv-panel nv-health-panel" custom={4} variants={cardVariants} initial="hidden" animate="show">
          <div className="nv-panel-head">
            <h2>System Health</h2>
            <button type="button" className="nv-panel-link" onClick={onViewHealth}>Details →</button>
          </div>
          <SystemHealthList health={health} runtime={runtime} webhooks={webhooks} />
          <KillSwitch runtime={runtime} onKill={onKill} />
        </motion.aside>
      </div>
    </div>
  )
}

function MetricCard({
  i,
  label,
  value,
  note,
  valueTone,
  noteTone,
}: {
  i: number
  label: string
  value: string
  note: string
  valueTone?: 'pos' | 'neg' | 'warn'
  noteTone?: 'pos' | 'neg'
}) {
  return (
    <motion.article className="nv-kpi-card" custom={i} variants={cardVariants} initial="hidden" animate="show">
      <span className="nv-kpi-label">{label}</span>
      <strong className={`nv-kpi-value${valueTone ? ` ${valueTone}` : ''}`}>{value}</strong>
      <span className={`nv-kpi-note${noteTone ? ` ${noteTone}` : ''}`}>{note}</span>
    </motion.article>
  )
}

function SmallStat({ label, value, tone }: { label: string; value: string; tone?: 'pos' | 'neg' }) {
  return (
    <span className="nv-small-stat">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </span>
  )
}

function StrategyPerformance({ rows, mode }: { rows: StrategyPerformanceRow[]; mode: string }) {
  if (!rows.length) {
    return <div className="nv-table-empty">No eligible strategies or tracked strategy activity is available yet.</div>
  }

  return (
    <div className="nv-table-wrap">
      <table className="nv-performance-table">
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Mode</th>
            <th className="num">Orders</th>
            <th>Loss risk</th>
            <th className="num">Max lots</th>
            <th className="num">Net P&L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>
                <span className="nv-strategy-name">
                  <span className="nv-strategy-tag">{row.selected ? 'ACTIVE' : 'NOVA'}</span>
                  {row.name}
                </span>
              </td>
              <td className="nv-mode-cell">{titleCase(row.mode || mode)}</td>
              <td className="num">{row.orders ?? '—'}</td>
              <td>
                {row.lossPct == null ? (
                  <span className="nv-muted">—</span>
                ) : (
                  <span className="nv-risk-meter">
                    <span><i style={{ width: `${Math.min(row.lossPct, 100)}%` }} /></span>
                    {row.lossPct.toFixed(0)}%
                  </span>
                )}
              </td>
              <td className="num">{row.maxLots ?? '—'}</td>
              <td className={`num ${row.pnl == null ? '' : row.pnl >= 0 ? 'pos' : 'neg'}`}>
                {row.pnl == null ? '—' : signedCurrency(row.pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SystemHealthList({
  health,
  runtime,
  webhooks,
}: {
  health: SystemHealth | null
  runtime: RuntimeStatus | null
  webhooks: WebhooksOverview | null
}) {
  const rows = [
    healthRow('Dhan Broker', dhanStatus(health), health?.dhan === 'connected' ? 'ok' : health?.dhan === 'auth_issue' ? 'bad' : 'muted'),
    healthRow('Market Feed', feedStatus(health), health?.feed === 'live' ? 'ok' : health?.feed === 'stale' ? 'warn' : health?.feed === 'down' ? 'bad' : 'muted'),
    healthRow('Router Engine', engineStatus(runtime, health), runtime?.engine.running ? 'info' : 'muted'),
    healthRow(
      'TradingView Webhook',
      webhooks?.available === false
        ? 'Unavailable'
        : webhooks
          ? `Healthy · ${webhooks.endpoints.length} endpoint${webhooks.endpoints.length === 1 ? '' : 's'}`
          : 'Unknown',
      webhooks?.available ? 'ok' : 'muted',
    ),
    healthRow('Static IP', staticIpStatus(health), health?.staticIp === 'verified' ? 'ok' : health?.staticIp === 'failed' ? 'bad' : 'muted'),
  ]

  return (
    <div className="nv-health-list">
      {rows.map((row) => (
        <div className="nv-health-row" key={row.label}>
          <span className={`nv-health-dot ${row.tone}`} />
          <strong>{row.label}</strong>
          <span className={row.tone}>{row.value}</span>
        </div>
      ))}
    </div>
  )
}

function KillSwitch({ runtime, onKill }: { runtime: RuntimeStatus | null; onKill: () => void }) {
  const [holding, setHolding] = useState(false)
  const timer = useRef<number | null>(null)
  const actionable = Boolean(runtime?.engine.running || runtime?.position.has_open_position)

  function cancel() {
    if (timer.current != null) window.clearTimeout(timer.current)
    timer.current = null
    setHolding(false)
  }

  function begin() {
    if (!actionable || timer.current != null) return
    setHolding(true)
    timer.current = window.setTimeout(() => {
      timer.current = null
      setHolding(false)
      onKill()
    }, 800)
  }

  useEffect(() => cancel, [])

  return (
    <div className="nv-kill-switch">
      <strong><ShieldAlert size={14} /> Kill Switch</strong>
      <p>Stops new entries and squares off NOVA&apos;s tracked open position.</p>
      <button
        type="button"
        disabled={!actionable}
        onPointerDown={begin}
        onPointerUp={cancel}
        onPointerLeave={cancel}
        onPointerCancel={cancel}
        onKeyDown={(event) => {
          if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) begin()
        }}
        onKeyUp={(event) => {
          if (event.key === 'Enter' || event.key === ' ') cancel()
        }}
      >
        {holding ? <MotionProgressFill durationSeconds={0.8} tone="danger" /> : null}
        <span>{actionable ? 'Hold to Stop & Square Off' : 'Engine stopped · position flat'}</span>
      </button>
    </div>
  )
}

function buildStrategyRows(runtime: RuntimeStatus | null, risk: RiskOverview | null): StrategyPerformanceRow[] {
  const strategies = runtime?.eligible_strategies ?? []
  const riskRows = risk?.strategies ?? []

  if (strategies.length) {
    return strategies.slice(0, 5).map((strategy) => {
      const matched = findRiskRow(riskRows, strategy.display_name)
      return {
        id: strategy.instance_id,
        name: strategy.display_name,
        mode: strategy.mode,
        selected: strategy.selected === true || runtime?.selected_strategy?.instance_id === strategy.instance_id,
        orders: matched?.usage.orders_count ?? null,
        lossPct: matched?.utilisation.loss.pct ?? null,
        maxLots: matched?.effective.max_lots_per_order ?? strategy.lots,
        pnl: matched ? matched.usage.realized_pnl_paise / 100 : null,
      }
    })
  }

  return riskRows.slice(0, 5).map((row) => ({
    id: row.strategy_name,
    name: row.strategy_name,
    mode: runtime?.engine.mode ?? '—',
    selected: false,
    orders: row.usage.orders_count,
    lossPct: row.utilisation.loss.pct,
    maxLots: row.effective.max_lots_per_order,
    pnl: row.usage.realized_pnl_paise / 100,
  }))
}

function findRiskRow(rows: RiskStrategyRow[], name: string): RiskStrategyRow | undefined {
  const normalized = name.trim().toLowerCase()
  return rows.find((row) => row.strategy_name.trim().toLowerCase() === normalized)
}

function getLossUtilisation(risk: RiskOverview | null): {
  pct: number | null
  remaining: number | null
  unlimited: boolean
} {
  if (!risk?.available) return { pct: null, remaining: null, unlimited: false }
  const limit = risk.user.max_loss_per_day_paise
  if (!limit) return { pct: null, remaining: null, unlimited: true }
  const used = risk.strategies.reduce((sum, row) => sum + Math.max(0, row.usage.loss_used_paise), 0)
  return {
    pct: (used / limit) * 100,
    remaining: Math.max(limit - used, 0) / 100,
    unlimited: false,
  }
}

function filterEquity(data: PortfolioAnalytics, range: EquityRange) {
  if (range === 'all' || data.equity_curve.length < 2) return data.equity_curve
  const days = range === '1w' ? 7 : range === '1m' ? 30 : 90
  const cutoff = Date.now() - days * 86_400_000
  const filtered = data.equity_curve.filter((point) => {
    const timestamp = point.t ? new Date(point.t).getTime() : Number.NaN
    return Number.isNaN(timestamp) || timestamp >= cutoff
  })
  return filtered.length >= 2 ? filtered : data.equity_curve
}

function getExportDates(range: ExportRange): { start: string; end: string } {
  const end = new Date()
  const start = new Date(end)
  if (range === '7d') start.setDate(start.getDate() - 6)
  if (range === '30d') start.setDate(start.getDate() - 29)
  return {
    start: start.toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' }),
    end: end.toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' }),
  }
}

function openPositionNote(
  position: RuntimeStatus['position'] | PortfolioAnalytics['open_position'],
  runtime: RuntimeStatus | null,
): string {
  if (!position) return 'Flat · no tracked position'
  const symbol = 'trading_symbol' in position ? position.trading_symbol : position.symbol
  const pnl = runtime?.position.unrealized_pnl
  return `${symbol ?? position.option_side ?? 'Tracked position'}${pnl == null ? '' : ` · ${signedCurrency(pnl)}`}`
}

function healthRow(label: string, value: string, tone: string) {
  return { label, value, tone }
}

function dhanStatus(health: SystemHealth | null): string {
  if (health?.dhan === 'connected') return 'Connected'
  if (health?.dhan === 'auth_issue') return 'Authentication issue'
  return 'Unknown'
}

function feedStatus(health: SystemHealth | null): string {
  if (health?.feed === 'live') return 'Live'
  if (health?.feed === 'stale') return 'Stale'
  if (health?.feed === 'down') return 'Down'
  if (health?.feed === 'off') return 'Idle'
  return 'Unknown'
}

function engineStatus(runtime: RuntimeStatus | null, health: SystemHealth | null): string {
  if (runtime?.engine.running) return `${titleCase(runtime.engine.mode ?? '')} · ${runtime.engine.accepting_signals ? 'listening' : 'paused'}`
  if (health?.engine === 'paused') return 'Paused'
  return runtime?.engine.display || 'Idle'
}

function staticIpStatus(health: SystemHealth | null): string {
  if (health?.staticIp === 'verified') return 'Verified'
  if (health?.staticIp === 'failed') return 'Verification failed'
  return 'Unknown'
}

function count(counts: Record<string, number>, key: string): number {
  return Number(counts[key] ?? 0)
}

function signedCurrency(value: number): string {
  return `${value >= 0 ? '+' : '-'}${formatCurrency(Math.abs(value))}`
}

function moneyOrUnavailable(value: number | null): string {
  return value == null ? 'Unavailable' : formatCurrency(value)
}

function formatPercent(value: number): string {
  return `${Number.isFinite(value) ? value.toFixed(1) : '0.0'}%`
}

function formatHold(minutes: number): string {
  if (!minutes || minutes <= 0) return '—'
  if (minutes < 60) return `${minutes.toFixed(minutes < 10 ? 1 : 0)}m`
  const hours = Math.floor(minutes / 60)
  const remainder = Math.round(minutes % 60)
  return `${hours}h ${remainder}m`
}

function formatSessionDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Session date unavailable'
  return date.toLocaleDateString('en-IN', {
    timeZone: 'Asia/Kolkata',
    weekday: 'long',
    day: '2-digit',
    month: 'long',
    year: 'numeric',
  })
}

function titleCase(value: string): string {
  if (!value) return 'Unknown'
  return `${value.charAt(0).toUpperCase()}${value.slice(1).toLowerCase()}`
}
