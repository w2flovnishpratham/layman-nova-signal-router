// Pure layout math for the NIFTY chart: candles/markers/dims in, a fully
// positioned description of what to draw out. No canvas calls here on
// purpose -- keeps this unit-testable with plain assertions, and keeps
// NiftyLiveChart.paint.ts's job to nothing but issuing ctx.* calls for
// whatever this computed.
import type { ChartTimeframe, NiftyCandle, NiftyTradeMarker } from '../api'
import { markerCandleIndex, markerStyle, sessionBarCount } from './NiftyLiveChart.helpers'

const PAD_LEFT = 8
const PAD_RIGHT = 56
const PAD_TOP = 20
const PAD_BOTTOM = 26
const MARKER_OFFSET = 10
const BADGE_RADIUS = 9
const TRIANGLE_HALF = 5
const TRIANGLE_HEIGHT = 9

export function istTime(epoch: number): string {
  return new Date(epoch * 1000).toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Asia/Kolkata',
  })
}

export interface CandleRect {
  x: number
  bodyWidth: number
  bodyTop: number
  bodyBottom: number
  wickTop: number
  wickBottom: number
  bull: boolean
  time: number
}

export type MarkerGlyph =
  | {
      kind: 'triangle-up' | 'triangle-down' | 'square' | 'circle'
      x: number
      y: number
      color: string
      marker: NiftyTradeMarker
      text: string
      hitRadius: number
    }
  | {
      kind: 'badge'
      x: number
      y: number
      color: string
      markers: NiftyTradeMarker[]
      count: number
      hitRadius: number
    }

export interface PriceLineLayout {
  y: number
  price: number
  color: string
  title: string
}

export interface ChartLayout {
  dims: { width: number; height: number }
  plot: { left: number; right: number; top: number; bottom: number }
  priceRange: { min: number; max: number }
  candles: CandleRect[]
  markers: MarkerGlyph[]
  priceLines: PriceLineLayout[]
  grid: { y: number; price: number }[]
  timeAxisLabels: { x: number; text: string }[]
  priceAxisLabels: { y: number; text: string }[]
  crosshair: { x: number; y: number; price: number; candle: NiftyCandle } | null
}

const PRICE_AXIS_STEP = 10

// Scroll-zoom: how many bar-slots are visible, and which bar-slot sits at
// the view's right edge. Both are indices into the same fixed session-bar
// space sessionBarCount() already reserves, not into the candles array --
// that's what lets a zoomed-in view stay put as new candles arrive rather
// than needing to be re-anchored on every poll.
export interface ZoomState {
  visibleBars: number
  viewEndBar: number
}

const MIN_VISIBLE_BARS = 15
const ZOOM_STEP = 0.85

// Undefined/null current zoom means "fully zoomed out" -- the whole session,
// exactly today's default. Clamped so visibleBars can never exceed the
// session's own bar count, which is the "can't zoom out past 9:15-15:30"
// limit: at max visibleBars, viewStartBar is forced to 0 and viewEndBar to
// the last bar, i.e. this function alone enforces that ceiling.
export function zoomChartView(args: {
  current: ZoomState | null
  deltaY: number
  hoverX: number
  timeframe: ChartTimeframe
  candleCount: number
  plot: { left: number; right: number }
}): ZoomState | null {
  const totalBars = Math.max(sessionBarCount(args.timeframe), args.candleCount)
  const plotWidth = Math.max(1, args.plot.right - args.plot.left)
  const prevVisible = args.current?.visibleBars ?? totalBars
  const prevViewEnd = args.current?.viewEndBar ?? totalBars - 1
  const prevBarSlot = plotWidth / prevVisible
  const prevViewStart = prevViewEnd - prevVisible + 1
  const cursorBar = prevViewStart + (args.hoverX - args.plot.left) / prevBarSlot

  const factor = args.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP
  const nextVisible = Math.min(totalBars, Math.max(MIN_VISIBLE_BARS, Math.round(prevVisible * factor)))
  if (nextVisible >= totalBars) return null

  const nextBarSlot = plotWidth / nextVisible
  const rawViewStart = cursorBar - (args.hoverX - args.plot.left) / nextBarSlot
  const nextViewStart = Math.max(0, Math.min(totalBars - nextVisible, rawViewStart))
  return { visibleBars: nextVisible, viewEndBar: nextViewStart + nextVisible - 1 }
}

