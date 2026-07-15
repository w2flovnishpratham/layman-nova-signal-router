/// <reference types="node" />
import { render, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NiftyLiveChart } from './NiftyLiveChart'

const responsiveCss = readFileSync('src/index.css', 'utf8')

const mocks = vi.hoisted(() => ({
  addSeries: vi.fn(),
  createChart: vi.fn(),
  createMarkers: vi.fn(),
  fitContent: vi.fn(),
  remove: vi.fn(),
  resize: vi.fn(),
  observe: vi.fn(),
  disconnect: vi.fn(),
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

let resizeCallback: ResizeObserverCallback

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

beforeEach(() => {
  class ResizeObserverMock {
    constructor(callback: ResizeObserverCallback) {
      resizeCallback = callback
    }
    observe = mocks.observe
    disconnect = mocks.disconnect
    unobserve = vi.fn()
  }
  vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    callback(0)
    return 1
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('NiftyLiveChart lifecycle', () => {
  it('creates one read-only chart, sets history, updates live, and disposes it', async () => {
    mocks.addSeries.mockReturnValue({ setData: mocks.setData, update: mocks.update })
    mocks.createMarkers.mockReturnValue({ setMarkers: mocks.setMarkers })
    mocks.createChart.mockReturnValue({
      addSeries: mocks.addSeries,
      remove: mocks.remove,
      resize: mocks.resize,
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
    expect(mocks.observe).toHaveBeenCalledTimes(1)
    const chartCreations = mocks.createChart.mock.calls.length
    const seriesCreations = mocks.addSeries.mock.calls.length
    const historyWrites = mocks.setData.mock.calls.length
    resizeCallback([{ contentRect: { width: 640.9, height: 320.7 } } as ResizeObserverEntry], {} as ResizeObserver)
    expect(mocks.resize).toHaveBeenCalledWith(640, 320)
    resizeCallback([{ contentRect: { width: 0, height: 320 } } as ResizeObserverEntry], {} as ResizeObserver)
    expect(mocks.resize).toHaveBeenCalledTimes(1)
    expect(mocks.createChart).toHaveBeenCalledTimes(chartCreations)
    expect(mocks.addSeries).toHaveBeenCalledTimes(seriesCreations)
    expect(mocks.setData).toHaveBeenCalledTimes(historyWrites)
    view.rerender(<NiftyLiveChart engineMode="paper" />)
    expect(mocks.createChart).toHaveBeenCalledTimes(1)
    expect(mocks.observe).toHaveBeenCalledTimes(1)
    view.unmount()
    expect(mocks.disconnect).toHaveBeenCalledTimes(1)
    expect(mocks.remove).toHaveBeenCalledTimes(1)
  })

  it('keeps the chart column shrinkable and stacks below the desktop breakpoint', () => {
    expect(responsiveCss).toContain('grid-template-columns: auto minmax(0, 1fr) auto;')
    expect(responsiveCss).toMatch(/\.engine-shell-grid\s*{[^}]*grid-template-columns: minmax\(0, 1fr\)/s)
    expect(responsiveCss).toMatch(/\.nifty-chart-canvas,[^}]*width: 100%;[^}]*max-width: 100%;[^}]*min-width: 0;/s)
    expect(responsiveCss).not.toMatch(/\.nifty-chart-canvas[^}]*width:\s*\d+px/s)
    expect(responsiveCss).toContain('@media (min-width: 1024px)')
  })
})
