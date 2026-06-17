import { motion } from 'framer-motion'
import { useMemo } from 'react'
import type { DailyPnlPoint, EquityPoint, SideStats } from './portfolioApi'
import { formatCurrency } from '../lib/format'

const EASE = [0.16, 1, 0.3, 1] as const

/* ----------------------------- Equity curve ----------------------------- */

export function EquityCurveChart({ points }: { points: EquityPoint[] }) {
  const W = 840
  const H = 300
  const pad = { top: 24, right: 20, bottom: 28, left: 16 }

  const geom = useMemo(() => {
    if (points.length < 2) return null
    const values = points.map((p) => p.equity)
    const start = points[0].equity
    let min = Math.min(...values, start)
    let max = Math.max(...values, start)
    if (min === max) {
      min -= 1
      max += 1
    }
    const range = max - min
    min -= range * 0.12
    max += range * 0.12
    const innerW = W - pad.left - pad.right
    const innerH = H - pad.top - pad.bottom
    const x = (i: number) => pad.left + (i / (points.length - 1)) * innerW
    const y = (v: number) => pad.top + innerH - ((v - min) / (max - min)) * innerH
    const coords = points.map((p, i) => ({ x: x(i), y: y(p.equity) }))
    const line = coords.map((c, i) => `${i === 0 ? 'M' : 'L'} ${c.x.toFixed(2)} ${c.y.toFixed(2)}`).join(' ')
    const area = `${line} L ${coords[coords.length - 1].x.toFixed(2)} ${H - pad.bottom} L ${coords[0].x.toFixed(2)} ${H - pad.bottom} Z`
    const baselineY = y(start)
    const last = points[points.length - 1].equity
    return { line, area, baselineY, coords, up: last >= start, last, start }
  }, [points])

  if (!geom) {
    return <ChartEmpty label="No equity history yet — trades will plot here." />
  }

  const stroke = geom.up ? 'var(--success)' : 'var(--danger)'
  const fillId = geom.up ? 'eqGradUp' : 'eqGradDown'

  return (
    <svg className="nv-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Equity curve">
      <defs>
        <linearGradient id="eqGradUp" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--success)" stopOpacity="0.34" />
          <stop offset="100%" stopColor="var(--success)" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="eqGradDown" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--danger)" stopOpacity="0.32" />
          <stop offset="100%" stopColor="var(--danger)" stopOpacity="0" />
        </linearGradient>
      </defs>

      {/* starting-balance baseline */}
      <line
        x1={pad.left}
        x2={W - pad.right}
        y1={geom.baselineY}
        y2={geom.baselineY}
        stroke="var(--border-hi)"
        strokeDasharray="3 5"
        strokeWidth={1}
      />

      <motion.path
        d={geom.area}
        fill={`url(#${fillId})`}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.9, delay: 0.5, ease: EASE }}
      />
      <motion.path
        d={geom.line}
        fill="none"
        stroke={stroke}
        strokeWidth={2.4}
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 1.4, ease: EASE }}
      />
      <motion.circle
        cx={geom.coords[geom.coords.length - 1].x}
        cy={geom.coords[geom.coords.length - 1].y}
        r={4.5}
        fill={stroke}
        initial={{ scale: 0, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.4, delay: 1.4, ease: EASE }}
      />
    </svg>
  )
}

/* ------------------------------ Daily PnL ------------------------------- */

export function DailyPnlBars({ data }: { data: DailyPnlPoint[] }) {
  const W = 840
  const H = 240
  const pad = { top: 18, right: 12, bottom: 26, left: 12 }

  const geom = useMemo(() => {
    if (!data.length) return null
    const maxAbs = Math.max(...data.map((d) => Math.abs(d.pnl)), 1)
    const innerW = W - pad.left - pad.right
    const innerH = H - pad.top - pad.bottom
    const zeroY = pad.top + innerH / 2
    const slot = innerW / data.length
    const barW = Math.min(34, slot * 0.62)
    return { maxAbs, innerH, zeroY, slot, barW }
  }, [data])

  if (!geom) return <ChartEmpty label="No closed trades yet — daily PnL will appear here." />

  return (
    <svg className="nv-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Daily profit and loss">
      <line x1={pad.left} x2={W - pad.right} y1={geom.zeroY} y2={geom.zeroY} stroke="var(--border-hi)" strokeWidth={1} />
      {data.map((d, i) => {
        const cx = pad.left + geom.slot * i + geom.slot / 2
        const h = (Math.abs(d.pnl) / geom.maxAbs) * (geom.innerH / 2)
        const up = d.pnl >= 0
        const y = up ? geom.zeroY - h : geom.zeroY
        return (
          <motion.rect
            key={d.date}
            x={cx - geom.barW / 2}
            width={geom.barW}
            rx={4}
            fill={up ? 'var(--success)' : 'var(--danger)'}
            initial={{ y: geom.zeroY, height: 0, opacity: 0.2 }}
            animate={{ y, height: Math.max(h, 1.5), opacity: 1 }}
            transition={{ duration: 0.7, delay: 0.1 + i * 0.05, ease: EASE }}
          >
            <title>{`${d.date}: ${formatCurrency(d.pnl)} · ${d.trades} trade${d.trades === 1 ? '' : 's'}`}</title>
          </motion.rect>
        )
      })}
    </svg>
  )
}

