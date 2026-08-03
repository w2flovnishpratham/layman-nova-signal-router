import { motion } from 'framer-motion'
import { useMemo } from 'react'
import { formatCurrency } from '../lib/format'
import type { DailyPnlPoint, EquityPoint } from './portfolioApi'

const EASE = [0.16, 1, 0.3, 1] as const
const EQUITY_PAD = { top: 22, right: 14, bottom: 28, left: 0 } as const
const DAILY_PAD = { top: 16, right: 0, bottom: 27, left: 0 } as const

export function EquityCurveChart({ points }: { points: EquityPoint[] }) {
  const width = 840
  const height = 300

  const geometry = useMemo(() => {
    if (points.length < 2) return null
    const values = points.map((point) => point.equity)
    let min = Math.min(...values)
    let max = Math.max(...values)
    if (min === max) {
      min -= 1
      max += 1
    }
    const range = max - min
    min -= range * 0.12
    max += range * 0.12

    const innerWidth = width - EQUITY_PAD.left - EQUITY_PAD.right
    const innerHeight = height - EQUITY_PAD.top - EQUITY_PAD.bottom
    const x = (index: number) => EQUITY_PAD.left + (index / (points.length - 1)) * innerWidth
    const y = (value: number) => EQUITY_PAD.top + innerHeight - ((value - min) / (max - min)) * innerHeight
    const coordinates = points.map((point, index) => ({ x: x(index), y: y(point.equity) }))
    const line = coordinates
      .map((coordinate, index) => `${index === 0 ? 'M' : 'L'} ${coordinate.x.toFixed(2)} ${coordinate.y.toFixed(2)}`)
      .join(' ')
    const area = `${line} L ${coordinates[coordinates.length - 1].x.toFixed(2)} ${height - EQUITY_PAD.bottom} L ${coordinates[0].x.toFixed(2)} ${height - EQUITY_PAD.bottom} Z`
    const labelIndexes = Array.from(new Set([
      0,
      Math.floor((points.length - 1) / 3),
      Math.floor(((points.length - 1) * 2) / 3),
      points.length - 1,
    ]))
    return {
      area,
      coordinates,
      labelIndexes,
      line,
      rising: values[values.length - 1] >= values[0],
      x,
    }
  }, [points])

  if (!geometry) {
    return <ChartEmpty label="No equity history yet — tracked closed trades will plot here." />
  }

  const stroke = geometry.rising ? 'var(--nv-blue)' : 'var(--danger)'
  const fillId = geometry.rising ? 'nvEquityBlue' : 'nvEquityRed'

  return (
    <svg
      className="nv-chart"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Equity curve"
    >
      <defs>
        <linearGradient id="nvEquityBlue" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--nv-blue)" stopOpacity="0.3" />
          <stop offset="100%" stopColor="var(--nv-blue)" stopOpacity="0.015" />
        </linearGradient>
        <linearGradient id="nvEquityRed" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--danger)" stopOpacity="0.25" />
          <stop offset="100%" stopColor="var(--danger)" stopOpacity="0.015" />
        </linearGradient>
      </defs>

      {[0.25, 0.5, 0.75].map((ratio) => (
        <line
          key={ratio}
          className="nv-chart-grid"
          x1={EQUITY_PAD.left}
          x2={width - EQUITY_PAD.right}
          y1={EQUITY_PAD.top + (height - EQUITY_PAD.top - EQUITY_PAD.bottom) * ratio}
          y2={EQUITY_PAD.top + (height - EQUITY_PAD.top - EQUITY_PAD.bottom) * ratio}
        />
      ))}

      <motion.path
        d={geometry.area}
        fill={`url(#${fillId})`}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.7, delay: 0.2, ease: EASE }}
      />
      <motion.path
        d={geometry.line}
        fill="none"
        stroke={stroke}
        strokeWidth={2.2}
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 1.05, ease: EASE }}
      />

      {geometry.labelIndexes.map((index, labelIndex) => (
        <text
          key={`${points[index].t}-${index}`}
          className="nv-chart-label"
          x={geometry.x(index)}
          y={height - 4}
          textAnchor={labelIndex === 0 ? 'start' : labelIndex === geometry.labelIndexes.length - 1 ? 'end' : 'middle'}
        >
          {formatChartDate(points[index].t)}
        </text>
      ))}
    </svg>
  )
}

export function DailyPnlBars({ data }: { data: DailyPnlPoint[] }) {
  const width = 840
  const height = 240

  const geometry = useMemo(() => {
    if (!data.length) return null
    const maxAbsolute = Math.max(...data.map((point) => Math.abs(point.pnl)), 1)
    const innerWidth = width - DAILY_PAD.left - DAILY_PAD.right
    const innerHeight = height - DAILY_PAD.top - DAILY_PAD.bottom
    const slot = innerWidth / data.length
    return {
      barWidth: Math.min(45, slot * 0.72),
      innerHeight,
      maxAbsolute,
      slot,
    }
  }, [data])

  if (!geometry) {
    return <ChartEmpty label="No closed trades yet — daily P&L will appear here." />
  }

  return (
    <svg
      className="nv-chart"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Daily profit and loss"
    >
      <line
        className="nv-chart-grid"
        x1={DAILY_PAD.left}
        x2={width - DAILY_PAD.right}
        y1={height - DAILY_PAD.bottom}
        y2={height - DAILY_PAD.bottom}
      />
      {data.map((point, index) => {
        const center = DAILY_PAD.left + geometry.slot * index + geometry.slot / 2
        const barHeight = (Math.abs(point.pnl) / geometry.maxAbsolute) * geometry.innerHeight
        const y = height - DAILY_PAD.bottom - barHeight
        const profitable = point.pnl >= 0
        const showLabel = index === 0 || index === data.length - 1 || index === Math.floor(data.length / 2)
        return (
          <g key={point.date}>
            <motion.rect
              x={center - geometry.barWidth / 2}
              width={geometry.barWidth}
              rx={4}
              fill={profitable ? 'var(--success)' : 'var(--danger)'}
              initial={{ y: height - DAILY_PAD.bottom, height: 0, opacity: 0.2 }}
              animate={{ y, height: Math.max(barHeight, 1.5), opacity: profitable ? 0.78 : 0.72 }}
              transition={{ duration: 0.65, delay: 0.08 + index * 0.035, ease: EASE }}
            >
              <title>
                {`${point.date}: ${formatCurrency(point.pnl)} · ${point.trades} trade${point.trades === 1 ? '' : 's'}`}
              </title>
            </motion.rect>
            {showLabel ? (
              <text
                className="nv-chart-label"
                x={center}
                y={height - 4}
                textAnchor={index === 0 ? 'start' : index === data.length - 1 ? 'end' : 'middle'}
              >
                {formatChartDate(point.date)}
              </text>
            ) : null}
          </g>
        )
      })}
    </svg>
  )
}

function formatChartDate(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 6)
  return date.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    timeZone: 'Asia/Kolkata',
  })
}

function ChartEmpty({ label }: { label: string }) {
  return <div className="nv-chart-empty">{label}</div>
}
