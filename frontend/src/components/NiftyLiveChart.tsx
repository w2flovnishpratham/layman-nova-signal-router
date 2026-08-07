import { Button } from '@/components/ui/button'
import { Loader2 } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import { memo, useEffect, useRef, useState, useMemo } from 'react'
import {
  getNiftyCandles,
  getNiftyMarkers,
  type ChartTimeframe,
  type NiftyCandle,
  type NiftyCandleSeries,
  type NiftyTradeMarker,
} from '../api'
import type { EngineMode } from '../types'
import { sameCandles, sessionOnly } from './NiftyLiveChart.helpers'
import { buildChartLayout, hitTestMarker, istTime, type ChartLayout } from './NiftyLiveChart.layout'
import { paintChart } from './NiftyLiveChart.paint'

const CANDLE_POLL_MS = 20_000
const MARKER_POLL_MS = 12_000
// With the market closed the candle series is finished and no new trades can
// arrive, so polling at live cadence just burns requests all night. Still
// polled, not stopped: the backoff has to be short enough to notice the next
// session opening without the user reloading.
const CLOSED_CANDLE_POLL_MS = 120_000
const CLOSED_MARKER_POLL_MS = 120_000
const CHART_TIMEFRAMES: ChartTimeframe[] = ['1m', '5m', '15m']

