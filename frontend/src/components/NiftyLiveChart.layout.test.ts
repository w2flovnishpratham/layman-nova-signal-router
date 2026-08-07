import { describe, expect, it } from 'vitest'
import type { NiftyCandle, NiftyTradeMarker } from '../api'
import { buildChartLayout, hitTestMarker, zoomChartView } from './NiftyLiveChart.layout'
import { sessionBarCount } from './NiftyLiveChart.helpers'

const SESSION_START = Date.parse('2026-07-21T09:15:00+05:30') / 1000
const DIMS = { width: 720, height: 320 }

function candle(offsetSeconds: number, open: number, high: number, low: number, close: number): NiftyCandle {
  return { time: SESSION_START + offsetSeconds, open, high, low, close, volume: 65 }
}

function marker(overrides: Partial<NiftyTradeMarker> & Pick<NiftyTradeMarker, 'time' | 'side'>): NiftyTradeMarker {
  return { label: '', ...overrides }
}

describe('buildChartLayout', () => {
  it('reserves the whole session width, not just however many candles exist so far', () => {
    const candles = [candle(0, 100, 102, 99, 101), candle(300, 101, 103, 100, 102), candle(600, 102, 104, 101, 103)]
    const layout = buildChartLayout({ candles, markers: [], timeframe: '5m', dims: DIMS })

    expect(layout.candles).toHaveLength(3)
    const totalBars = sessionBarCount('5m')
    const barSlot = (DIMS.width - 8 - 56) / totalBars // PAD_LEFT=8, PAD_RIGHT=56
    // Three candles occupy only the first three slots of a ~77-slot session --
    // the third candle's x should sit nowhere near the right edge.
    expect(layout.candles[2].x).toBeLessThan(DIMS.width * 0.2)
    expect(layout.candles[2].x).toBeCloseTo(8 + 2 * barSlot + barSlot / 2, 1)
  })

  it('spaces the price axis at a fixed 10-point step regardless of range', () => {
    const candles = [candle(0, 24600, 24695, 24595, 24636)]
    const layout = buildChartLayout({ candles, markers: [], timeframe: '5m', dims: DIMS })

    const prices = layout.grid.map((line) => line.price)
    expect(prices.length).toBeGreaterThan(1)
    for (let i = 1; i < prices.length; i++) {
      expect(prices[i] - prices[i - 1]).toBeCloseTo(10, 5)
    }
    expect(prices.every((p) => p % 10 === 0)).toBe(true)
  })

  it('never needs a reset on timeframe switch -- each call is pure', () => {
    const candles = [candle(0, 100, 102, 99, 101)]
    const oneMinute = buildChartLayout({ candles, markers: [], timeframe: '1m', dims: DIMS })
    const fifteenMinute = buildChartLayout({ candles, markers: [], timeframe: '15m', dims: DIMS })

    // Different timeframes reserve different total bar counts, so the same
    // single candle gets a different width purely from this call's
    // arguments -- nothing persisted from the previous call could have
    // been carried over even if it wanted to.
    const widthAt = (layout: ReturnType<typeof buildChartLayout>) => layout.candles[0].bodyWidth
    expect(widthAt(oneMinute)).not.toBe(widthAt(fifteenMinute))
    expect(sessionBarCount('1m')).toBeGreaterThan(sessionBarCount('15m'))
  })

  it('builds entry/SL/TP price lines for the latest open BUY marker only', () => {
    const candles = [candle(0, 100, 102, 99, 101), candle(300, 101, 103, 100, 102)]
    const markers: NiftyTradeMarker[] = [
      marker({ time: SESSION_START, side: 'SELL', exit_kind: 'TARGET' }),
      marker({
        time: SESSION_START + 310,
        side: 'BUY',
        option_side: 'CE',
        price: 24110,
        stop_price: 24060,
        target_price: 24210,
      }),
    ]
    const layout = buildChartLayout({ candles, markers, timeframe: '5m', dims: DIMS })

    expect(layout.priceLines).toHaveLength(3)
    expect(layout.priceLines).toContainEqual(expect.objectContaining({ price: 24110, title: 'BUY CE', color: '#34d399' }))
    expect(layout.priceLines).toContainEqual(expect.objectContaining({ price: 24060, title: 'SL', color: '#ef4444' }))
    expect(layout.priceLines).toContainEqual(expect.objectContaining({ price: 24210, title: 'TP', color: '#22c55e' }))
  })

  it('expands the y-axis to fit a SL/TP that sits outside the visible candle range, instead of clipping it', () => {
    const candles = [candle(0, 100, 102, 99, 101), candle(300, 101, 103, 100, 102)]
    const markers: NiftyTradeMarker[] = [
      marker({ time: SESSION_START + 310, side: 'BUY', option_side: 'CE', price: 101, stop_price: 40, target_price: 160 }),
    ]
    const layout = buildChartLayout({ candles, markers, timeframe: '5m', dims: DIMS })

    expect(layout.priceRange.min).toBeLessThan(40)
    expect(layout.priceRange.max).toBeGreaterThan(160)
    const slLine = layout.priceLines.find((line) => line.title === 'SL')
    const tpLine = layout.priceLines.find((line) => line.title === 'TP')
    expect(slLine?.y).toBeGreaterThanOrEqual(0)
    expect(slLine?.y).toBeLessThanOrEqual(DIMS.height)
    expect(tpLine?.y).toBeGreaterThanOrEqual(0)
    expect(tpLine?.y).toBeLessThanOrEqual(DIMS.height)
  })

  it('omits price lines when the latest marker is an exit, not an open BUY', () => {
    const candles = [candle(0, 100, 102, 99, 101)]
    const markers = [marker({ time: SESSION_START, side: 'SELL', exit_kind: 'EOD' })]
    const layout = buildChartLayout({ candles, markers, timeframe: '5m', dims: DIMS })
    expect(layout.priceLines).toHaveLength(0)
  })

  it('collapses multiple trades on one candle into a single badge glyph', () => {
    const candles = [candle(0, 100, 102, 99, 101), candle(300, 101, 103, 100, 102)]
    const markers: NiftyTradeMarker[] = [
      marker({ time: SESSION_START + 30, side: 'BUY', option_side: 'CE' }),
      marker({ time: SESSION_START + 90, side: 'SELL', exit_kind: 'SL' }),
      marker({ time: SESSION_START + 150, side: 'BUY', option_side: 'PE' }),
    ]
    const layout = buildChartLayout({ candles, markers, timeframe: '5m', dims: DIMS })

    expect(layout.markers).toHaveLength(1)
    const [glyph] = layout.markers
    expect(glyph.kind).toBe('badge')
    if (glyph.kind === 'badge') {
      expect(glyph.count).toBe(3)
      expect(glyph.markers).toHaveLength(3)
    }
  })

  it('keeps a single trade on a candle as its own directional glyph, not a badge', () => {
    const candles = [candle(0, 100, 102, 99, 101), candle(300, 101, 103, 100, 102)]
    const markers = [marker({ time: SESSION_START + 310, side: 'BUY', option_side: 'CE', price: 22932.15 })]
    const layout = buildChartLayout({ candles, markers, timeframe: '5m', dims: DIMS })

    expect(layout.markers).toHaveLength(1)
    const [glyph] = layout.markers
    expect(glyph.kind).toBe('triangle-up')
    if (glyph.kind !== 'badge') expect(glyph.text).toBe('BUY CE  22,932.15')
  })
})

