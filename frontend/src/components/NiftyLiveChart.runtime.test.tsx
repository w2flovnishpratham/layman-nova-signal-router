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
    fireEvent.click(canvas, { clientX: badge.x, clientY: badge.y })

    expect(await screen.findByText('2 trades')).toBeInTheDocument()
    expect(screen.getByText('₹128.75')).toBeInTheDocument()
    expect(screen.getByText('₹120.40')).toBeInTheDocument()
  })
})