function supportedTimeframe(value: string | null | undefined): ChartTimeframe {
  return value === '1m' || value === '15m' ? value : '5m'
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
  // Drives the poll backoff below. Deliberately a boolean off the series rather
  // than the series object, so it only changes when the session flips.
  const marketClosed = series?.market_state === 'closed'
  const [markers, setMarkers] = useState<NiftyTradeMarker[]>([])
  const [loadFailed, setLoadFailed] = useState(false)
  const tradingDateRef = useRef<string | null>(null)

  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [dims, setDims] = useState({ width: 0, height: 0 })
  const layoutRef = useRef<ChartLayout | null>(null)
  const [activeGroup, setActiveGroup] = useState<{ x: number; y: number; markers: NiftyTradeMarker[] } | null>(null)

  // ---------------------------------------------------------------------
  // Polling fetch. Unchanged from the lightweight-charts version: this is
  // pure data plumbing, independent of how candles/markers get rendered.
  // ---------------------------------------------------------------------
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
    // marketClosed is a boolean derived from the series, not the series object,
    // so this effect re-arms only when the session actually flips -- not on
    // every poll.
    const candleTimer = window.setInterval(
      () => void loadCandles(),
      marketClosed ? CLOSED_CANDLE_POLL_MS : CANDLE_POLL_MS,
    )
    const markerTimer = window.setInterval(
      () => void loadMarkers(),
      marketClosed ? CLOSED_MARKER_POLL_MS : MARKER_POLL_MS,
    )
    return () => {
      cancelled = true
      candleRequest?.abort()
      markerRequest?.abort()
      window.clearInterval(candleTimer)
      window.clearInterval(markerTimer)
    }
  }, [engineMode, timeframe, marketClosed])

  // Keeps the same array reference across polls that return identical
  // candles, so the repaint effect below (deps: candles, ...) doesn't fire
  // on every 20s poll -- only when something actually changed.
  const previousCandlesRef = useRef<NiftyCandle[]>([])
  const candles = useMemo(() => {
    const next = series ? sessionOnly(series) : []
    if (sameCandles(previousCandlesRef.current, next)) return previousCandlesRef.current
    previousCandlesRef.current = next
    return next
  }, [series])
  const hasCandles = candles.length > 0

  // Latest candles/markers/timeframe mirrored into refs so the canvas's
  // native event listeners (attached once, see below) always read fresh
  // values without needing to be re-attached on every data change.
  const candlesForEventsRef = useRef<NiftyCandle[]>([])
  candlesForEventsRef.current = candles
  const markersForEventsRef = useRef<NiftyTradeMarker[]>([])
  markersForEventsRef.current = markers
  const timeframeForEventsRef = useRef<ChartTimeframe>(timeframe)
  timeframeForEventsRef.current = timeframe
  const dimsRef = useRef(dims)
  dimsRef.current = dims

  // ---------------------------------------------------------------------
  // Canvas lifecycle: size it (with devicePixelRatio scaling), keep it
  // sized on resize, and wire up click/hover. Runs once per hasCandles
  // mount -- there's no persistent chart instance to recreate, just a
  // canvas element, so this is simpler than the old two-ResizeObserver
  // version (one observed the container, the other let the chart library
  // resize its own instance).
  // ---------------------------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !hasCandles) return

    let resizeFrame: number | null = null
    const applySize = () => {
      const rect = canvas.getBoundingClientRect()
      if (rect.width <= 0 || rect.height <= 0) return
      const dpr = window.devicePixelRatio || 1
      canvas.width = Math.floor(rect.width * dpr)
      canvas.height = Math.floor(rect.height * dpr)
      const ctx = canvas.getContext('2d')
      ctx?.setTransform(dpr, 0, 0, dpr, 0, 0)
      setDims({ width: rect.width, height: rect.height })
    }
    applySize()
    const resizeObserver = new ResizeObserver(() => {
      if (resizeFrame !== null) window.cancelAnimationFrame(resizeFrame)
      resizeFrame = window.requestAnimationFrame(() => {
        resizeFrame = null
        applySize()
      })
    })
    resizeObserver.observe(canvas)

    // Only a click does anything -- no pan/zoom/scroll wiring, so the chart
    // stays exactly as static as the lightweight-charts version was (that
    // one had to explicitly disable handleScroll/handleScale/kineticScroll;
    // canvas has nothing like that to disable in the first place). Hover
    // only drives the crosshair readout, gated to real mice so touch taps
    // never get a flash of crosshair before the click fires.
    const redraw = (hoverX: number | null) => {
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      const layout = buildChartLayout({
        candles: candlesForEventsRef.current,
        markers: markersForEventsRef.current,
        timeframe: timeframeForEventsRef.current,
        dims: dimsRef.current,
        hoverX,
      })
      layoutRef.current = layout
      paintChart(ctx, layout)
    }
    const handlePointerMove = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') return
      const rect = canvas.getBoundingClientRect()
      redraw(event.clientX - rect.left)
    }
    const handlePointerLeave = () => redraw(null)
    const handleClick = (event: MouseEvent) => {
      const layout = layoutRef.current
      if (!layout) return
      const rect = canvas.getBoundingClientRect()
      const glyph = hitTestMarker(layout, event.clientX - rect.left, event.clientY - rect.top)
      setActiveGroup(glyph && glyph.kind === 'badge' ? { x: event.clientX, y: event.clientY, markers: glyph.markers } : null)
    }
    canvas.addEventListener('pointermove', handlePointerMove)
    canvas.addEventListener('pointerleave', handlePointerLeave)
    canvas.addEventListener('click', handleClick)

    return () => {
      if (resizeFrame !== null) window.cancelAnimationFrame(resizeFrame)
      resizeObserver.disconnect()
      canvas.removeEventListener('pointermove', handlePointerMove)
      canvas.removeEventListener('pointerleave', handlePointerLeave)
      canvas.removeEventListener('click', handleClick)
      layoutRef.current = null
      setActiveGroup(null)
    }
  }, [hasCandles])

  // ---------------------------------------------------------------------
  // Recompute layout and repaint whenever the data or size actually
  // changes. No setVisibleLogicalRange/scrollToRealTime/candlesRef-reset
  // dance here: buildChartLayout is a pure function of its arguments, so
  // every timeframe switch just gets that timeframe's own full-session
  // width fresh -- there's no persistent chart instance left to inherit a
  // stale zoom/scroll position from, so that whole bug class can't recur.
  // ---------------------------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx || dims.width <= 0 || dims.height <= 0) return
    const layout = buildChartLayout({ candles, markers, timeframe, dims })
    layoutRef.current = layout
    paintChart(ctx, layout)
  }, [candles, markers, dims, timeframe])

  useEffect(() => setActiveGroup(null), [timeframe])

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
      <canvas
        ref={canvasRef}
        className="nifty-chart-canvas nova-live-chart"
        role="img"
        aria-label={`NIFTY ${series.interval} candlestick chart`}
      />
      {activeGroup ? (
        <div className="nifty-marker-popover-backdrop" onClick={() => setActiveGroup(null)}>
          <div
            className="nifty-marker-popover"
            style={{ left: activeGroup.x, top: activeGroup.y }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="nifty-marker-popover-head">
              <span>{activeGroup.markers.length} trades</span>
              <button type="button" className="nifty-marker-popover-close" onClick={() => setActiveGroup(null)} aria-label="Close">×</button>
            </div>
            <ul className="nifty-marker-popover-list">
              {activeGroup.markers.map((marker, i) => (
                <li key={marker.id ?? i}>
                  <span className={`nifty-marker-popover-side is-${marker.side === 'BUY' ? 'buy' : 'sell'}`}>
                    {marker.side}{marker.option_side ? ` ${marker.option_side}` : ''}
                  </span>
                  <span className="nifty-marker-popover-contract">{marker.contract ?? istTime(marker.time)}</span>
                  <span className="nifty-marker-popover-price">
                    {marker.execution_price != null ? `₹${marker.execution_price.toFixed(2)}` : '—'}
                  </span>
                  {marker.pnl != null ? (
                    <span className={`nifty-marker-popover-pnl ${marker.pnl >= 0 ? 'is-profit' : 'is-loss'}`}>
                      {marker.pnl >= 0 ? '+' : '-'}₹{Math.abs(marker.pnl).toLocaleString('en-IN')}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </section>
  )
}

// Parent (App) re-renders on every wallet/P&L/WS tick; memo stops those
// unrelated updates from re-mounting this chart's render pass.
export const NiftyLiveChart = memo(NiftyLiveChartInner)