describe('crosshair', () => {
  it('snaps X to the hovered candle\'s center, and without hoverY falls back to its close', () => {
    const candles = [candle(0, 100, 102, 99, 101), candle(300, 101, 108, 100, 105)]
    const layout = buildChartLayout({ candles, markers: [], timeframe: '5m', dims: DIMS, hoverX: 400 })

    expect(layout.crosshair).not.toBeNull()
    expect(layout.crosshair?.candle).toBe(candles[1])
    expect(layout.crosshair?.x).toBe(layout.candles[1].x)
    const expectedY = layout.candles[1].bodyTop // close >= open here, so bodyTop is the close
    expect(layout.crosshair?.y).toBeCloseTo(expectedY, 5)
  })

  it('tracks the exact cursor height for its price readout, not the candle close', () => {
    const candles = [candle(0, 100, 102, 99, 101), candle(300, 101, 108, 100, 105)]
    // Hover well above the second candle's close, inside the plot area.
    const layout = buildChartLayout({ candles, markers: [], timeframe: '5m', dims: DIMS, hoverX: 400, hoverY: 40 })

    expect(layout.crosshair?.candle).toBe(candles[1])
    expect(layout.crosshair?.y).toBe(40)
    expect(layout.crosshair?.price).not.toBeCloseTo(105, 0)
    // The readout price must actually correspond to that y via the same
    // scale the candles themselves are drawn on -- well above the close,
    // near the top of the visible range.
    expect(layout.crosshair?.price).toBeGreaterThan(106)
  })
})

