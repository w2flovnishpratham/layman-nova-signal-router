import { describe, expect, it } from 'vitest'
import type { NiftyTradeMarker } from '../api'
import {
  applyLiveTick,
  chartConnectionLabel,
  istFiveMinuteBucket,
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

describe('markerStyle', () => {
  it('maps every confirmed marker kind to a distinct visual', () => {
    expect(markerStyle(marker({ side: 'BUY', option_side: 'CE' })).text).toBe('BUY CE')
    expect(markerStyle(marker({ side: 'BUY', option_side: 'PE' })).text).toBe('BUY PE')
    expect(markerStyle(marker({ exit_kind: 'SL' })).text).toBe('SL EXIT')
    expect(markerStyle(marker({ exit_kind: 'TARGET' })).text).toBe('TARGET')
    expect(markerStyle(marker({ exit_kind: 'REVERSAL' })).text).toBe('REV EXIT')
    expect(markerStyle(marker({ exit_kind: 'EOD' })).text).toBe('EOD EXIT')
    expect(markerStyle(marker({ exit_kind: 'EXIT', label: 'EXIT' })).text).toBe('EXIT')
    const texts = [
      marker({ side: 'BUY', option_side: 'CE' }),
      marker({ side: 'BUY', option_side: 'PE' }),
      marker({ exit_kind: 'SL' }),
      marker({ exit_kind: 'TARGET' }),
      marker({ exit_kind: 'REVERSAL' }),
      marker({ exit_kind: 'EOD' }),
      marker({ exit_kind: 'EXIT' }),
    ].map((m) => JSON.stringify(markerStyle(m)))
    expect(new Set(texts).size).toBe(texts.length)
  })

  it('buys point up below the bar; exits sit above the bar', () => {
    expect(markerStyle(marker({ side: 'BUY', option_side: 'CE' })).position).toBe('belowBar')
    expect(markerStyle(marker({ exit_kind: 'SL' })).position).toBe('aboveBar')
  })
})

describe('candle helpers', () => {
  const candle = (time: number) => ({ time, open: 1, high: 2, low: 0.5, close: 1.5, volume: 0 })

  it('sessionOnly clips to the session window, dedupes, and sorts', () => {
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
    expect(candles.map((c) => c.time)).toEqual([start, start + 600])
  })

  it('buckets execution times on the IST cash-session grid', () => {
    const epoch = (iso: string) => Date.parse(iso) / 1000
    expect(istFiveMinuteBucket(epoch('2026-07-15T09:15:00+05:30'))).toBe(epoch('2026-07-15T09:15:00+05:30'))
    expect(istFiveMinuteBucket(epoch('2026-07-15T09:22:35+05:30'))).toBe(epoch('2026-07-15T09:20:00+05:30'))
    expect(istFiveMinuteBucket(epoch('2026-07-15T10:08:20+05:30'))).toBe(epoch('2026-07-15T10:05:00+05:30'))
    expect(istFiveMinuteBucket(epoch('2026-07-15T09:14:59+05:30'))).toBeNull()
    expect(istFiveMinuteBucket(epoch('2026-07-15T15:30:00+05:30'))).toBeNull()
  })

  it('updates one forming candle and rejects older ticks', () => {
    const start = Date.parse('2026-07-15T09:20:00+05:30') / 1000
    const initial = [{ time: start, open: 100, high: 101, low: 99, close: 100 }]
    const updated = applyLiveTick(initial, 102, start + 30)
    expect(updated).toMatchObject({ appended: false, candle: { open: 100, high: 102, low: 99, close: 102 } })
    const next = applyLiveTick(updated!.candles, 103, start + 300)
    expect(next).toMatchObject({ appended: true, candle: { open: 103, high: 103, low: 103, close: 103 } })
    expect(next!.candles).toHaveLength(2)
    expect(applyLiveTick(next!.candles, 98, start + 60)).toBeNull()
    expect(applyLiveTick(next!.candles, 104, start + 330, start + 340)).toBeNull()
    expect(applyLiveTick(next!.candles, 104, start + 340, start + 340)).toBeNull()
  })

  it('maps markers to their exact shared five-minute bucket', () => {
    const start = Date.parse('2026-07-15T09:20:00+05:30') / 1000
    expect(markerCandleIndex([candle(start)], start + 155)).toBe(0)
    expect(markerCandleIndex([], start)).toBeNull()
  })
})

describe('connection status', () => {
  const updatedAt = '2026-07-15T05:30:00.000Z'
  const base = { loading: false, unavailable: false, marketClosed: false, stale: false, wsStatus: 'live' as const, feedConnected: true, updatedAt }

  it('does not label stale, disconnected, closed, or unavailable data live', () => {
    expect(chartConnectionLabel({ ...base, now: Date.parse(updatedAt) + 1_000 })).toBe('Live')
    expect(chartConnectionLabel({ ...base, now: Date.parse(updatedAt) + 46_000 })).toBe('Delayed')
    expect(chartConnectionLabel({ ...base, wsStatus: 'degraded' })).toBe('Reconnecting')
    expect(chartConnectionLabel({ ...base, feedConnected: false })).toBe('Reconnecting')
    expect(chartConnectionLabel({ ...base, marketClosed: true })).toBe('Market closed')
    expect(chartConnectionLabel({ ...base, unavailable: true })).toBe('Unavailable')
  })
})
