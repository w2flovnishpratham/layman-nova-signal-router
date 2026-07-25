import { describe, expect, it } from 'vitest'
import { deriveMarketBias } from './MarketBiasCard'

describe('deriveMarketBias', () => {
  it('derives a deterministic percentage from closed candles only', () => {
    const bias = deriveMarketBias([
      { time: 1, open: 100, high: 110, low: 95, close: 108, volume: 1000 },
      { time: 2, open: 108, high: 112, low: 101, close: 104, volume: 1000 },
      { time: 3, open: 104, high: 109, low: 102, close: 107, volume: 1000 },
    ], '2026-07-25T10:00:00Z')

    expect(bias).toEqual({
      bullish_percentage: 67,
      bearish_percentage: 33,
      updated_at: '2026-07-25T10:00:00Z',
    })
  })

  it('returns a neutral snapshot when no directional candle exists', () => {
    expect(deriveMarketBias([
      { time: 1, open: 100, high: 100, low: 100, close: 100, volume: 1000 },
    ])).toEqual({
      bullish_percentage: 50,
      bearish_percentage: 50,
      updated_at: null,
    })
  })
})
