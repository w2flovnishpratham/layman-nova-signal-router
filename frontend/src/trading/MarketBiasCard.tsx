import { useEffect, useState } from 'react'
import { getMarketSentiment, getNiftyCandles, type NiftyCandle } from '../api'

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

export function marketBiasMeter(direction: 'Bullish' | 'Bearish' | 'Neutral', strength: number) {
  const boundedStrength = Math.max(0, Math.min(100, strength))
  const tone = direction === 'Bearish'
    ? 'red'
    : direction === 'Neutral'
      ? 'yellow'
    : boundedStrength <= 20
      ? 'red'
      : boundedStrength <= 40
        ? 'orange'
        : boundedStrength <= 60
          ? 'yellow'
          : boundedStrength <= 80
            ? 'mint'
            : 'green'

  return { activeSegments: Math.ceil(boundedStrength / 20), tone }
}

export function MarketBiasCard() {
  const [bias, setBias] = useState<MarketBiasSnapshot>({ bullish_percentage: 50, bearish_percentage: 50, updated_at: null })

  useEffect(() => {
    let active = true
    const load = async () => {
      // Prefer the real NOVA intelligence sentiment; fall back to the local
      // candle-direction heuristic if it is unavailable, so the card is never blank.
      try {
        const s = await getMarketSentiment()
        if (s.available && s.bullish_percent != null) {
          if (active) {
            setBias({
              bullish_percentage: s.bullish_percent,
              bearish_percentage: s.bearish_percent ?? 100 - s.bullish_percent,
              updated_at: s.updated_at,
            })
          }
          return
        }
      } catch {
        // fall through to the candle heuristic
      }
      try {
        const series = await getNiftyCandles()
        if (active) setBias(deriveMarketBias(series.candles, series.updated_at ?? null))
      } catch {
        // Preserve the last confirmed informational snapshot.
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 60 * 1000)
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
  const meter = marketBiasMeter(direction, strength)

  return (
    <section className="sidebar-card market-bias-card" aria-label="Market Bias NIFTY">
      <div className="sidebar-title"><span>Market Bias (NIFTY)</span><strong data-tone={meter.tone}>{direction}</strong></div>
      <dl>
        <div className="market-bias-strength">
          <dt>Strength</dt>
          <dd>
            <span
              className="market-bias-bar"
              data-tone={meter.tone}
              role="progressbar"
              aria-label={`${direction} market strength`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={strength}
            >
              {Array.from({ length: 5 }, (_, index) => (
                <i key={index} data-active={index < meter.activeSegments} aria-hidden="true" />
              ))}
            </span>
            <strong>{strength}%</strong>
          </dd>
        </div>
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
