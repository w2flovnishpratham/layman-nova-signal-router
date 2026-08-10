import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const CANDLE_TIME = Date.parse('2026-07-21T09:15:00+05:30') / 1000

const apiMocks = vi.hoisted(() => ({
  getNiftyCandles: vi.fn().mockImplementation((timeframe: string) => Promise.resolve({
    symbol: 'NIFTY', interval: timeframe, source: 'dhan_authoritative_1m', status: 'ready', market_state: 'open',
    trading_date: '2026-07-21', session_start: '2026-07-21T09:15:00+05:30',
    session_end: '2026-07-21T15:30:00+05:30', updated_at: '2026-07-21T04:00:00Z',
    candles: [{ time: CANDLE_TIME, open: 24100, high: 24120, low: 24090, close: 24110, volume: 1 }],
  })),
  getNiftyMarkers: vi.fn().mockResolvedValue({ trading_date: '2026-07-21', markers: [] }),
}))

vi.mock('../api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api')>(),
  ...apiMocks,
}))

import { useSessionStore } from '../state/sessionStore'
import { NiftyLiveChart } from './NiftyLiveChart'
import { buildChartLayout } from './NiftyLiveChart.layout'

class TestResizeObserver {
  private callback: ResizeObserverCallback
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
  }
  observe(target: Element) {
    this.callback([{ target, contentRect: target.getBoundingClientRect() } as ResizeObserverEntry], this as unknown as ResizeObserver)
  }
  disconnect() {}
  unobserve() {}
}

// Small, enumerable vocabulary matching exactly what NiftyLiveChart.paint.ts
// calls -- jsdom has no real 2D canvas, so this stub exists purely to let
// the component render/repaint without throwing. It intentionally records
// calls (not pixels) for the smoke test below; geometry correctness is
// covered by NiftyLiveChart.layout.test.ts's plain-data assertions instead.
function createStubCtx() {
  return {
    clearRect: vi.fn(), fillRect: vi.fn(), beginPath: vi.fn(), closePath: vi.fn(),
    moveTo: vi.fn(), lineTo: vi.fn(), arcTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(),
    fill: vi.fn(), fillText: vi.fn(), setLineDash: vi.fn(), save: vi.fn(), restore: vi.fn(),
    setTransform: vi.fn(), measureText: vi.fn(() => ({ width: 40 })),
    fillStyle: '', strokeStyle: '', lineWidth: 0, font: '', textAlign: 'left', textBaseline: 'alphabetic',
  }
}

const DIMS = { width: 720, height: 320 }

