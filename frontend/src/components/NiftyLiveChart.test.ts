import { describe, expect, it } from 'vitest'
import type { NiftyTradeMarker } from '../api'
import {
  chartConnectionLabel,
  istFiveMinuteBucket,
  istTimeframeBucket,
  markerCandleIndex,
  markerStyle,
  sessionOnly,
} from './NiftyLiveChart.helpers'

const marker = (overrides: Partial<NiftyTradeMarker>): NiftyTradeMarker => ({
  id: 'marker-1',
  time: 1_000,
  side: 'SELL',
  label: 'EXIT',
  ...overrides,
})

describe('TradingView chart helpers', () => {
  const candle = (time: number) => ({ time, open: 1, high: 2, low: 0.5, close: 1.5, volume: 0 })

  it('maps confirmed backend marker kinds', () => {
    expect(markerStyle(marker({ side: 'BUY', option_side: 'CE' })).text).toBe('BUY CE')
    expect(markerStyle(marker({ side: 'BUY', option_side: 'PE' })).text).toBe('BUY PE')
    expect(markerStyle(marker({ exit_kind: 'SL' })).text).toBe('SL EXIT')
    expect(markerStyle(marker({ exit_kind: 'TARGET' })).text).toBe('TARGET')
  })

  it('clips, deduplicates, and sorts historical session candles', () => {
    const start = Date.parse('2026-07-15T09:15:00+05:30') / 1000
    const candles = sessionOnly({
      symbol: 'NIFTY',
      interval: '5m',
      source: 'test',
      status: 'ready',
      market_state: 'open',
      session_start: new Date(start * 1000).toISOString(),
      session_end: new Date((start + 900) * 1000).toISOString(),
      candles: [candle(start + 600), candle(start), candle(start), candle(start - 300), candle(start + 1200)],
    })
    expect(candles.map((item) => item.time)).toEqual([start, start + 600])
  })

  it('aligns only the supported presentation timeframes to the IST session', () => {
    const start = Date.parse('2026-07-15T09:15:00+05:30') / 1000
    expect(istTimeframeBucket(start + 155, '1m')).toBe(start + 120)
    expect(istTimeframeBucket(start + 155, '5m')).toBe(start)
    expect(istTimeframeBucket(start + 755, '15m')).toBe(start)
  })

  it('places markers in the selected presentation bucket', () => {
    const start = Date.parse('2026-07-15T09:20:00+05:30') / 1000
    expect(istFiveMinuteBucket(start + 155)).toBe(start)
    expect(markerCandleIndex([candle(start)], start + 155)).toBe(0)
    expect(markerCandleIndex([candle(start)], start + 155, '15m')).toBeNull()
  })

  it('labels stale, disconnected, closed, and unavailable data honestly', () => {
    const updatedAt = '2026-07-15T05:30:00.000Z'
    const base = { loading: false, unavailable: false, marketClosed: false, stale: false, wsStatus: 'live' as const, feedConnected: true, updatedAt }
    expect(chartConnectionLabel({ ...base, now: Date.parse(updatedAt) + 1_000 })).toBe('Live')
    expect(chartConnectionLabel({ ...base, now: Date.parse(updatedAt) + 46_000 })).toBe('Delayed')
    expect(chartConnectionLabel({ ...base, wsStatus: 'degraded' })).toBe('Reconnecting')
    expect(chartConnectionLabel({ ...base, wsStatus: 'degraded', snapshotSource: 'rest' })).toBe('REST fallback')
    expect(chartConnectionLabel({ ...base, marketClosed: true })).toBe('Market closed')
    expect(chartConnectionLabel({ ...base, unavailable: true })).toBe('Unavailable')
  })
})
