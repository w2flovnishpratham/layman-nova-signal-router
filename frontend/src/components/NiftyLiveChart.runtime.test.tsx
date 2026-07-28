import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const chartMocks = vi.hoisted(() => {
  const series = { setData: vi.fn(), update: vi.fn() }
  const chart = {
    addSeries: vi.fn(() => series),
    resize: vi.fn(),
    timeScale: vi.fn(() => ({ fitContent: vi.fn(), scrollToRealTime: vi.fn() })),
    remove: vi.fn(),
  }
  return { series, chart, createChart: vi.fn(() => chart), setMarkers: vi.fn() }
})

const apiMocks = vi.hoisted(() => ({
  getNiftyCandles: vi.fn().mockImplementation((timeframe: string) => Promise.resolve({
    symbol: 'NIFTY', interval: timeframe, source: 'dhan_authoritative_1m', status: 'ready', market_state: 'open',
    trading_date: '2026-07-21', session_start: '2026-07-21T09:15:00+05:30',
    session_end: '2026-07-21T15:30:00+05:30', updated_at: '2026-07-21T04:00:00Z',
    candles: [{ time: Date.parse('2026-07-21T09:15:00+05:30') / 1000, open: 24100, high: 24120, low: 24090, close: 24110, volume: 1 }],
  })),
  getNiftyMarkers: vi.fn().mockResolvedValue({ trading_date: '2026-07-21', markers: [] }),
}))

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: {}, CrosshairMode: { Hidden: 0 },
  createChart: chartMocks.createChart,
  createSeriesMarkers: vi.fn(() => ({ setMarkers: chartMocks.setMarkers })),
}))

vi.mock('../api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api')>(),
  ...apiMocks,
}))

import { useSessionStore } from '../state/sessionStore'
import { NiftyLiveChart } from './NiftyLiveChart'

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

beforeEach(() => {
  useSessionStore.getState().resetSession()
  vi.stubGlobal('ResizeObserver', TestResizeObserver)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    width: 720, height: 320, top: 0, left: 0, right: 720, bottom: 320, x: 0, y: 0,
    toJSON: () => ({}),
  })
  chartMocks.createChart.mockClear()
  chartMocks.series.setData.mockClear()
  chartMocks.series.update.mockClear()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('NiftyLiveChart responsive runtime', () => {
  it('renders authoritative history and switches among 1m, 5m, and 15m', async () => {
    render(<NiftyLiveChart engineMode="paper" />)
    await waitFor(() => expect(chartMocks.createChart).toHaveBeenCalledTimes(1))
    expect(chartMocks.createChart).toHaveBeenCalledWith(expect.any(HTMLElement), expect.objectContaining({ width: 720, height: 320 }))
    await waitFor(() => expect(chartMocks.series.setData).toHaveBeenCalledTimes(1))
    expect(apiMocks.getNiftyCandles).toHaveBeenCalledWith('5m', expect.any(AbortSignal))
    fireEvent.click(screen.getByRole('button', { name: '1m' }))
    await waitFor(() => expect(apiMocks.getNiftyCandles).toHaveBeenCalledWith('1m', expect.any(AbortSignal)))
    fireEvent.click(screen.getByRole('button', { name: '15m' }))
    await waitFor(() => expect(apiMocks.getNiftyCandles).toHaveBeenCalledWith('15m', expect.any(AbortSignal)))
    expect(chartMocks.chart.resize).toHaveBeenCalledWith(720, 320)
  })

  it('falls back unsupported saved preferences to 5m', async () => {
    render(<NiftyLiveChart engineMode="paper" defaultTimeframe="1h" />)
    await waitFor(() => expect(apiMocks.getNiftyCandles).toHaveBeenCalledWith('5m', expect.any(AbortSignal)))
    expect(screen.getByRole('button', { name: '5m' })).toHaveAttribute('aria-pressed', 'true')
  })
})