/* ---------------------------- Win/Loss donut ---------------------------- */

export function WinLossDonut({ wins, losses, breakeven }: { wins: number; losses: number; breakeven: number }) {
  const total = wins + losses + breakeven
  const R = 54
  const C = 2 * Math.PI * R
  const segs = total
    ? [
        { key: 'win', value: wins, color: 'var(--success)' },
        { key: 'loss', value: losses, color: 'var(--danger)' },
        { key: 'flat', value: breakeven, color: 'var(--text-3)' },
      ].filter((s) => s.value > 0)
    : []

  let offset = 0
  const winRate = total ? Math.round((wins / total) * 100) : 0

  return (
    <div className="nv-donut">
      <svg viewBox="0 0 140 140" role="img" aria-label="Win/loss distribution">
        <circle cx="70" cy="70" r={R} fill="none" stroke="var(--bg-tertiary)" strokeWidth="14" />
        {segs.map((s, i) => {
          const len = (s.value / total) * C
          const dash = `${len} ${C - len}`
          const thisOffset = offset
          offset += len
          return (
            <motion.circle
              key={s.key}
              cx="70"
              cy="70"
              r={R}
              fill="none"
              stroke={s.color}
              strokeWidth="14"
              strokeLinecap="butt"
              strokeDasharray={dash}
              transform="rotate(-90 70 70)"
              initial={{ strokeDashoffset: C, opacity: 0.4 }}
              animate={{ strokeDashoffset: -thisOffset, opacity: 1 }}
              transition={{ duration: 0.9, delay: 0.2 + i * 0.18, ease: EASE }}
            />
          )
        })}
        <text x="70" y="64" textAnchor="middle" className="nv-donut-value">{winRate}%</text>
        <text x="70" y="84" textAnchor="middle" className="nv-donut-label">win rate</text>
      </svg>
      <div className="nv-donut-legend">
        <LegendDot color="var(--success)" label="Wins" value={wins} />
        <LegendDot color="var(--danger)" label="Losses" value={losses} />
        {breakeven > 0 ? <LegendDot color="var(--text-3)" label="Flat" value={breakeven} /> : null}
      </div>
    </div>
  )
}

function LegendDot({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <span className="nv-legend-row">
      <span className="nv-legend-dot" style={{ background: color }} />
      <span className="nv-legend-label">{label}</span>
      <span className="nv-legend-value">{value}</span>
    </span>
  )
}

/* --------------------------- CE / PE split bar -------------------------- */

export function SideSplit({ ce, pe }: { ce: SideStats; pe: SideStats }) {
  const rows: Array<{ key: string; label: string; stats: SideStats; tone: string }> = [
    { key: 'CE', label: 'CE · Calls', stats: ce, tone: 'var(--accent-paper)' },
    { key: 'PE', label: 'PE · Puts', stats: pe, tone: 'var(--accent)' },
  ]
  const maxTrades = Math.max(ce.trades, pe.trades, 1)
  return (
    <div className="nv-side-split">
      {rows.map((row, i) => (
        <div className="nv-side-row" key={row.key}>
          <div className="nv-side-head">
            <span className="nv-side-label">{row.label}</span>
            <span className={`nv-side-pnl ${row.stats.pnl >= 0 ? 'pos' : 'neg'}`}>{formatCurrency(row.stats.pnl)}</span>
          </div>
          <div className="nv-side-track">
            <motion.div
              className="nv-side-fill"
              style={{ background: row.tone }}
              initial={{ width: 0 }}
              animate={{ width: `${(row.stats.trades / maxTrades) * 100}%` }}
              transition={{ duration: 0.8, delay: 0.15 + i * 0.12, ease: EASE }}
            />
          </div>
          <div className="nv-side-meta">
            {row.stats.trades} trade{row.stats.trades === 1 ? '' : 's'} · {row.stats.win_rate.toFixed(0)}% win
          </div>
        </div>
      ))}
    </div>
  )
}

function ChartEmpty({ label }: { label: string }) {
  return <div className="nv-chart-empty">{label}</div>
}
