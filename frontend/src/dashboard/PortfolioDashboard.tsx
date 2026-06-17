import { AnimatePresence, motion } from 'framer-motion'
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Award,
  Clock,
  Flame,
  Gauge,
  Layers,
  PieChart,
  RefreshCw,
  Receipt,
  TrendingDown,
  TrendingUp,
  Wallet,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { TickingNumber } from '../components/TickingNumber'
import { formatCurrency } from '../lib/format'
import { DailyPnlBars, EquityCurveChart, SideSplit, WinLossDonut } from './charts'
import { getPortfolioAnalytics, type PortfolioAnalytics, type PortfolioTrade } from './portfolioApi'
import './dashboard.css'

const cardVariants = {
  hidden: { opacity: 0, y: 14 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { duration: 0.5, delay: i * 0.05, ease: [0.16, 1, 0.3, 1] as const },
  }),
}

export function PortfolioDashboard() {
  const [data, setData] = useState<PortfolioAnalytics | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async (soft = false) => {
    if (soft) setRefreshing(true)
    try {
      const next = await getPortfolioAnalytics()
      setData(next)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load portfolio analytics')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    let mounted = true
    void load()
    const timer = window.setInterval(() => {
      if (mounted) void load(true)
    }, 15000)
    return () => {
      mounted = false
      window.clearInterval(timer)
    }
  }, [load])

  if (loading && !data) {
    return (
      <div className="nv-dash-state">
        <span className="nv-dash-spinner" />
        <p>Crunching your tracked trades…</p>
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
  const pnlUp = kpis.realized_pnl >= 0
  const streak = kpis.current_streak

  const head = (
    <div className="nv-dash-head">
      <div>
        <h1>Live Portfolio</h1>
        <p>Real-money round-trips NOVA executed on your live account · paper excluded</p>
      </div>
      <button
        className={`secondary-button nv-refresh${refreshing ? ' spin' : ''}`}
        type="button"
        onClick={() => void load(true)}
        aria-label="Refresh analytics"
      >
        <RefreshCw size={14} />
        {refreshing ? 'Refreshing' : 'Refresh'}
      </button>
    </div>
  )

  if (kpis.total_trades === 0 && !data.open_position) {
    return (
      <div className="nv-dash">
        {head}
        <motion.div className="nv-empty-hero" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }}>
          <span className="nv-empty-icon"><Wallet size={26} /></span>
          <h2>No live trades yet</h2>
          <p>
            This dashboard tracks only real-money round-trips — an entry NOVA placed and the matching exit it
            later placed on your live Dhan account. Paper trades are excluded because the paper wallet can be
            reset at any time.
          </p>
          <p className="nv-empty-note">
            {data.funds_connected
              ? `Live funds connected${wallet.available_balance != null ? ` · ${formatCurrency(wallet.available_balance)} available` : ''}. Your first live exit will appear here.`
              : 'Switch the engine to Live mode and connect your Dhan credentials to start tracking.'}
          </p>
        </motion.div>
      </div>
    )
  }

  return (
    <div className="nv-dash">
      {head}

      {/* Hero band */}
      <motion.section className="nv-hero" custom={0} variants={cardVariants} initial="hidden" animate="show">
        <div className="nv-hero-main">
          <span className="nv-hero-label">
            <Wallet size={13} /> Net realized P&L
          </span>
          <div className={`nv-hero-value ${pnlUp ? 'pos' : 'neg'}`}>
            <TickingNumber value={kpis.realized_pnl} kind="currency" signed />
          </div>
          <div className="nv-hero-sub">
            <span className={`nv-chip ${pnlUp ? 'pos' : 'neg'}`}>
              {pnlUp ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}
              {kpis.realized_pnl_pct >= 0 ? '+' : ''}
              {kpis.realized_pnl_pct.toFixed(2)}%
            </span>
            <span className="nv-hero-muted">on {formatCurrency(wallet.starting_balance ?? 0)} capital</span>
          </div>
        </div>
        <div className="nv-hero-grid">
          <HeroStat label="Equity" value={formatCurrency(wallet.equity ?? 0)} />
          <HeroStat label="Available" value={formatCurrency(wallet.available_balance ?? 0)} />
          <HeroStat label="Deployed" value={formatCurrency(wallet.utilized_amount ?? 0)} />
          <HeroStat
            label="Current streak"
            value={streak.count > 0 ? `${streak.count} ${streak.type}${streak.count === 1 ? '' : 's'}` : '—'}
            tone={streak.type === 'win' ? 'pos' : streak.type === 'loss' ? 'neg' : undefined}
            icon={<Flame size={13} />}
          />
        </div>
      </motion.section>

      {/* KPI grid */}
      <div className="nv-kpi-grid">
        <KpiCard i={1} icon={<Gauge size={15} />} label="Win rate" tone={kpis.win_rate >= 50 ? 'pos' : 'neg'}>
          <TickingNumber value={kpis.win_rate} kind="percent" />
          <span className="nv-kpi-foot">{kpis.wins}W · {kpis.losses}L{kpis.breakeven ? ` · ${kpis.breakeven}F` : ''}</span>
        </KpiCard>
        <KpiCard i={2} icon={<Activity size={15} />} label="Total trades">
          <TickingNumber value={kpis.total_trades} kind="number" />
          <span className="nv-kpi-foot">avg {formatCurrency(kpis.avg_trade)}/trade</span>
        </KpiCard>
        <KpiCard i={3} icon={<Layers size={15} />} label="Profit factor" tone={kpis.profit_factor >= 1 ? 'pos' : 'neg'}>
          <TickingNumber value={kpis.profit_factor} kind="number" decimals={2} />
          <span className="nv-kpi-foot">gross win ÷ gross loss</span>
        </KpiCard>
        <KpiCard i={4} icon={<TrendingUp size={15} />} label="Best trade" tone="pos">
          <span className="nv-kpi-value pos">{formatCurrency(kpis.best_trade)}</span>
          <span className="nv-kpi-foot">single round-trip</span>
        </KpiCard>
        <KpiCard i={5} icon={<TrendingDown size={15} />} label="Worst trade" tone="neg">
          <span className="nv-kpi-value neg">{formatCurrency(kpis.worst_trade)}</span>
          <span className="nv-kpi-foot">single round-trip</span>
        </KpiCard>
        <KpiCard i={6} icon={<Award size={15} />} label="Avg win / loss">
          <span className="nv-kpi-value">
            <span className="pos">{formatCurrency(kpis.avg_win)}</span>
            <span className="nv-kpi-divider">/</span>
            <span className="neg">{formatCurrency(kpis.avg_loss)}</span>
          </span>
          <span className="nv-kpi-foot">per winning · losing trade</span>
        </KpiCard>
        <KpiCard i={7} icon={<TrendingDown size={15} />} label="Max drawdown" tone="neg">
          <span className="nv-kpi-value neg">{formatCurrency(kpis.max_drawdown)}</span>
          <span className="nv-kpi-foot">{kpis.max_drawdown_pct.toFixed(2)}% peak-to-trough</span>
        </KpiCard>
        <KpiCard i={8} icon={<Clock size={15} />} label="Avg hold">
          <span className="nv-kpi-value">{formatHold(kpis.avg_hold_minutes)}</span>
          <span className="nv-kpi-foot">{formatCurrency(kpis.total_charges)} total charges</span>
        </KpiCard>
      </div>

      {data.open_position ? (
        <motion.div className="nv-open-banner" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
          <span className="nv-pulse" />
          <strong>Open position</strong>
          <span>
            {data.open_position.qty} × {data.open_position.symbol ?? '—'} ({data.open_position.option_side}) @{' '}
            {formatCurrency(data.open_position.entry_price ?? 0)}
          </span>
        </motion.div>
      ) : null}

      {/* Equity curve */}
      <motion.section className="nv-panel nv-panel-wide" custom={2} variants={cardVariants} initial="hidden" animate="show">
        <div className="nv-panel-head">
          <h2><TrendingUp size={15} /> Equity curve</h2>
          <span className="nv-panel-note">cumulative realized P&L over closed trades</span>
        </div>
        <EquityCurveChart points={data.equity_curve} />
      </motion.section>

      {/* Daily + distribution */}
      <div className="nv-two-col">
        <motion.section className="nv-panel" custom={3} variants={cardVariants} initial="hidden" animate="show">
          <div className="nv-panel-head">
            <h2><Activity size={15} /> Daily P&L</h2>
            <span className="nv-panel-note">realized by trading day (IST)</span>
          </div>
          <DailyPnlBars data={data.daily_pnl} />
        </motion.section>

        <motion.section className="nv-panel" custom={4} variants={cardVariants} initial="hidden" animate="show">
          <div className="nv-panel-head">
            <h2><PieChart size={15} /> Outcome split</h2>
          </div>
          <WinLossDonut wins={kpis.wins} losses={kpis.losses} breakeven={kpis.breakeven} />
          <SideSplit ce={data.side_breakdown.CE} pe={data.side_breakdown.PE} />
        </motion.section>
      </div>

      {/* Symbol breakdown */}
      {data.symbol_breakdown.length ? (
        <motion.section className="nv-panel" custom={5} variants={cardVariants} initial="hidden" animate="show">
          <div className="nv-panel-head">
            <h2><Layers size={15} /> By contract</h2>
          </div>
          <div className="nv-symbol-list">
            {data.symbol_breakdown.map((s, i) => (
              <motion.div
                className="nv-symbol-row"
                key={s.symbol}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.1 + i * 0.04 }}
              >
                <span className="nv-symbol-name">{s.symbol}</span>
                <span className="nv-symbol-trades">{s.trades} trade{s.trades === 1 ? '' : 's'}</span>
                <span className={`nv-symbol-pnl ${s.pnl >= 0 ? 'pos' : 'neg'}`}>{formatCurrency(s.pnl)}</span>
              </motion.div>
            ))}
          </div>
        </motion.section>
      ) : null}

      {/* Trades ledger */}
      <motion.section className="nv-panel nv-panel-wide" custom={6} variants={cardVariants} initial="hidden" animate="show">
        <div className="nv-panel-head">
          <h2><Receipt size={15} /> Trade ledger</h2>
          <span className="nv-panel-note">{data.trades.length} closed round-trip{data.trades.length === 1 ? '' : 's'}</span>
        </div>
        <TradesTable trades={data.trades} />
      </motion.section>
    </div>
  )
}

