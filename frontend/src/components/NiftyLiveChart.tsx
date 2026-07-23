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
import {
  applyLiveTick,
  chartConnectionLabel,
  FIVE_MINUTE_SECONDS,
  markerCandleIndex,
  markerStyle,
  sameCandles,
  sessionOnly,
} from './NiftyLiveChart.helpers'

const CANDLE_POLL_MS = 20_000
const MARKER_POLL_MS = 12_000

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
  const snapshotSource = useSessionStore((state) => state.marketSnapshotSource)
  const wsStatus = useSessionStore((state) => state.wsStatus)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const lastTickEpochRef = useRef<number | null>(null)
  const candlesRef = useRef<NiftyCandle[]>([])
  const [containerReady, setContainerReady] = useState(false)

  useEffect(() => {
    let cancelled = false
    let candleRequest: AbortController | null = null
    let markerRequest: AbortController | null = null

    async function loadCandles() {
      if (document.hidden || candleRequest) return
      candleRequest = new AbortController()
      try {
        const next = await getNiftyCandles(candleRequest.signal)
        if (cancelled) return
        if (next.trading_date && tradingDateRef.current && next.trading_date !== tradingDateRef.current) setMarkers([])
        tradingDateRef.current = next.trading_date ?? tradingDateRef.current
        setSeries(next)
        setLoadFailed(false)
      } catch (error) {
        if (!cancelled && !(error instanceof DOMException && error.name === 'AbortError')) setLoadFailed(true)
      } finally {
        candleRequest = null
      }
    }

    async function loadMarkers() {
      if (document.hidden || markerRequest) return
      if (engineMode !== 'paper' && engineMode !== 'live') return
      markerRequest = new AbortController()
      try {
        const next = await getNiftyMarkers(engineMode, markerRequest.signal)
        if (cancelled) return
        if (tradingDateRef.current && next.trading_date && next.trading_date !== tradingDateRef.current) {
          setMarkers([])
          return
        }
        setMarkers(next.markers ?? [])
      } catch {
        // Keep confirmed markers while a poll recovers.
      } finally {
        markerRequest = null
      }
    }

    void loadCandles()
    void loadMarkers()
    const candleTimer = window.setInterval(() => void loadCandles(), CANDLE_POLL_MS)
    const markerTimer = window.setInterval(() => void loadMarkers(), MARKER_POLL_MS)
    return () => {
      cancelled = true
      candleRequest?.abort()
      markerRequest?.abort()
      window.clearInterval(candleTimer)
      window.clearInterval(markerTimer)
    }
  }, [engineMode])

  const candles = useMemo(() => (series ? sessionOnly(series) : []), [series])
  const hasCandles = candles.length > 0

  useEffect(() => {
    const container = containerRef.current
    if (!container || !hasCandles) {
      setContainerReady(false)
      return
    }
    const observeSize = () => {
      const rect = container.getBoundingClientRect()
      if (rect.width > 0 && rect.height > 0) setContainerReady(true)
      if (chartRef.current && rect.width > 0 && rect.height > 0) {
        chartRef.current.resize(Math.floor(rect.width), Math.floor(rect.height))
      }
    }
    observeSize()
    const observer = new ResizeObserver(observeSize)
    observer.observe(container)
    return () => observer.disconnect()
  }, [hasCandles])

  useEffect(() => {
    const container = containerRef.current
    if (!container || !hasCandles || !containerReady || chartRef.current) return
    const bounds = container.getBoundingClientRect()
    if (bounds.width <= 0 || bounds.height <= 0) return
    const chart = createChart(container, {
      width: Math.floor(bounds.width),
      height: Math.floor(bounds.height),
      layout: { background: { color: 'transparent' }, textColor: 'rgba(148, 155, 175, 0.9)', fontSize: 10 },
      grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(255, 255, 255, 0.06)' } },
      rightPriceScale: { borderVisible: false },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: UTCTimestamp) => istTime(time),
      },
      localization: { timeFormatter: (time: UTCTimestamp) => istTime(time) },
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
      priceLineVisible: true,
      lastValueVisible: true,
    })
    chartRef.current = chart
    seriesRef.current = candleSeries
    markersPluginRef.current = createSeriesMarkers(candleSeries, [])
    let resizeFrame: number | null = null
    const resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry) return
      const width = Math.floor(entry.contentRect.width)
      const height = Math.floor(entry.contentRect.height)
      if (width <= 0 || height <= 0) return
      if (resizeFrame !== null) window.cancelAnimationFrame(resizeFrame)
      resizeFrame = window.requestAnimationFrame(() => {
        resizeFrame = null
        chart.resize(width, height)
      })
    })
    resizeObserver.observe(container)
    return () => {
      resizeObserver.disconnect()
      if (resizeFrame !== null) window.cancelAnimationFrame(resizeFrame)
      markersPluginRef.current = null
      seriesRef.current = null
      chartRef.current = null
      candlesRef.current = []
      lastTickEpochRef.current = null
      chart.remove()
    }
  }, [containerReady, hasCandles])

  useEffect(() => {
    const candleSeries = seriesRef.current
    if (!candleSeries || candles.length === 0) return
    let merged = candles
    const previous = candlesRef.current
    const forming = previous[previous.length - 1]
    const lastServer = candles[candles.length - 1]
    if (forming && forming.time > lastServer.time && forming.time - lastServer.time <= 2 * FIVE_MINUTE_SECONDS) {
      merged = [...candles, forming]
    } else if (forming?.time === lastServer.time && snapshot?.marketStatus === 'open' && wsStatus === 'live' && snapshot.atm?.marketfeed?.connected) {
      merged = [...candles.slice(0, -1), {
        ...lastServer,
        high: Math.max(lastServer.high, forming.high),
        low: Math.min(lastServer.low, forming.low),
        close: forming.close,
      }]
    }
    if (sameCandles(previous, merged)) return
    candlesRef.current = [...merged]
    candleSeries.setData(merged.map(toBar))
    if (previous.length === 0) chartRef.current?.timeScale().fitContent()
    else if (merged[merged.length - 1].time > previous[previous.length - 1].time) chartRef.current?.timeScale().scrollToRealTime()
  }, [candles, containerReady, hasCandles, snapshot?.atm?.marketfeed?.connected, snapshot?.marketStatus, wsStatus])

  useEffect(() => {
    const candleSeries = seriesRef.current
    const spot = snapshot?.niftySpot
    const tickReceivedAt = snapshot?.atm?.niftySpotReceivedAt
    if (!candleSeries || spot == null || snapshot?.marketStatus !== 'open' || !tickReceivedAt) return
    const parsed = Date.parse(tickReceivedAt)
    if (!Number.isFinite(parsed)) return
    const tickEpoch = Math.floor(parsed / 1000)
    const update = applyLiveTick(candlesRef.current, spot, tickEpoch, lastTickEpochRef.current)
    if (!update) return
    lastTickEpochRef.current = tickEpoch
    candlesRef.current = update.candles
    candleSeries.update(toBar(update.candle))
    if (update.appended) chartRef.current?.timeScale().scrollToRealTime()
  }, [snapshot, hasCandles])

  useEffect(() => {
    const plugin = markersPluginRef.current
    if (!plugin) return
    const visibleCandles = candlesRef.current
    const placed = markers
      .map((marker): SeriesMarker<Time> | null => {
        const index = markerCandleIndex(visibleCandles, marker.time)
        if (index === null) return null
        const style = markerStyle(marker)
        const fill = [marker.contract, marker.execution_price != null ? `₹${marker.execution_price.toFixed(2)}` : null].filter(Boolean).join(' @ ')
        const details = [fill, marker.pnl != null ? `P&L ${marker.pnl >= 0 ? '+' : '-'}₹${Math.abs(marker.pnl).toLocaleString('en-IN')}` : null].filter(Boolean).join('\n')
        return {
          ...style,
          time: visibleCandles[index].time as UTCTimestamp,
          text: `${style.text}${marker.mode === 'paper' ? ' (P)' : ''}${details ? `\n${details}` : ''}`,
        }
      })
      .filter((marker): marker is SeriesMarker<Time> => marker !== null)
      .sort((left, right) => (left.time as number) - (right.time as number))
    plugin.setMarkers(placed)
  }, [markers, candles, containerReady, hasCandles])

  const tickReceivedAt = snapshot?.atm?.niftySpotReceivedAt
  const lastPrice = (tickReceivedAt ? snapshot?.niftySpot : null) ?? (hasCandles ? candles[candles.length - 1].close : null)
  const lastStampIso = tickReceivedAt ?? (series?.market_state === 'closed' ? series.updated_at : null)
  const connectionLabel = chartConnectionLabel({
    loading: !series && !loadFailed,
    unavailable: loadFailed || series?.status === 'unavailable',
    marketClosed: series?.market_state === 'closed' || snapshot?.marketStatus === 'closed',
    stale: Boolean(series?.stale),
    wsStatus,
    feedConnected: snapshot?.atm?.marketfeed?.connected,
    snapshotSource,
    updatedAt: lastStampIso,
  })
  const connectionClass = connectionLabel.toLowerCase().replaceAll(' ', '-')
  const header = (
    <div className="nifty-chart-header">
      <span className="nifty-chart-title"><Activity size={14} />NIFTY 50 · 5m</span>
      <span className="nifty-chart-meta">
        {lastPrice != null ? <strong>{lastPrice.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong> : null}
        {lastPrice != null ? ' · ' : null}
        <span className={`nifty-conn ${connectionClass}`}>{connectionLabel}</span>
        {lastStampIso ? ` · Updated ${shortTime(lastStampIso)} IST` : ''}
      </span>
    </div>
  )

  if (!series && !loadFailed) return <section className="nifty-chart-card">{header}<div className="nifty-chart-empty">Loading NIFTY 5m chart...</div></section>
  if (loadFailed || !series || series.status === 'unavailable') return <section className="nifty-chart-card">{header}<div className="nifty-chart-empty">Market data unavailable</div></section>
  if (!hasCandles) return <section className="nifty-chart-card">{header}<div className="nifty-chart-empty">No NIFTY 5m candles available for today yet.</div></section>

  return (
    <section className="nifty-chart-card">
      {header}
      {series.market_state === 'closed' ? <p className="nifty-chart-note">Market closed - showing today's latest candles.</p> : null}
      <div ref={containerRef} className="nifty-chart-canvas nova-live-chart" role="img" aria-label="NIFTY 5 minute candlestick chart" />
    </section>
  )
}

function shortTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  return parsed.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'Asia/Kolkata' })
}
