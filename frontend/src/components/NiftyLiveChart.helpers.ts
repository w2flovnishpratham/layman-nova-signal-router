import type { NiftyCandle, NiftyCandleSeries, NiftyTradeMarker } from '../api'

export const FIVE_MINUTE_SECONDS = 5 * 60
const IST_OFFSET_SECONDS = 5.5 * 60 * 60
const SESSION_OPEN_SECONDS = 9 * 60 * 60 + 15 * 60
const SESSION_CLOSE_SECONDS = 15 * 60 * 60 + 30 * 60
export const STALE_AFTER_MS = 45_000

export function istFiveMinuteBucket(epoch: number): number | null {
  if (!Number.isFinite(epoch)) return null
  const shifted = Math.floor(epoch) + IST_OFFSET_SECONDS
  const secondsOfDay = ((shifted % 86_400) + 86_400) % 86_400
  if (secondsOfDay < SESSION_OPEN_SECONDS || secondsOfDay >= SESSION_CLOSE_SECONDS) return null
  return Math.floor(shifted / FIVE_MINUTE_SECONDS) * FIVE_MINUTE_SECONDS - IST_OFFSET_SECONDS
}

export function applyLiveTick(
  candles: NiftyCandle[],
  price: number,
  epoch: number,
  lastTickEpoch?: number | null,
): { candle: NiftyCandle; candles: NiftyCandle[]; appended: boolean } | null {
  const bucket = istFiveMinuteBucket(epoch)
  if (bucket === null || !Number.isFinite(price) || price <= 0 || candles.length === 0 || (lastTickEpoch != null && epoch <= lastTickEpoch)) return null
  const last = candles[candles.length - 1]
  if (bucket < last.time) return null
  if (bucket === last.time) {
    const candle = { ...last, high: Math.max(last.high, price), low: Math.min(last.low, price), close: price }
    return { candle, candles: [...candles.slice(0, -1), candle], appended: false }
  }
  const candle = { time: bucket, open: price, high: price, low: price, close: price, volume: 0 }
  return { candle, candles: [...candles, candle], appended: true }
}

export function chartConnectionLabel(args: {
  loading: boolean
  unavailable: boolean
  marketClosed: boolean
  stale: boolean
  wsStatus: 'live' | 'degraded' | 'down'
  feedConnected?: boolean
  updatedAt?: string | null
  now?: number
}): 'Loading' | 'Live' | 'Delayed' | 'Reconnecting' | 'Market closed' | 'Unavailable' {
  if (args.loading) return 'Loading'
  if (args.unavailable) return 'Unavailable'
  if (args.marketClosed) return 'Market closed'
  if (args.wsStatus !== 'live' || args.feedConnected !== true) return 'Reconnecting'
  const updated = Date.parse(args.updatedAt ?? '')
  if (args.stale || !Number.isFinite(updated) || (args.now ?? Date.now()) - updated > STALE_AFTER_MS) return 'Delayed'
  return 'Live'
}

function validCandle(candle: NiftyCandle): boolean {
  const values = [candle.time, candle.open, candle.high, candle.low, candle.close]
  return values.every(Number.isFinite)
    && candle.open > 0 && candle.high > 0 && candle.low > 0 && candle.close > 0
    && candle.high >= Math.max(candle.open, candle.low, candle.close)
    && candle.low <= Math.min(candle.open, candle.high, candle.close)
    && istFiveMinuteBucket(candle.time) === candle.time
}

export function sameCandles(left: NiftyCandle[], right: NiftyCandle[]): boolean {
  return left.length === right.length && left.every((candle, index) => {
    const other = right[index]
    return candle.time === other.time && candle.open === other.open && candle.high === other.high
      && candle.low === other.low && candle.close === other.close && candle.volume === other.volume
  })
}

export function sessionOnly(series: NiftyCandleSeries): NiftyCandle[] {
  const start = series.session_start ? Math.floor(new Date(series.session_start).getTime() / 1000) : null
  const end = series.session_end ? Math.floor(new Date(series.session_end).getTime() / 1000) : null
  const seen = new Set<number>()
  return (series.candles ?? []).filter((candle) => {
    if (!validCandle(candle)) return false
    if (start !== null && candle.time < start) return false
    if (end !== null && candle.time >= end) return false
    if (seen.has(candle.time)) return false
    seen.add(candle.time)
    return true
  }).sort((left, right) => left.time - right.time)
}

export function markerCandleIndex(candles: NiftyCandle[], time: number): number | null {
  const bucket = istFiveMinuteBucket(time)
  if (bucket === null) return null
  const index = candles.findIndex((candle) => candle.time === bucket)
  return index < 0 ? null : index
}

interface MarkerStyle {
  position: 'aboveBar' | 'belowBar'
  shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square'
  color: string
  text: string
}

export function markerStyle(marker: NiftyTradeMarker): MarkerStyle {
  if (marker.side === 'BUY') {
    return marker.option_side === 'PE'
      ? { position: 'aboveBar', shape: 'arrowDown', color: '#fb7185', text: 'BUY PE' }
      : { position: 'belowBar', shape: 'arrowUp', color: '#34d399', text: marker.option_side ? 'BUY CE' : 'BUY' }
  }
  switch (marker.exit_kind) {
    case 'SL': return { position: 'aboveBar', shape: 'square', color: '#ef4444', text: 'SL EXIT' }
    case 'TARGET': return { position: 'aboveBar', shape: 'square', color: '#22c55e', text: 'TARGET' }
    case 'REVERSAL': return { position: 'aboveBar', shape: 'circle', color: '#a78bfa', text: 'REV EXIT' }
    case 'EOD': return { position: 'aboveBar', shape: 'circle', color: '#f59e0b', text: 'EOD EXIT' }
    default: return { position: 'aboveBar', shape: 'circle', color: '#94a3b8', text: marker.label || 'EXIT' }
  }
}
