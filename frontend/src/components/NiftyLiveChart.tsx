import { useEffect, useMemo, useState } from 'react'
import { Activity } from 'lucide-react'
import { getNiftyCandles, getNiftyMarkers, type NiftyCandle, type NiftyCandleSeries, type NiftyTradeMarker } from '../api'

const CANDLE_POLL_MS = 20_000
const MARKER_POLL_MS = 12_000
const VISIBLE_CANDLES = 75 // one 5m session

/** Snap a marker to the nearest candle by time; null when out of range. */
export function nearestCandleIndex(candles: NiftyCandle[], time: number): number | null {
  if (candles.length === 0) return null
  let best = 0
  let bestDelta = Math.abs(candles[0].time - time)
  for (let i = 1; i < candles.length; i += 1) {
    const delta = Math.abs(candles[i].time - time)
    if (delta < bestDelta) {
      best = i
      bestDelta = delta
    }
  }
  // Ignore markers further than 30 minutes from any visible candle.
  return bestDelta <= 30 * 60 ? best : null
}

export function NiftyLiveChart() {
  const [series, setSeries] = useState<NiftyCandleSeries | null>(null)
  const [markers, setMarkers] = useState<NiftyTradeMarker[]>([])
  const [loadFailed, setLoadFailed] = useState(false)

  useEffect(() => {
    let cancelled = false

    async function loadCandles() {
      if (document.hidden) return
      try {
        const next = await getNiftyCandles()
        if (!cancelled) {
          setSeries(next)
          setLoadFailed(false)
        }
      } catch {
        if (!cancelled) setLoadFailed(true)
      }
    }

    async function loadMarkers() {
      if (document.hidden) return
      try {
        const next = await getNiftyMarkers()
        if (!cancelled) setMarkers(next)
      } catch {
        // Markers are additive; keep the last known set on poll failure.
      }
    }

    void loadCandles()
    void loadMarkers()
    const candleTimer = window.setInterval(() => void loadCandles(), CANDLE_POLL_MS)
    const markerTimer = window.setInterval(() => void loadMarkers(), MARKER_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(candleTimer)
      window.clearInterval(markerTimer)
    }
  }, [])

  const candles = useMemo(() => (series?.candles ?? []).slice(-VISIBLE_CANDLES), [series])

  if (!series && !loadFailed) {
    return (
      <section className="nifty-chart-card">
        <ChartHeader series={null} />
        <div className="nifty-chart-empty">Loading NIFTY 5m chart...</div>
      </section>
    )
  }

  if (loadFailed || !series || (series.status !== 'ready' && candles.length === 0)) {
    return (
      <section className="nifty-chart-card">
        <ChartHeader series={series} />
        <div className="nifty-chart-empty">NIFTY chart temporarily unavailable. Market data reconnecting.</div>
      </section>
    )
  }

  return (
    <section className="nifty-chart-card">
      <ChartHeader series={series} />
      {series.market_state === 'closed' ? (
        <p className="nifty-chart-note">Market closed - showing latest available candles.</p>
      ) : null}
      <CandleSvg candles={candles} markers={markers} />
    </section>
  )
}