beforeEach(() => {
  useSessionStore.getState().resetSession()
  vi.stubGlobal('ResizeObserver', TestResizeObserver)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    width: DIMS.width, height: DIMS.height, top: 0, left: 0, right: DIMS.width, bottom: DIMS.height, x: 0, y: 0,
    toJSON: () => ({}),
  })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(createStubCtx() as unknown as CanvasRenderingContext2D)
  apiMocks.getNiftyCandles.mockClear()
  apiMocks.getNiftyCandles.mockImplementation((timeframe: string) => Promise.resolve({
    symbol: 'NIFTY', interval: timeframe, source: 'dhan_authoritative_1m', status: 'ready', market_state: 'open',
    trading_date: '2026-07-21', session_start: '2026-07-21T09:15:00+05:30',
    session_end: '2026-07-21T15:30:00+05:30', updated_at: '2026-07-21T04:00:00Z',
    candles: [{ time: CANDLE_TIME, open: 24100, high: 24120, low: 24090, close: 24110, volume: 1 }],
  }))
  apiMocks.getNiftyMarkers.mockClear()
  apiMocks.getNiftyMarkers.mockResolvedValue({ trading_date: '2026-07-21', markers: [] })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('NiftyLiveChart responsive runtime', () => {
  it('renders authoritative history and switches among 1m, 5m, and 15m', async () => {
    render(<NiftyLiveChart engineMode="paper" />)
    await waitFor(() => expect(apiMocks.getNiftyCandles).toHaveBeenCalledWith('5m', expect.any(AbortSignal)))
    expect(await screen.findByRole('img', { name: /NIFTY 5m candlestick chart/i })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '1m' }))
    await waitFor(() => expect(apiMocks.getNiftyCandles).toHaveBeenCalledWith('1m', expect.any(AbortSignal)))
    fireEvent.click(screen.getByRole('button', { name: '15m' }))
    await waitFor(() => expect(apiMocks.getNiftyCandles).toHaveBeenCalledWith('15m', expect.any(AbortSignal)))
  })

  it('falls back unsupported saved preferences to 5m', async () => {
    render(<NiftyLiveChart engineMode="paper" defaultTimeframe="1h" />)
    await waitFor(() => expect(apiMocks.getNiftyCandles).toHaveBeenCalledWith('5m', expect.any(AbortSignal)))
    expect(screen.getByRole('button', { name: '5m' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('can render an isolated preview without calling the backend candle endpoint', async () => {
    const previewLoader = vi.fn().mockImplementation((timeframe: string) => Promise.resolve({
      symbol: 'NIFTY', interval: timeframe, source: 'terminal_preview', status: 'ready', market_state: 'open',
      trading_date: '2026-07-21', candles: [{ time: CANDLE_TIME, open: 24100, high: 24120, low: 24090, close: 24110, volume: 1 }],
    }))

    render(<NiftyLiveChart engineMode="paper" candleLoader={previewLoader} />)

    expect(await screen.findByRole('img', { name: /NIFTY 5m candlestick chart/i })).toBeInTheDocument()
    expect(previewLoader).toHaveBeenCalledWith('5m', expect.any(AbortSignal))
    expect(apiMocks.getNiftyCandles).not.toHaveBeenCalled()
  })

  it('shows setup guidance instead of an endless reconnect spinner when market data is not configured', async () => {
    apiMocks.getNiftyCandles.mockResolvedValue({
      symbol: 'NIFTY', interval: '5m', source: 'dhan_authoritative_1m', status: 'unavailable', market_state: 'closed',
      trading_date: '2026-07-21', candles: [], reason: 'market_data_unavailable',
      message: 'Authoritative 1m NIFTY candles are unavailable. Nova is reconnecting market data.',
    })

    render(<NiftyLiveChart engineMode="paper" />)

    expect(await screen.findByText('NIFTY market data is not configured for this environment.')).toBeInTheDocument()
    expect(screen.getByText('Connect the shared Dhan data account to load authoritative candles.')).toBeInTheDocument()
    expect(screen.queryByText(/Retrying automatically/i)).not.toBeInTheDocument()
  })

  it('paints without throwing once candles and markers arrive', async () => {
    apiMocks.getNiftyMarkers.mockResolvedValue({
      trading_date: '2026-07-21',
      markers: [{ id: 'm1', time: CANDLE_TIME + 30, side: 'BUY', option_side: 'CE', label: 'BUY CE', mode: 'paper', price: 24110 }],
    })
    const ctx = createStubCtx()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D)

    render(<NiftyLiveChart engineMode="paper" />)
    await waitFor(() => expect(ctx.fillText).toHaveBeenCalled())
    // A single marker draws as a triangle (via ctx.fill), not text -- this
    // just proves paintChart actually ran to completion, not exact geometry.
    expect(ctx.clearRect).toHaveBeenCalled()
    expect(ctx.fill).toHaveBeenCalled()
  })

  it('opens a popover listing every trade when a busy-candle badge is clicked', async () => {
    const markers = [
      { id: 'm1', time: CANDLE_TIME + 30, side: 'BUY' as const, option_side: 'CE' as const, label: 'BUY CE', mode: 'paper', execution_price: 128.75 },
      { id: 'm2', time: CANDLE_TIME + 90, side: 'SELL' as const, exit_kind: 'SL' as const, label: 'SL EXIT', mode: 'paper', execution_price: 120.4 },
    ]
    apiMocks.getNiftyMarkers.mockResolvedValue({ trading_date: '2026-07-21', markers })

    render(<NiftyLiveChart engineMode="paper" />)
    await waitFor(() => expect(apiMocks.getNiftyMarkers).toHaveBeenCalled())

    // Both markers land in the same 5m bucket as the mocked candle -> one
    // badge glyph. Compute exactly where it lands the same way the
    // component does, so the click lands on it regardless of constant
    // tuning inside layout.ts.
    const layout = buildChartLayout({
      candles: [{ time: CANDLE_TIME, open: 24100, high: 24120, low: 24090, close: 24110, volume: 1 }],
      markers,
      timeframe: '5m',
      dims: DIMS,
    })
    const [badge] = layout.markers
    expect(badge.kind).toBe('badge')

    const canvas = await screen.findByRole('img', { name: /NIFTY 5m candlestick chart/i })
    // The repaint effect only picks up the fetched markers a tick or two
    // after the mock resolves, so layoutRef isn't hit-testable yet at the
    // instant the mock was called -- retry the click until it lands.
    await waitFor(() => {
      fireEvent.click(canvas, { clientX: badge.x, clientY: badge.y })
      expect(screen.getByText('2 trades')).toBeInTheDocument()
    })
    expect(screen.getByText('₹128.75')).toBeInTheDocument()
    expect(screen.getByText('₹120.40')).toBeInTheDocument()
  })

  it('refreshes markers immediately on an order.filled/trade.exit push, not just on the poll', async () => {
    render(<NiftyLiveChart engineMode="paper" />)
    await waitFor(() => expect(apiMocks.getNiftyMarkers).toHaveBeenCalledTimes(1))

    window.dispatchEvent(new CustomEvent('nova:terminal-delta', {
      detail: { id: 'evt-1', type: 'order.filled', ts: '2026-07-21T04:05:00Z', data: {} },
    }))
    await waitFor(() => expect(apiMocks.getNiftyMarkers).toHaveBeenCalledTimes(2))

    window.dispatchEvent(new CustomEvent('nova:terminal-delta', {
      detail: { id: 'evt-2', type: 'trade.exit', ts: '2026-07-21T04:06:00Z', data: {} },
    }))
    await waitFor(() => expect(apiMocks.getNiftyMarkers).toHaveBeenCalledTimes(3))

    // An unrelated event type must not trigger a refetch.
    window.dispatchEvent(new CustomEvent('nova:terminal-delta', {
      detail: { id: 'evt-3', type: 'system.event', ts: '2026-07-21T04:07:00Z', data: {} },
    }))
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(apiMocks.getNiftyMarkers).toHaveBeenCalledTimes(3)
  })

  it('applies a pushed market.candles series for the current timeframe without an extra fetch', async () => {
    const ctx = createStubCtx()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D)

    render(<NiftyLiveChart engineMode="paper" />)
    await waitFor(() => expect(apiMocks.getNiftyCandles).toHaveBeenCalledTimes(1))
    const paintsBeforePush = ctx.clearRect.mock.calls.length

    window.dispatchEvent(new CustomEvent('nova:terminal-delta', {
      detail: {
        id: 'evt-candles', type: 'market.candles', ts: '2026-07-21T04:10:00Z',
        data: {
          symbol: 'NIFTY', interval: '5m', source: 'dhan_authoritative_1m', status: 'ready', market_state: 'open',
          trading_date: '2026-07-21', updated_at: '2026-07-21T04:10:00Z',
          candles: [
            { time: CANDLE_TIME, open: 24100, high: 24120, low: 24090, close: 24110, volume: 1 },
            { time: CANDLE_TIME + 300, open: 24110, high: 24130, low: 24105, close: 24125, volume: 1 },
          ],
        },
      },
    }))

    await waitFor(() => expect(ctx.clearRect.mock.calls.length).toBeGreaterThan(paintsBeforePush))
    // Applied directly from the push, not by triggering a second REST fetch.
    expect(apiMocks.getNiftyCandles).toHaveBeenCalledTimes(1)
  })

  it('upserts one pushed candle without fetching the full series again', async () => {
    const ctx = createStubCtx()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D)
    render(<NiftyLiveChart engineMode="paper" />)
    await waitFor(() => expect(apiMocks.getNiftyCandles).toHaveBeenCalledTimes(1))
    const paintsBeforePush = ctx.clearRect.mock.calls.length

    window.dispatchEvent(new CustomEvent('nova:terminal-delta', {
      detail: {
        id: 'evt-candle-upsert', type: 'market.candle.upsert', ts: '2026-07-21T04:10:00Z',
        data: {
          symbol: 'NIFTY', interval: '5m', source: 'dhan_marketfeed_ws', market_state: 'open',
          trading_date: '2026-07-21', updated_at: '2026-07-21T04:10:00Z', candle_count: 1,
          candle: { time: CANDLE_TIME, open: 24100, high: 24125, low: 24090, close: 24120, volume: 1 },
        },
      },
    }))

    await waitFor(() => expect(ctx.clearRect.mock.calls.length).toBeGreaterThan(paintsBeforePush))
    expect(apiMocks.getNiftyCandles).toHaveBeenCalledTimes(1)
  })

  it('ignores a pushed candle series for a timeframe the user is not currently viewing', async () => {
    render(<NiftyLiveChart engineMode="paper" />)
    await waitFor(() => expect(apiMocks.getNiftyCandles).toHaveBeenCalledWith('5m', expect.any(AbortSignal)))

    window.dispatchEvent(new CustomEvent('nova:terminal-delta', {
      detail: {
        id: 'evt-candles-1m', type: 'market.candles', ts: '2026-07-21T04:10:00Z',
        data: {
          symbol: 'NIFTY', interval: '1m', source: 'dhan_authoritative_1m', status: 'ready', market_state: 'open',
          trading_date: '2026-07-21', updated_at: '2026-07-21T04:10:00Z',
          candles: [{ time: CANDLE_TIME, open: 1, high: 1, low: 1, close: 1, volume: 1 }],
        },
      },
    }))

    await new Promise((resolve) => setTimeout(resolve, 10))
    // Still showing the 5m header -- a 1m push while viewing 5m must be a no-op.
    expect(await screen.findByRole('img', { name: /NIFTY 5m candlestick chart/i })).toBeInTheDocument()
  })
})
