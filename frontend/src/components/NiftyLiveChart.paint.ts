// Thin imperative layer: takes a ChartLayout (from NiftyLiveChart.layout.ts)
// and issues the canvas calls to draw it. No layout math here on purpose --
// if a coordinate is wrong, the bug is in layout.ts, not here.
import type { ChartLayout, MarkerGlyph } from './NiftyLiveChart.layout'
import { BADGE_RADIUS, TRIANGLE_HALF, TRIANGLE_HEIGHT } from './NiftyLiveChart.layout'

const MARKER_TEXT_COLOR = '#f7f3ff'
type ShapeGlyph = Extract<MarkerGlyph, { kind: 'triangle-up' | 'triangle-down' | 'square' | 'circle' }>

const BULL_COLOR = '#34d399'
const BEAR_COLOR = '#fb7185'
const GRID_COLOR = 'rgba(255,255,255,0.055)'
const AXIS_TEXT_COLOR = 'rgba(247,243,255,0.38)'
const TIME_TEXT_COLOR = 'rgba(247,243,255,0.34)'
const CROSSHAIR_COLOR = 'rgba(255,255,255,0.16)'
const MONO_FONT = '10.5px "SF Mono", "Cascadia Mono", ui-monospace, Consolas, monospace'
const SANS_FONT = '700 10px -apple-system, "Segoe UI", sans-serif'

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

function drawTriangle(ctx: CanvasRenderingContext2D, x: number, y: number, up: boolean, color: string): void {
  ctx.fillStyle = color
  ctx.beginPath()
  if (up) {
    ctx.moveTo(x, y - TRIANGLE_HALF)
    ctx.lineTo(x - TRIANGLE_HALF, y + TRIANGLE_HALF)
    ctx.lineTo(x + TRIANGLE_HALF, y + TRIANGLE_HALF)
  } else {
    ctx.moveTo(x, y + TRIANGLE_HALF)
    ctx.lineTo(x - TRIANGLE_HALF, y - TRIANGLE_HALF)
    ctx.lineTo(x + TRIANGLE_HALF, y - TRIANGLE_HALF)
  }
  ctx.closePath()
  ctx.fill()
}

// Multiple trades on one candle: a small thin (stroked, not filled) arrow
// with a notification-style count badge peeking out of its top-left
// corner -- an unread-count bubble on a bell icon, not a plain numbered
// circle standing in for a trade direction that no longer means anything
// once several trades are collapsed into it.
function drawClusterGlyph(ctx: CanvasRenderingContext2D, x: number, y: number, count: number, color: string): void {
  const armLen = 8
  ctx.strokeStyle = color
  ctx.lineWidth = 1.5
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.beginPath()
  ctx.moveTo(x, y + armLen / 2)
  ctx.lineTo(x, y - armLen / 2)
  ctx.moveTo(x - 3, y - armLen / 2 + 3.5)
  ctx.lineTo(x, y - armLen / 2)
  ctx.lineTo(x + 3, y - armLen / 2 + 3.5)
  ctx.stroke()

  const badgeR = BADGE_RADIUS * 0.72
  const badgeCx = x - armLen / 2
  const badgeCy = y - armLen / 2
  ctx.beginPath()
  ctx.arc(badgeCx, badgeCy, badgeR, 0, Math.PI * 2)
  ctx.fillStyle = color
  ctx.fill()
  ctx.fillStyle = MARKER_TEXT_COLOR
  ctx.font = SANS_FONT
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(String(count), badgeCx, badgeCy + 0.5)
  ctx.textAlign = 'left'
  ctx.textBaseline = 'alphabetic'
}

// A single trade renders as a solid filled pill -- icon + short label +
// price, same look TradingView's own marker labels have (arrow, filled
// background, readable at a glance) rather than a bare unlabeled shape.
// Only `triangle-up` (a bullish CE entry) sits below its candle; every
// other kind sits above -- that split comes straight from markerStyle()
// in NiftyLiveChart.helpers.ts, so it's inferred here rather than passed
// through the glyph.
function drawLabeledMarker(ctx: CanvasRenderingContext2D, glyph: ShapeGlyph): void {
  const belowBar = glyph.kind === 'triangle-up'
  ctx.font = MONO_FONT
  const textWidth = ctx.measureText(glyph.text).width
  const iconR = TRIANGLE_HALF
  const padX = 6
  const gap = 5
  const pillH = 16
  const pillW = padX + iconR * 2 + gap + textWidth + padX
  const left = glyph.x - pillW / 2
  const top = belowBar ? glyph.y : glyph.y - pillH

  ctx.fillStyle = glyph.color
  roundRect(ctx, left, top, pillW, pillH, pillH / 2)
  ctx.fill()

  const iconCx = left + padX + iconR
  const iconCy = top + pillH / 2
  ctx.fillStyle = MARKER_TEXT_COLOR
  if (glyph.kind === 'triangle-up') drawTriangle(ctx, iconCx, iconCy, true, MARKER_TEXT_COLOR)
  else if (glyph.kind === 'triangle-down') drawTriangle(ctx, iconCx, iconCy, false, MARKER_TEXT_COLOR)
  else if (glyph.kind === 'square') {
    ctx.fillRect(iconCx - TRIANGLE_HEIGHT / 2, iconCy - TRIANGLE_HEIGHT / 2, TRIANGLE_HEIGHT, TRIANGLE_HEIGHT)
  } else {
    ctx.beginPath()
    ctx.arc(iconCx, iconCy, iconR * 0.7, 0, Math.PI * 2)
    ctx.fill()
  }

  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  ctx.fillText(glyph.text, iconCx + iconR + gap, iconCy + 0.5)
  ctx.textBaseline = 'alphabetic'
}