function emptyLayout(dims: { width: number; height: number }): ChartLayout {
  return {
    dims,
    plot: { left: PAD_LEFT, right: dims.width - PAD_RIGHT, top: PAD_TOP, bottom: dims.height - PAD_BOTTOM },
    priceRange: { min: 0, max: 1 },
    candles: [],
    markers: [],
    priceLines: [],
    grid: [],
    timeAxisLabels: [],
    priceAxisLabels: [],
    crosshair: null,
  }
}

export function buildChartLayout(args: {
  candles: NiftyCandle[]
  markers: NiftyTradeMarker[]
  timeframe: ChartTimeframe
  dims: { width: number; height: number }
  hoverX?: number | null
  hoverY?: number | null
  zoom?: ZoomState | null
}): ChartLayout {
  const { candles, markers, timeframe, dims } = args
  if (candles.length === 0 || dims.width <= 0 || dims.height <= 0) return emptyLayout(dims)

  const plotLeft = PAD_LEFT
  const plotRight = dims.width - PAD_RIGHT
  const plotTop = PAD_TOP
  const plotBottom = dims.height - PAD_BOTTOM
  const plotWidth = Math.max(1, plotRight - plotLeft)
  const plotHeight = Math.max(1, plotBottom - plotTop)

  let lo = Math.min(...candles.map((c) => c.low))
  let hi = Math.max(...candles.map((c) => c.high))

  // Entry/SL/TP for the latest open BUY can sit well outside the visible
  // candle range (a wide SL on a cheap option, say) -- fold them into the
  // axis bounds up front so the line is never clipped instead of just drawn
  // off-canvas.
  const latestMarker = [...markers].sort((left, right) => left.time - right.time).at(-1)
  const levels: { price: number | null | undefined; color: string; title: string }[] = []
  if (latestMarker && latestMarker.side === 'BUY') {
    const entryColor = latestMarker.option_side === 'PE' ? '#fb7185' : '#34d399'
    levels.push(
      { price: latestMarker.price, color: entryColor, title: `BUY ${latestMarker.option_side ?? ''}`.trim() },
      { price: latestMarker.stop_price, color: '#ef4444', title: 'SL' },
      { price: latestMarker.target_price, color: '#22c55e', title: 'TP' },
    )
  }
  for (const level of levels) {
    if (level.price != null && Number.isFinite(level.price) && level.price > 0) {
      lo = Math.min(lo, level.price)
      hi = Math.max(hi, level.price)
    }
  }

  const pricePad = (hi - lo) * 0.08 || 1
  lo -= pricePad
  hi += pricePad
  const priceRange = { min: lo, max: hi }

  function yFor(price: number): number {
    return plotTop + (1 - (price - lo) / (hi - lo)) * plotHeight
  }
  function priceForY(y: number): number {
    return lo + (1 - (y - plotTop) / plotHeight) * (hi - lo)
  }

  const totalBars = Math.max(sessionBarCount(timeframe), candles.length)
  const visibleBars = Math.min(totalBars, args.zoom?.visibleBars ?? totalBars)
  const viewEndBar = Math.min(totalBars - 1, Math.max(visibleBars - 1, args.zoom?.viewEndBar ?? totalBars - 1))
  const viewStartBar = viewEndBar - visibleBars + 1
  const barSlot = plotWidth / visibleBars
  const bodyWidth = Math.max(2, Math.min(10, barSlot * 0.62))

  function xFor(index: number): number {
    return plotLeft + (index - viewStartBar) * barSlot + barSlot / 2
  }
  function inView(index: number): boolean {
    return index >= viewStartBar && index <= viewEndBar
  }

  const candleRects: CandleRect[] = candles.reduce<CandleRect[]>((rects, c, i) => {
    if (!inView(i)) return rects
    const bull = c.close >= c.open
    rects.push({
      x: xFor(i),
      bodyWidth,
      bodyTop: yFor(Math.max(c.open, c.close)),
      bodyBottom: yFor(Math.min(c.open, c.close)),
      wickTop: yFor(c.high),
      wickBottom: yFor(c.low),
      bull,
      time: c.time,
    })
    return rects
  }, [])

  // --- markers: group by candle index, single trade -> small directional
  // glyph, multiple trades on one candle -> one clickable count badge. ---
  const groups = new Map<number, NiftyTradeMarker[]>()
  for (const marker of markers) {
    const index = markerCandleIndex(candles, marker.time, timeframe)
    if (index === null || !inView(index)) continue
    const group = groups.get(index)
    if (group) group.push(marker)
    else groups.set(index, [marker])
  }

  const markerGlyphs: MarkerGlyph[] = []
  groups.forEach((group, index) => {
    const candle = candles[index]
    const x = xFor(index)
    if (group.length === 1) {
      const marker = group[0]
      const style = markerStyle(marker)
      const y = style.position === 'belowBar' ? yFor(candle.low) + MARKER_OFFSET : yFor(candle.high) - MARKER_OFFSET
      const kind: MarkerGlyph['kind'] =
        style.shape === 'arrowUp' ? 'triangle-up'
          : style.shape === 'arrowDown' ? 'triangle-down'
          : style.shape === 'square' ? 'square'
          : 'circle'
      const priceText = marker.price != null
        ? marker.price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : null
      const text = `${style.text}${priceText ? `  ${priceText}` : ''}${marker.mode === 'paper' ? ' (P)' : ''}`
      markerGlyphs.push({
        kind,
        x, y,
        color: style.color,
        marker,
        text,
        hitRadius: Math.max(TRIANGLE_HALF, BADGE_RADIUS),
      })
      return
    }
    markerGlyphs.push({
      kind: 'badge',
      x,
      y: yFor(candle.high) - MARKER_OFFSET - 2,
      color: '#8fb4f7',
      markers: group,
      count: group.length,
      hitRadius: BADGE_RADIUS,
    })
  })

  // --- price lines: entry/SL/TP for the latest still-open BUY marker ---
  const priceLines: PriceLineLayout[] = []
  for (const level of levels) {
    if (level.price != null && Number.isFinite(level.price) && level.price > 0) {
      priceLines.push({ y: yFor(level.price), price: level.price, color: level.color, title: level.title })
    }
  }

  // --- grid + price axis labels: fixed 10-point rungs, not a tick-count
  // target -- NIFTY's own point scale makes 10 the readable, expected step
  // regardless of how much range a given timeframe happens to show. ---
  const grid: { y: number; price: number }[] = []
  const priceAxisLabels: { y: number; text: string }[] = []
  const step = PRICE_AXIS_STEP
  const firstLine = Math.ceil(lo / step) * step
  for (let p = firstLine; p < hi; p += step) {
    const y = yFor(p)
    grid.push({ y, price: p })
    priceAxisLabels.push({ y, text: p.toLocaleString('en-IN', { maximumFractionDigits: 0 }) })
  }

  // --- sparse time axis labels, only for candles actually in view ---
  const timeAxisLabels: { x: number; text: string }[] = []
  const visibleCandleCount = Math.min(candles.length, viewEndBar + 1) - Math.max(0, viewStartBar)
  const labelEvery = Math.max(1, Math.round(visibleCandleCount / 6))
  for (let i = Math.max(0, viewStartBar); i <= Math.min(candles.length - 1, viewEndBar); i += labelEvery) {
    timeAxisLabels.push({ x: xFor(i), text: istTime(candles[i].time) })
  }

  // --- crosshair: X snaps to the nearest candle's center (so you're always
  // reading a real bar, not a gap between two) -- Y tracks the raw cursor
  // height so the price readout is the exact value under the pointer, not
  // locked to that candle's close. ---
  let crosshair: ChartLayout['crosshair'] = null
  if (args.hoverX != null) {
    const index = Math.max(0, Math.min(candles.length - 1, viewStartBar + Math.round((args.hoverX - plotLeft - barSlot / 2) / barSlot)))
    const snapped = candles[index]
    const y = args.hoverY != null ? Math.max(plotTop, Math.min(plotBottom, args.hoverY)) : yFor(snapped.close)
    crosshair = { x: xFor(index), y, price: priceForY(y), candle: snapped }
  }

  return {
    dims,
    plot: { left: plotLeft, right: plotRight, top: plotTop, bottom: plotBottom },
    priceRange,
    candles: candleRects,
    markers: markerGlyphs,
    priceLines,
    grid,
    timeAxisLabels,
    priceAxisLabels,
    crosshair,
  }
}

export function hitTestMarker(layout: ChartLayout, x: number, y: number): MarkerGlyph | null {
  let best: MarkerGlyph | null = null
  let bestDist = Infinity
  for (const glyph of layout.markers) {
    const dist = Math.hypot(glyph.x - x, glyph.y - y)
    if (dist <= glyph.hitRadius && dist < bestDist) {
      best = glyph
      bestDist = dist
    }
  }
  return best
}

export { TRIANGLE_HALF, TRIANGLE_HEIGHT, BADGE_RADIUS }
