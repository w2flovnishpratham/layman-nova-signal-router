import { describe, expect, it } from 'vitest'
import type { NiftyTradeMarker } from '../api'
import { markerStyle, nearestCandleIndex, sessionOnly } from './NiftyLiveChart'

const marker = (overrides: Partial<NiftyTradeMarker>): NiftyTradeMarker => ({
  time: 1_000,
  side: 'SELL',
  label: 'EXIT',
  ...overrides,
})

describe('markerStyle', () => {
  it('maps every confirmed marker kind to a distinct visual', () => {
    expect(markerStyle(marker({ side: 'BUY', option_side: 'CE' })).text).toBe('BUY CE')
    expect(markerStyle(marker({ side: 'BUY', option_side: 'PE' })).text).toBe('BUY PE')
    expect(markerStyle(marker({ exit_kind: 'SL' })).text).toBe('EXIT SL')
    expect(markerStyle(marker({ exit_kind: 'TARGET' })).text).toBe('EXIT TGT')
    expect(markerStyle(marker({ exit_kind: 'REVERSAL' })).text).toBe('REV EXIT')
    expect(markerStyle(marker({ exit_kind: 'EXIT', label: 'EXIT' })).text).toBe('EXIT')
    const texts = [
      marker({ side: 'BUY', option_side: 'CE' }),
      marker({ side: 'BUY', option_side: 'PE' }),
      marker({ exit_kind: 'SL' }),
      marker({ exit_kind: 'TARGET' }),
      marker({ exit_kind: 'REVERSAL' }),
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
    const candles = sessionOnly({
      symbol: 'NIFTY',
      interval: '5m',
      source: 'test',
      status: 'ready',
      market_state: 'open',
      session_start: new Date(300_000).toISOString(),
      session_end: new Date(900_000).toISOString(),
      candles: [candle(900), candle(300), candle(300), candle(0), candle(1200)],
    })
    expect(candles.map((c) => c.time)).toEqual([300, 900])
  })

  it('nearestCandleIndex snaps within 30 minutes and rejects beyond', () => {
    const candles = [candle(300), candle(600), candle(900)]
    expect(nearestCandleIndex(candles, 650)).toBe(1)
    expect(nearestCandleIndex(candles, 900 + 31 * 60)).toBeNull()
    expect(nearestCandleIndex([], 300)).toBeNull()
  })
})
