import { useEffect, useMemo, useRef, useState } from 'react'
import { Activity } from 'lucide-react'
import {
  CandlestickSeries,
  CrosshairMode,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { getNiftyCandles, getNiftyMarkers, type NiftyCandle, type NiftyCandleSeries, type NiftyTradeMarker } from '../api'
import { useSessionStore } from '../state/sessionStore'
import type { EngineMode } from '../types'

const CANDLE_POLL_MS = 20_000
const MARKER_POLL_MS = 12_000
const CANDLE_SECONDS = 5 * 60

/** Keep only candles inside the response's own session window (defense in
 * depth; the backend already filters to today's IST session). */
export function sessionOnly(series: NiftyCandleSeries): NiftyCandle[] {
  const start = series.session_start ? Math.floor(new Date(series.session_start).getTime() / 1000) : null
  const end = series.session_end ? Math.floor(new Date(series.session_end).getTime() / 1000) : null
  const seen = new Set<number>()
  return (series.candles ?? [])
    .filter((c) => {
      if (start !== null && c.time < start) return false
      if (end !== null && c.time > end) return false
      if (seen.has(c.time)) return false
      seen.add(c.time)
      return true
    })
    .sort((a, b) => a.time - b.time)
}

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

interface MarkerStyle {
  position: 'aboveBar' | 'belowBar'
  shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square'
  color: string
  text: string
}

/** Visual identity for each confirmed execution marker kind. */
export function markerStyle(marker: NiftyTradeMarker): MarkerStyle {
  if (marker.side === 'BUY') {
    return marker.option_side === 'PE'
      ? { position: 'aboveBar', shape: 'arrowDown', color: '#fb7185', text: 'BUY PE' }
      : { position: 'belowBar', shape: 'arrowUp', color: '#34d399', text: marker.option_side ? 'BUY CE' : 'BUY' }
  }
  switch (marker.exit_kind) {
    case 'SL':
      return { position: 'aboveBar', shape: 'square', color: '#ef4444', text: 'EXIT SL' }
    case 'TARGET':
      return { position: 'aboveBar', shape: 'square', color: '#22c55e', text: 'EXIT TGT' }
    case 'REVERSAL':
      return { position: 'aboveBar', shape: 'circle', color: '#a78bfa', text: 'REV EXIT' }
    default:
      return { position: 'aboveBar', shape: 'circle', color: '#94a3b8', text: marker.label || 'EXIT' }
  }
}

function istTime(epoch: number): string {
  return new Date(epoch * 1000).toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Asia/Kolkata',
  })
}

function toBar(candle: NiftyCandle) {
  return {
    time: candle.time as UTCTimestamp,
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
  }
}

