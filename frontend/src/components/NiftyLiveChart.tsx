import { Button } from '@/components/ui/button'
import { Loader2 } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries,
  CrosshairMode,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  LineStyle,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import {
  getNiftyCandles,
  getNiftyMarkers,
  type ChartTimeframe,
  type NiftyCandle,
  type NiftyCandleSeries,
  type NiftyTradeMarker,
} from '../api'
import type { EngineMode } from '../types'
import {
  markerCandleIndex,
  markerStyle,
  sameCandles,
  sessionOnly,
} from './NiftyLiveChart.helpers'

const CANDLE_POLL_MS = 20_000
const MARKER_POLL_MS = 12_000
const CHART_TIMEFRAMES: ChartTimeframe[] = ['1m', '5m', '15m']

function supportedTimeframe(value: string | null | undefined): ChartTimeframe {
  return value === '1m' || value === '15m' ? value : '5m'
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

function NiftyLiveChartInner({
  engineMode,
  defaultTimeframe,
}: {
  engineMode: EngineMode | null
  defaultTimeframe?: string | null
}) {
  const [timeframe, setTimeframe] = useState<ChartTimeframe>(() => supportedTimeframe(defaultTimeframe))
  const [series, setSeries] = useState<NiftyCandleSeries | null>(null)
  const [markers, setMarkers] = useState<NiftyTradeMarker[]>([])
  const [loadFailed, setLoadFailed] = useState(false)
  const tradingDateRef = useRef<string | null>(null)

  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const tradePriceLinesRef = useRef<IPriceLine[]>([])
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
        const next = await getNiftyCandles(timeframe, candleRequest.signal)
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
  }, [engineMode, timeframe])

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
      tradePriceLinesRef.current = []
      seriesRef.current = null
      chartRef.current = null
      candlesRef.current = []
      chart.remove()
    }
  }, [containerReady, hasCandles])

  useEffect(() => {
    const candleSeries = seriesRef.current
    if (!candleSeries || candles.length === 0) return
    const previous = candlesRef.current
    if (sameCandles(previous, candles)) return
    candlesRef.current = [...candles]
    candleSeries.setData(candles.map(toBar))
    if (previous.length === 0) chartRef.current?.timeScale().fitContent()
    else if (candles[candles.length - 1].time > previous[previous.length - 1].time) chartRef.current?.timeScale().scrollToRealTime()
  }, [candles, containerReady, hasCandles])

  useEffect(() => {
    const plugin = markersPluginRef.current
    if (!plugin) return
    const visibleCandles = candlesRef.current
    const placed = markers
      .map((marker): SeriesMarker<Time> | null => {
        const index = markerCandleIndex(
          visibleCandles,
          marker.time,
          supportedTimeframe(series?.interval),
        )
        if (index === null) return null
        const style = markerStyle(marker)
        const indexLevel = marker.price != null
          ? marker.price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : null
        const fill = [marker.contract, marker.execution_price != null ? `₹${marker.execution_price.toFixed(2)}` : null].filter(Boolean).join(' @ ')
        const details = [fill, marker.pnl != null ? `P&L ${marker.pnl >= 0 ? '+' : '-'}₹${Math.abs(marker.pnl).toLocaleString('en-IN')}` : null].filter(Boolean).join('\n')
        return {
          ...style,
          time: visibleCandles[index].time as UTCTimestamp,
          text: `${style.text}${indexLevel ? `  ${indexLevel}` : ''}${marker.mode === 'paper' ? ' (P)' : ''}${details ? `\n${details}` : ''}`,
        }
      })
      .filter((marker): marker is SeriesMarker<Time> => marker !== null)
      .sort((left, right) => (left.time as number) - (right.time as number))
    plugin.setMarkers(placed)
  }, [markers, candles, containerReady, hasCandles, series?.interval])

  useEffect(() => {
    const candleSeries = seriesRef.current
    if (!candleSeries) return
    tradePriceLinesRef.current.forEach((line) => candleSeries.removePriceLine(line))
    tradePriceLinesRef.current = []
    const latest = [...markers].sort((left, right) => left.time - right.time).at(-1)
    if (!latest || latest.side !== 'BUY') return
    const entryColor = latest.option_side === 'PE' ? '#fb7185' : '#34d399'
    const levels = [
      { price: latest.price, color: entryColor, title: `BUY ${latest.option_side ?? ''}`.trim() },
      { price: latest.stop_price, color: '#ef4444', title: 'SL' },
      { price: latest.target_price, color: '#22c55e', title: 'TP' },
    ]
    tradePriceLinesRef.current = levels.flatMap((level) => (
      level.price != null && Number.isFinite(level.price) && level.price > 0
        ? [candleSeries.createPriceLine({
            price: level.price,
            color: level.color,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            lineVisible: true,
            axisLabelVisible: true,
            title: level.title,
          })]
        : []
    ))
  }, [markers, containerReady, hasCandles])

  const header = (
    <div className="nifty-chart-header">
      <span className="nifty-chart-title">NIFTY 50 · {series?.interval ?? timeframe}</span>
      <div className="nifty-chart-timeframes" role="group" aria-label="Chart timeframe">
        {CHART_TIMEFRAMES.map((value) => (
          <Button variant="unstyled"
            key={value}
            type="button"
            className={timeframe === value ? 'is-active' : ''}
            aria-pressed={timeframe === value}
            onClick={() => setTimeframe(value)}
          >
            {value}
          </Button>
        ))}
        <Button variant="unstyled" type="button" disabled title="Hourly candles are not available yet">1H</Button>
        <Button variant="unstyled" type="button" disabled title="Daily candles are not available yet">D</Button>
      </div>
    </div>
  )

  // The header (symbol, timeframe switcher) is already real above; only the
  // plot area is pending, and it takes the canvas's own sizing class so the
  // placeholder occupies exactly the space the chart will.
  if (!series && !loadFailed) return (
    <section className="nifty-chart-card">
      {header}
      <Skeleton
        className="nifty-chart-canvas"
        role="status"
        aria-busy="true"
        aria-label={`Loading NIFTY ${timeframe} chart`}
      />
    </section>
  )
  if (loadFailed || !series || series.status === 'unavailable') return (
    <section className="nifty-chart-card">
      {header}
      <div className="nifty-chart-empty">
        <p>{series?.message ?? `Authoritative NIFTY ${timeframe} candles unavailable.`}</p>
        <span className="nifty-chart-retry-hint"><Loader2 size={13} className="nifty-chart-retry-spinner" /> Retrying automatically…</span>
      </div>
    </section>
  )
  if (!hasCandles) return <section className="nifty-chart-card">{header}<div className="nifty-chart-empty">No NIFTY {timeframe} candles available for this session.</div></section>

  return (
    <section className="nifty-chart-card">
      {header}
      {series.market_state === 'closed' ? <p className="nifty-chart-note">Market closed - showing today's latest candles.</p> : null}
      <div ref={containerRef} className="nifty-chart-canvas nova-live-chart" role="img" aria-label={`NIFTY ${series.interval} candlestick chart`} />
    </section>
  )
}

// Parent (App) re-renders on every wallet/P&L/WS tick; memo stops those
// unrelated updates from re-mounting this chart's render pass.
export const NiftyLiveChart = memo(NiftyLiveChartInner)