export function paintChart(ctx: CanvasRenderingContext2D, layout: ChartLayout): void {
  const { dims, plot } = layout
  ctx.clearRect(0, 0, dims.width, dims.height)
  if (layout.candles.length === 0) return

  // grid + price axis
  ctx.strokeStyle = GRID_COLOR
  ctx.lineWidth = 1
  ctx.font = MONO_FONT
  ctx.fillStyle = AXIS_TEXT_COLOR
  ctx.textAlign = 'left'
  ctx.textBaseline = 'alphabetic'
  for (let i = 0; i < layout.grid.length; i++) {
    const y = Math.round(layout.grid[i].y) + 0.5
    ctx.beginPath()
    ctx.moveTo(plot.left, y)
    ctx.lineTo(plot.right, y)
    ctx.stroke()
    ctx.fillText(layout.priceAxisLabels[i].text, plot.right + 8, layout.grid[i].y + 3.5)
  }

  // time axis
  ctx.fillStyle = TIME_TEXT_COLOR
  for (const label of layout.timeAxisLabels) {
    ctx.fillText(label.text, label.x - 14, dims.height - 8)
  }

  // crosshair (drawn under candles so wicks stay legible on top)
  if (layout.crosshair) {
    ctx.save()
    ctx.strokeStyle = CROSSHAIR_COLOR
    ctx.setLineDash([3, 3])
    ctx.beginPath()
    ctx.moveTo(layout.crosshair.x, plot.top)
    ctx.lineTo(layout.crosshair.x, plot.bottom)
    ctx.stroke()
    ctx.restore()
  }

  // candles
  for (const c of layout.candles) {
    const color = c.bull ? BULL_COLOR : BEAR_COLOR
    ctx.strokeStyle = color
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(c.x, c.wickTop)
    ctx.lineTo(c.x, c.wickBottom)
    ctx.stroke()
    const bodyHeight = Math.max(1.4, c.bodyBottom - c.bodyTop)
    ctx.fillStyle = color
    roundRect(ctx, c.x - c.bodyWidth / 2, c.bodyTop, c.bodyWidth, bodyHeight, 1.6)
    ctx.fill()
  }

  // price lines (entry/SL/TP)
  ctx.setLineDash([4, 3])
  ctx.lineWidth = 1
  for (const line of layout.priceLines) {
    ctx.strokeStyle = line.color
    ctx.beginPath()
    ctx.moveTo(plot.left, Math.round(line.y) + 0.5)
    ctx.lineTo(plot.right, Math.round(line.y) + 0.5)
    ctx.stroke()
    ctx.fillStyle = line.color
    ctx.font = MONO_FONT
    ctx.fillText(line.title, plot.right + 8, line.y + 3.5)
  }
  ctx.setLineDash([])

  // marker glyphs -- drawn after candles and price lines on purpose, so
  // they always sit on top rather than getting clipped by a wick or a
  // dashed SL/TP line at the same y. Badge checked first so TS narrows the
  // rest of the union to the single shape-glyph member (its `kind` is
  // itself a 4-literal union, which only narrows cleanly once the other
  // member is eliminated).
  for (const glyph of layout.markers) {
    if (glyph.kind === 'badge') {
      drawClusterGlyph(ctx, glyph.x, glyph.y, glyph.count, glyph.color)
      continue
    }
    drawLabeledMarker(ctx, glyph)
  }

  // crosshair OHLC readout, top-left, drawn last so it sits above everything
  if (layout.crosshair) {
    const { candle } = layout.crosshair
    ctx.font = MONO_FONT
    const text = `O ${candle.open.toFixed(2)}  H ${candle.high.toFixed(2)}  L ${candle.low.toFixed(2)}  C ${candle.close.toFixed(2)}`
    const metrics = ctx.measureText(text)
    const boxW = metrics.width + 16
    ctx.fillStyle = 'rgba(10,9,17,0.78)'
    roundRect(ctx, plot.left + 4, 4, boxW, 20, 6)
    ctx.fill()
    ctx.fillStyle = '#f7f3ff'
    ctx.fillText(text, plot.left + 12, 18)
  }
}