describe('zoomChartView', () => {
  const plot = { left: 8, right: DIMS.width - 56 }

  it('zooms in (fewer visible bars) on scroll-up, out (more) on scroll-down', () => {
    const totalBars = sessionBarCount('5m')
    const zoomedIn = zoomChartView({ current: null, deltaY: -100, hoverX: 300, timeframe: '5m', candleCount: 1, plot })
    expect(zoomedIn).not.toBeNull()
    expect(zoomedIn!.visibleBars).toBeLessThan(totalBars)

    const zoomedOutAgain = zoomChartView({ current: zoomedIn, deltaY: 100, hoverX: 300, timeframe: '5m', candleCount: 1, plot })
    expect(zoomedOutAgain === null || zoomedOutAgain.visibleBars > zoomedIn!.visibleBars).toBe(true)
  })

  it('never zooms out past the whole session -- the "9:15-15:30" ceiling', () => {
    let zoom = zoomChartView({ current: null, deltaY: 100, hoverX: 300, timeframe: '5m', candleCount: 1, plot })
    for (let i = 0; i < 5; i++) {
      zoom = zoomChartView({ current: zoom, deltaY: 100, hoverX: 300, timeframe: '5m', candleCount: 1, plot })
    }
    // Scrolling out repeatedly settles back at "fully zoomed out", not an
    // ever-widening window past the session's own bar count.
    expect(zoom).toBeNull()
  })

  it('never zooms in past a minimum bar count, so candles stay legible', () => {
    let zoom = zoomChartView({ current: null, deltaY: -100, hoverX: 300, timeframe: '5m', candleCount: 1, plot })
    for (let i = 0; i < 20; i++) {
      zoom = zoomChartView({ current: zoom, deltaY: -100, hoverX: 300, timeframe: '5m', candleCount: 1, plot })
    }
    expect(zoom!.visibleBars).toBeGreaterThanOrEqual(15)
  })
})

describe('buildChartLayout with zoom', () => {
  it('only positions candles inside the zoomed-in view window', () => {
    const candles = Array.from({ length: 20 }, (_, i) => candle(i * 300, 100 + i, 101 + i, 99 + i, 100 + i))
    const zoom = { visibleBars: 5, viewEndBar: 10 }
    const layout = buildChartLayout({ candles, markers: [], timeframe: '5m', dims: DIMS, zoom })

    // viewEndBar 10, visibleBars 5 -> candles 6..10 are in view, 0..5 and 11..19 are not.
    expect(layout.candles).toHaveLength(5)
    expect(layout.candles.map((c) => c.time)).toEqual(candles.slice(6, 11).map((c) => c.time))
  })
})

describe('hitTestMarker', () => {
  it('finds the badge glyph within its hit radius and nothing outside it', () => {
    const candles = [candle(0, 100, 102, 99, 101)]
    const markers: NiftyTradeMarker[] = [
      marker({ time: SESSION_START + 30, side: 'BUY', option_side: 'CE' }),
      marker({ time: SESSION_START + 90, side: 'SELL', exit_kind: 'SL' }),
    ]
    const layout = buildChartLayout({ candles, markers, timeframe: '5m', dims: DIMS })
    const [glyph] = layout.markers

    expect(hitTestMarker(layout, glyph.x, glyph.y)).toBe(glyph)
    expect(hitTestMarker(layout, glyph.x + 500, glyph.y + 500)).toBeNull()
  })
})
