import { describe, expect, it } from 'vitest'
import type { NiftyCandle, NiftyTradeMarker } from '../api'
import { buildChartLayout, hitTestMarker } from './NiftyLiveChart.layout'
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