function HeroStat({ label, value, tone, icon }: { label: string; value: string; tone?: 'pos' | 'neg'; icon?: React.ReactNode }) {
  return (
    <div className="nv-hero-stat">
      <span className="nv-hero-stat-label">{icon}{label}</span>
      <span className={`nv-hero-stat-value${tone ? ` ${tone}` : ''}`}>{value}</span>
    </div>
  )
}

function KpiCard({
  i,
  icon,
  label,
  tone,
  children,
}: {
  i: number
  icon: React.ReactNode
  label: string
  tone?: 'pos' | 'neg'
  children: React.ReactNode
}) {
  return (
    <motion.div className={`nv-kpi-card${tone ? ` accent-${tone}` : ''}`} custom={i} variants={cardVariants} initial="hidden" animate="show">
      <span className="nv-kpi-label">{icon}{label}</span>
      <div className="nv-kpi-body">{children}</div>
    </motion.div>
  )
}

function TradesTable({ trades }: { trades: PortfolioTrade[] }) {
  if (!trades.length) {
    return <div className="nv-chart-empty">No closed trades yet. Once NOVA exits a position it lands here.</div>
  }
  return (
    <div className="nv-table-wrap">
      <table className="nv-table">
        <thead>
          <tr>
            <th>Contract</th>
            <th>Side</th>
            <th className="num">Qty</th>
            <th className="num">Entry</th>
            <th className="num">Exit</th>
            <th className="num">P&L</th>
            <th className="num">Return</th>
            <th>Closed</th>
          </tr>
        </thead>
        <tbody>
          <AnimatePresence initial={false}>
            {trades.map((t, i) => {
              const pnl = t.realized_pnl ?? 0
              return (
                <motion.tr
                  key={t.exit_order_id ?? `${t.symbol}-${t.closed_at}-${i}`}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.35, delay: Math.min(i * 0.03, 0.4) }}
                >
                  <td className="nv-td-symbol">{t.symbol ?? '—'}</td>
                  <td>
                    <span className={`nv-tag ${t.option_side === 'CE' ? 'ce' : 'pe'}`}>{t.option_side ?? '—'}</span>
                  </td>
                  <td className="num">{t.qty}</td>
                  <td className="num">{t.entry_price != null ? formatCurrency(t.entry_price) : '—'}</td>
                  <td className="num">{t.exit_price != null ? formatCurrency(t.exit_price) : '—'}</td>
                  <td className={`num ${pnl >= 0 ? 'pos' : 'neg'}`}>{formatCurrency(pnl)}</td>
                  <td className={`num ${pnl >= 0 ? 'pos' : 'neg'}`}>
                    {t.pnl_pct != null ? `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(1)}%` : '—'}
                  </td>
                  <td className="nv-td-time">{formatClosed(t.closed_at)}</td>
                </motion.tr>
              )
            })}
          </AnimatePresence>
        </tbody>
      </table>
    </div>
  )
}

function formatHold(minutes: number): string {
  if (!minutes || minutes <= 0) return '—'
  if (minutes < 60) return `${minutes.toFixed(minutes < 10 ? 1 : 0)}m`
  const h = Math.floor(minutes / 60)
  const m = Math.round(minutes % 60)
  return `${h}h ${m}m`
}

function formatClosed(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}
