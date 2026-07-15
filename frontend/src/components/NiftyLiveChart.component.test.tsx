import { render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NiftyLiveChart } from './NiftyLiveChart'

const mocks = vi.hoisted(() => ({
  addSeries: vi.fn(),
  createChart: vi.fn(),
  createMarkers: vi.fn(),
  fitContent: vi.fn(),
  remove: vi.fn(),
  scrollToRealTime: vi.fn(),
  setData: vi.fn(),
  setMarkers: vi.fn(),
  update: vi.fn(),
  state: {
    marketSnapshot: {
      niftySpot: 101,
      marketStatus: 'open' as const,
      lastUpdatedAt: '2026-07-15T09:22:35+05:30',
      atm: {
        niftySpotReceivedAt: '2026-07-15T09:22:35+05:30',
        marketfeed: { connected: true },
      },
    },
    wsStatus: 'live' as const,
  },
}))

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: {},
  CrosshairMode: { Hidden: 0 },
  createChart: mocks.createChart,
  createSeriesMarkers: mocks.createMarkers,
}))

vi.mock('../state/sessionStore', () => ({
  useSessionStore: (selector: (state: typeof mocks.state) => unknown) => selector(mocks.state),
}))

vi.mock('../api', async (loadOriginal) => {
  const actual = await loadOriginal<typeof import('../api')>()
  const time = Date.parse('2026-07-15T09:20:00+05:30') / 1000
  return {
    ...actual,
    getNiftyCandles: vi.fn().mockResolvedValue({
      symbol: 'NIFTY', interval: '5m', source: 'dhan', status: 'ready', market_state: 'open',
      trading_date: '2026-07-15', session_start: '2026-07-15T09:15:00+05:30',
      session_end: '2026-07-15T15:30:00+05:30', updated_at: '2026-07-15T09:22:35+05:30',
      candles: [{ time, open: 100, high: 101, low: 99, close: 100 }],
    }),
    getNiftyMarkers: vi.fn().mockResolvedValue({ symbol: 'NIFTY', trading_date: '2026-07-15', markers: [] }),
  }
})

afterEach(() => vi.clearAllMocks())

describe('NiftyLiveChart lifecycle', () => {
  it('creates one read-only chart, sets history, updates live, and disposes it', async () => {
    mocks.addSeries.mockReturnValue({ setData: mocks.setData, update: mocks.update })
    mocks.createMarkers.mockReturnValue({ setMarkers: mocks.setMarkers })
    mocks.createChart.mockReturnValue({
      addSeries: mocks.addSeries,
      remove: mocks.remove,
      timeScale: () => ({ fitContent: mocks.fitContent, scrollToRealTime: mocks.scrollToRealTime }),
    })

    const view = render(<NiftyLiveChart engineMode="paper" />)
    await waitFor(() => expect(mocks.createChart).toHaveBeenCalledTimes(1))
    expect(mocks.createChart.mock.calls[0][1]).toMatchObject({
      handleScroll: false,
      handleScale: false,
      crosshair: { mode: 0 },
      kineticScroll: { touch: false, mouse: false },
    })
    expect(mocks.setData).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(mocks.update).toHaveBeenCalled())
    view.rerender(<NiftyLiveChart engineMode="paper" />)
    expect(mocks.createChart).toHaveBeenCalledTimes(1)
    view.unmount()
    expect(mocks.remove).toHaveBeenCalledTimes(1)
  })
})
