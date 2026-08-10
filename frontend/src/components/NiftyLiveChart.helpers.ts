import type { ChartTimeframe, NiftyCandle, NiftyCandleSeries, NiftyCandleUpsert, NiftyTradeMarker } from '../api'

export const FIVE_MINUTE_SECONDS = 5 * 60
export const TIMEFRAME_SECONDS: Record<ChartTimeframe, number> = {
  '1m': 60,
  '5m': FIVE_MINUTE_SECONDS,
  '15m': 15 * 60,
}
const IST_OFFSET_SECONDS = 5.5 * 60 * 60
const SESSION_OPEN_SECONDS = 9 * 60 * 60 + 15 * 60
// F&O close moved 15:30 -> 15:40 IST with NSE's Closing Auction Session
// change (2026-08-03) -- candles between 15:30 and 15:40 were being filtered
// out of the chart entirely by istTimeframeBucket below.
const SESSION_CLOSE_SECONDS = 15 * 60 * 60 + 40 * 60
export const STALE_AFTER_MS = 45_000

// Total bar slots in a full trading session for a given timeframe -- used to
// reserve the chart's whole-day width up front instead of letting it stretch
// to fit however few candles exist so far (see NiftyLiveChart.tsx).
export function sessionBarCount(timeframe: ChartTimeframe): number {
  return Math.ceil((SESSION_CLOSE_SECONDS - SESSION_OPEN_SECONDS) / TIMEFRAME_SECONDS[timeframe])
}

export function istTimeframeBucket(epoch: number, timeframe: ChartTimeframe): number | null {
  if (!Number.isFinite(epoch)) return null
  const shifted = Math.floor(epoch) + IST_OFFSET_SECONDS
  const secondsOfDay = ((shifted % 86_400) + 86_400) % 86_400
  if (secondsOfDay < SESSION_OPEN_SECONDS || secondsOfDay >= SESSION_CLOSE_SECONDS) return null
  const seconds = TIMEFRAME_SECONDS[timeframe]
  return Math.floor(shifted / seconds) * seconds - IST_OFFSET_SECONDS
}

export function istFiveMinuteBucket(epoch: number): number | null {
  return istTimeframeBucket(epoch, '5m')
}

export function chartConnectionLabel(args: {
  loading: boolean
  unavailable: boolean
  marketClosed: boolean
  stale: boolean
  wsStatus: 'live' | 'degraded' | 'down'
  feedConnected?: boolean
  snapshotSource?: 'push' | 'rest' | null
  updatedAt?: string | null
  now?: number
}): 'Loading' | 'Live' | 'REST fallback' | 'Delayed' | 'Reconnecting' | 'Market closed' | 'Unavailable' {
  if (args.loading) return 'Loading'
  if (args.unavailable) return 'Unavailable'
  if (args.marketClosed) return 'Market closed'
  if (args.feedConnected === true && args.snapshotSource === 'rest') return 'REST fallback'
  if (args.wsStatus !== 'live' || args.feedConnected !== true) return 'Reconnecting'
  const updated = Date.parse(args.updatedAt ?? '')
  if (args.stale || !Number.isFinite(updated) || (args.now ?? Date.now()) - updated > STALE_AFTER_MS) return 'Delayed'
  return 'Live'
}

function validCandle(candle: NiftyCandle, timeframe: ChartTimeframe): boolean {
  const values = [candle.time, candle.open, candle.high, candle.low, candle.close]
  return values.every(Number.isFinite)
    && candle.open > 0 && candle.high > 0 && candle.low > 0 && candle.close > 0
    && candle.high >= Math.max(candle.open, candle.low, candle.close)
    && candle.low <= Math.min(candle.open, candle.high, candle.close)
    && istTimeframeBucket(candle.time, timeframe) === candle.time
}

export function sameCandles(left: NiftyCandle[], right: NiftyCandle[]): boolean {
  return left.length === right.length && left.every((candle, index) => {
    const other = right[index]
    return candle.time === other.time && candle.open === other.open && candle.high === other.high
      && candle.low === other.low && candle.close === other.close && candle.volume === other.volume
  })
}

export function upsertCandle(
  series: NiftyCandleSeries | null,
  delta: NiftyCandleUpsert,
): NiftyCandleSeries | null {
  if (!series || series.interval !== delta.interval || series.trading_date !== delta.trading_date) return series
  const candles = [...series.candles]
  const index = candles.findIndex((candle) => candle.time === delta.candle.time)
  if (index >= 0) candles[index] = delta.candle
  else candles.push(delta.candle)
  candles.sort((left, right) => left.time - right.time)
  return {
    ...series,
    source: delta.source,
    market_state: delta.market_state,
    updated_at: delta.updated_at,
    candle_count: delta.candle_count,
    candles,
  }
}

export function sessionOnly(series: NiftyCandleSeries): NiftyCandle[] {
  const timeframe: ChartTimeframe = (
    series.interval === '1m' || series.interval === '15m' ? series.interval : '5m'
  )
  const start = series.session_start ? Math.floor(new Date(series.session_start).getTime() / 1000) : null
  const end = series.session_end ? Math.floor(new Date(series.session_end).getTime() / 1000) : null
  const seen = new Set<number>()
  return (series.candles ?? []).filter((candle) => {
    if (!validCandle(candle, timeframe)) return false
    if (start !== null && candle.time < start) return false
    if (end !== null && candle.time >= end) return false
    if (seen.has(candle.time)) return false
    seen.add(candle.time)
    return true
  }).sort((left, right) => left.time - right.time)
}

export function markerCandleIndex(
  candles: NiftyCandle[],
  time: number,
  timeframe: ChartTimeframe = '5m',
): number | null {
  const bucket = istTimeframeBucket(time, timeframe)
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
    case 'REVERSAL': return { position: 'aboveBar', shape: 'circle', color: '#2f6bed', text: 'REV EXIT' }
    case 'EOD': return { position: 'aboveBar', shape: 'circle', color: '#f59e0b', text: 'EOD EXIT' }
    default: return { position: 'aboveBar', shape: 'circle', color: '#94a3b8', text: marker.label || 'EXIT' }
  }
}
