import { useEffect, useState } from 'react'
import { getNiftyCandles, type NiftyCandle } from '../api'

export interface MarketBiasSnapshot {
  bullish_percentage: number
  bearish_percentage: number
  updated_at: string | null
}

export function deriveMarketBias(candles: NiftyCandle[], updatedAt: string | null = null): MarketBiasSnapshot {
  const directional = candles.filter((candle) => Number.isFinite(candle.open) && Number.isFinite(candle.close) && candle.open !== candle.close)
  if (directional.length === 0) {
    return { bullish_percentage: 50, bearish_percentage: 50, updated_at: updatedAt }
  }
  const bullish = directional.filter((candle) => candle.close > candle.open).length
  const bullishPercentage = Math.round((bullish / directional.length) * 100)
  return {
    bullish_percentage: bullishPercentage,
    bearish_percentage: 100 - bullishPercentage,
    updated_at: updatedAt,
  }
}

export function MarketBiasCard() {
  const [bias, setBias] = useState<MarketBiasSnapshot>({ bullish_percentage: 50, bearish_percentage: 50, updated_at: null })

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const series = await getNiftyCandles()
        if (active) setBias(deriveMarketBias(series.candles, series.updated_at ?? null))
      } catch {
        // Preserve the last confirmed informational snapshot.
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 5 * 60 * 1000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [])

  const direction = bias.bullish_percentage > bias.bearish_percentage
    ? 'Bullish'
    : bias.bearish_percentage > bias.bullish_percentage
      ? 'Bearish'
      : 'Neutral'
  const strength = Math.abs(bias.bullish_percentage - bias.bearish_percentage)

  return (
    <section className="sidebar-card market-bias-card" aria-label="Market Bias NIFTY">
      <div className="sidebar-title"><span>Market Bias (NIFTY)</span><strong>{direction}</strong></div>
      <div className="market-bias-bar" aria-label={`Bullish ${bias.bullish_percentage} percent, bearish ${bias.bearish_percentage} percent`}>
        <span style={{ width: `${bias.bullish_percentage}%` }} />
      </div>
      <dl>
        <div><dt>Strength</dt><dd>{strength}%</dd></div>
        <div><dt>Bullish</dt><dd>{bias.bullish_percentage}%</dd></div>
        <div><dt>Bearish</dt><dd>{bias.bearish_percentage}%</dd></div>
        <div><dt>Updated</dt><dd>{formatTime(bias.updated_at)}</dd></div>
      </dl>
    </section>
  )
}

function formatTime(value: string | null): string {
  if (!value) return 'Awaiting data'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? 'Awaiting data'
    : date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' })
}