export function NiftyLiveChart({ engineMode }: { engineMode: EngineMode | null }) {
  const [series, setSeries] = useState<NiftyCandleSeries | null>(null)
  const [markers, setMarkers] = useState<NiftyTradeMarker[]>([])
  const [loadFailed, setLoadFailed] = useState(false)
  const tradingDateRef = useRef<string | null>(null)

  const snapshot = useSessionStore((state) => state.marketSnapshot)
  const wsStatus = useSessionStore((state) => state.wsStatus)

  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  // Runtime candle list = last server poll + live ticks applied on top.
  const candlesRef = useRef<NiftyCandle[]>([])

  useEffect(() => {
    let cancelled = false

    async function loadCandles() {
      if (document.hidden) return
      try {
        const next = await getNiftyCandles()
        if (cancelled) return
        // New trading date -> REPLACE everything; never merge across days.
        if (next.trading_date && tradingDateRef.current && next.trading_date !== tradingDateRef.current) {
          setMarkers([])
        }
        tradingDateRef.current = next.trading_date ?? tradingDateRef.current
        setSeries(next)
        setLoadFailed(false)
      } catch {
        if (!cancelled) setLoadFailed(true)
      }
    }

    async function loadMarkers() {
      if (document.hidden) return
      if (engineMode !== 'paper' && engineMode !== 'live') return
      try {
        const next = await getNiftyMarkers(engineMode)
        if (cancelled) return
        // Drop markers that belong to another trading date entirely.
        if (tradingDateRef.current && next.trading_date && next.trading_date !== tradingDateRef.current) {
          setMarkers([])
          return
        }
        setMarkers(next.markers ?? [])
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
  }, [engineMode])

  const candles = useMemo(() => (series ? sessionOnly(series) : []), [series])
  const hasCandles = candles.length > 0

  // Chart lifecycle: create once when data first arrives, destroy on unmount.
  useEffect(() => {
    const container = containerRef.current
    if (!container || !hasCandles || chartRef.current) return
    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: 'rgba(148, 155, 175, 0.9)',
        fontSize: 10,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: 'rgba(255, 255, 255, 0.06)' },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: UTCTimestamp) => istTime(time),
      },
      localization: { timeFormatter: (time: UTCTimestamp) => istTime(time) },
      // Read-only: no zoom, scroll, pan, or crosshair.
      crosshair: { mode: CrosshairMode.Hidden },
      handleScroll: false,
      handleScale: false,
      kineticScroll: { touch: false, mouse: false },
    })
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#34d399',
      downColor: '#fb7185',
      wickUpColor: '#34d399',
      wickDownColor: '#fb7185',
      borderVisible: false,
      // Built-in last-value line doubles as the current market-price line.
      priceLineVisible: true,
      lastValueVisible: true,
    })
    chartRef.current = chart
    seriesRef.current = candleSeries
    markersPluginRef.current = createSeriesMarkers(candleSeries, [])
    return () => {
      markersPluginRef.current = null
      seriesRef.current = null
      chartRef.current = null
      candlesRef.current = []
      chart.remove()
    }
  }, [hasCandles])

  // Server candles: source of truth, but keep a locally formed newer candle
  // so a slightly stale poll never rolls the chart backwards.
  useEffect(() => {
    const candleSeries = seriesRef.current
    if (!candleSeries || candles.length === 0) return
    let merged = candles
    const forming = candlesRef.current[candlesRef.current.length - 1]
    const lastServer = candles[candles.length - 1]
    if (forming && forming.time > lastServer.time && forming.time - lastServer.time <= 2 * CANDLE_SECONDS) {
      merged = [...candles, forming]
    }
    candlesRef.current = [...merged]
    candleSeries.setData(merged.map(toBar))
    chartRef.current?.timeScale().fitContent()
  }, [candles, hasCandles])

  // Live tick from the session websocket: update the forming candle in place,
  // or open the next 5m candle when the bucket rolls over.
  useEffect(() => {
    const candleSeries = seriesRef.current
    const spot = snapshot?.niftySpot
    if (!candleSeries || spot == null || snapshot?.marketStatus !== 'open') return
    const last = candlesRef.current[candlesRef.current.length - 1]
    if (!last) return
    const parsed = Date.parse(snapshot.lastUpdatedAt ?? '')
    const epoch = Number.isFinite(parsed) ? Math.floor(parsed / 1000) : Math.floor(Date.now() / 1000)
    const bucket = Math.floor(epoch / CANDLE_SECONDS) * CANDLE_SECONDS
    if (bucket < last.time) return
    if (bucket === last.time) {
      const next = { ...last, high: Math.max(last.high, spot), low: Math.min(last.low, spot), close: spot }
      candlesRef.current[candlesRef.current.length - 1] = next
      candleSeries.update(toBar(next))
    } else {
      const next = { time: bucket, open: spot, high: spot, low: spot, close: spot, volume: 0 }
      candlesRef.current = [...candlesRef.current, next]
      candleSeries.update(toBar(next))
      chartRef.current?.timeScale().fitContent()
    }
  }, [snapshot])

  // Confirmed execution markers, snapped to the nearest visible candle.
  useEffect(() => {
    const plugin = markersPluginRef.current
    if (!plugin) return
    const placed = markers
      .map((marker): SeriesMarker<Time> | null => {
        const index = nearestCandleIndex(candles, marker.time)
        if (index === null) return null
        const style = markerStyle(marker)
        return {
          ...style,
          time: candles[index].time as UTCTimestamp,
          text: `${style.text}${marker.mode === 'paper' ? ' (P)' : ''}`,
        }
      })
      .filter((m): m is SeriesMarker<Time> => m !== null)
      .sort((a, b) => (a.time as number) - (b.time as number))
    plugin.setMarkers(placed)
  }, [markers, candles, hasCandles])

  const lastLocal = candlesRef.current[candlesRef.current.length - 1]
  const lastPrice = snapshot?.niftySpot ?? lastLocal?.close ?? (hasCandles ? candles[candles.length - 1].close : null)
  const lastStampIso = snapshot?.lastUpdatedAt ?? series?.updated_at ?? null

  const header = (
    <div className="nifty-chart-header">
      <span className="nifty-chart-title">
        <Activity size={14} />
        NIFTY - 5m
      </span>
      <span className="nifty-chart-meta">
        {lastPrice != null ? (
          <strong>{lastPrice.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong>
        ) : null}
        {lastStampIso ? ` - ${shortTime(lastStampIso)} IST` : ''}
        {' - '}
        <span className={`nifty-conn ${wsStatus}`}>
          {wsStatus === 'live' ? 'Live' : wsStatus === 'degraded' ? 'Reconnecting' : 'Offline'}
        </span>
      </span>
    </div>
  )

  if (!series && !loadFailed) {
    return (
      <section className="nifty-chart-card">
        {header}
        <div className="nifty-chart-empty">Loading NIFTY 5m chart...</div>
      </section>
    )
  }

  if (loadFailed || !series || series.status === 'unavailable') {
    return (
      <section className="nifty-chart-card">
        {header}
        <div className="nifty-chart-empty">NIFTY chart temporarily unavailable. Market data reconnecting.</div>
      </section>
    )
  }

  if (!hasCandles) {
    return (
      <section className="nifty-chart-card">
        {header}
        <div className="nifty-chart-empty">No NIFTY 5m candles available for today yet.</div>
      </section>
    )
  }

  return (
    <section className="nifty-chart-card">
      {header}
      {series.market_state === 'closed' ? (
        <p className="nifty-chart-note">Market closed - showing today's latest candles.</p>
      ) : null}
      <div ref={containerRef} className="nifty-chart-canvas" role="img" aria-label="NIFTY 5 minute candlestick chart" />
    </section>
  )
}

function shortTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  return parsed.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' })
}