function ChartHeader({ series }: { series: NiftyCandleSeries | null }) {
  const last = series?.candles?.length ? series.candles[series.candles.length - 1] : null
  return (
    <div className="nifty-chart-header">
      <span className="nifty-chart-title">
        <Activity size={14} />
        NIFTY - 5m
      </span>
      <span className="nifty-chart-meta">
        {last ? <strong>{last.close.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong> : null}
        {series?.updated_at ? ` - ${shortTime(series.updated_at)}` : ''}
        {' - Source: Dhan 5m candles'}
      </span>
    </div>
  )
}

function CandleSvg({ candles, markers }: { candles: NiftyCandle[]; markers: NiftyTradeMarker[] }) {
  const width = 720
  const height = 260
  const padTop = 12
  const padBottom = 26
  const padLeft = 8
  const padRight = 56

  const lows = candles.map((c) => c.low)
  const highs = candles.map((c) => c.high)
  const min = Math.min(...lows)
  const max = Math.max(...highs)
  const spread = Math.max(max - min, 1)
  const plotW = width - padLeft - padRight
  const plotH = height - padTop - padBottom
  const step = plotW / Math.max(candles.length, 1)
  const bodyW = Math.max(Math.min(step * 0.6, 9), 2)

  const y = (price: number) => padTop + (1 - (price - min) / spread) * plotH
  const x = (index: number) => padLeft + index * step + step / 2

  const placedMarkers = markers
    .map((marker) => {
      const index = nearestCandleIndex(candles, marker.time)
      if (index === null) return null
      return { marker, index }
    })
    .filter((item): item is { marker: NiftyTradeMarker; index: number } => item !== null)

  const gridLevels = [min, min + spread / 2, max]
  const lastClose = candles.length ? candles[candles.length - 1].close : null

  return (
    <svg
      className="nifty-chart-svg"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="NIFTY 5 minute candlestick chart"
    >
      {gridLevels.map((level) => (
        <g key={level}>
          <line x1={padLeft} x2={width - padRight} y1={y(level)} y2={y(level)} className="nifty-grid-line" />
          <text x={width - padRight + 4} y={y(level) + 3} className="nifty-grid-label">
            {Math.round(level).toLocaleString('en-IN')}
          </text>
        </g>
      ))}
      {candles.map((candle, index) => {
        const up = candle.close >= candle.open
        const bodyTop = y(Math.max(candle.open, candle.close))
        const bodyBottom = y(Math.min(candle.open, candle.close))
        return (
          <g key={candle.time} className={up ? 'nifty-candle up' : 'nifty-candle down'}>
            <line x1={x(index)} x2={x(index)} y1={y(candle.high)} y2={y(candle.low)} />
            <rect
              x={x(index) - bodyW / 2}
              y={bodyTop}
              width={bodyW}
              height={Math.max(bodyBottom - bodyTop, 1)}
            />
          </g>
        )
      })}
      {lastClose !== null ? (
        <line x1={padLeft} x2={width - padRight} y1={y(lastClose)} y2={y(lastClose)} className="nifty-last-line" />
      ) : null}
      {placedMarkers.map(({ marker, index }, i) => {
        const buy = marker.side === 'BUY'
        const candle = candles[index]
        const markerY = buy ? y(candle.low) + 14 : y(candle.high) - 14
        return (
          <g key={`${marker.time}-${marker.side}-${i}`} className={buy ? 'nifty-marker buy' : 'nifty-marker sell'}>
            <path
              d={
                buy
                  ? `M ${x(index) - 5} ${markerY + 5} L ${x(index) + 5} ${markerY + 5} L ${x(index)} ${markerY - 3} Z`
                  : `M ${x(index) - 5} ${markerY - 5} L ${x(index) + 5} ${markerY - 5} L ${x(index)} ${markerY + 3} Z`
              }
            />
            <text x={x(index)} y={buy ? markerY + 16 : markerY - 10} textAnchor="middle">
              {marker.label}
              {marker.mode === 'paper' ? ' (P)' : ''}
            </text>
          </g>
        )
      })}
      {candles.length > 0 ? (
        <>
          <text x={padLeft} y={height - 8} className="nifty-grid-label">
            {timeLabel(candles[0].time)}
          </text>
          <text x={width - padRight} y={height - 8} textAnchor="end" className="nifty-grid-label">
            {timeLabel(candles[candles.length - 1].time)}
          </text>
        </>
      ) : null}
    </svg>
  )
}

function timeLabel(epoch: number): string {
  return new Date(epoch * 1000).toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Asia/Kolkata',
  })
}

function shortTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  return parsed.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' })
}
