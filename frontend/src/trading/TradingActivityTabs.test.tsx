import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  activity: vi.fn(),
  engine: vi.fn(),
  executions: vi.fn(),
  alerts: vi.fn(),
  acknowledge: vi.fn(),
  preferences: vi.fn(),
}))
vi.mock('./terminalApi', () => ({
  getTradingActivity: api.activity,
  getTradingEngineLog: api.engine,
  getTradingExecutions: api.executions,
  getTradingAlerts: api.alerts,
  acknowledgeHistoricalAlerts: api.acknowledge,
}))
vi.mock('../settings/settingsApi', () => ({
  getPreferences: api.preferences,
}))
vi.mock('../automations/AutomationsPage', () => ({
  AutomationsPage: () => <div>Automation settings</div>,
}))

import { TradingActivityTabs } from './TradingActivityTabs'

const feed = (items: Array<Record<string, unknown>> = []) => ({
  ok: true,
  items,
  next_cursor: items.length ? 'next' : null,
  reconciliation_status: 'CURRENT',
})

beforeEach(() => {
  window.history.replaceState({}, '', '/app/trading')
  api.activity.mockResolvedValue(feed())
  api.engine.mockResolvedValue(feed())
  api.executions.mockResolvedValue(feed())
  api.alerts.mockResolvedValue({
    ...feed(),
    active_items: [],
    historical_items: [],
    unacknowledged_count: 0,
  })
  api.acknowledge.mockResolvedValue(undefined)
  api.preferences.mockResolvedValue({ notification_preferences: {} })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TradingActivityTabs', () => {
  it('consumes a Trading deep link and opens the correlated lifecycle', async () => {
    window.history.replaceState({}, '', '/app/trading?tab=activity&event=signal-1')
    api.activity.mockResolvedValue(feed([{
      id: 'event-1',
      occurred_at: '2026-07-26T09:00:00Z',
      correlation_id: 'signal-1',
      status: 'TRADED',
      lifecycle: [
        {
          stage: 'SIGNAL_RECEIVED',
          occurred_at: '2026-07-26T09:00:00Z',
          message: 'Signal persisted.',
        },
        {
          stage: 'ORDER_FILLED',
          occurred_at: '2026-07-26T09:00:01Z',
          message: 'Confirmed fill.',
        },
      ],
    }]))

    render(<TradingActivityTabs mode="paper" />)

    expect(await screen.findByRole('complementary', { name: 'Activity lifecycle' })).toBeInTheDocument()
    expect(screen.getByText('signal-1')).toBeInTheDocument()
    expect(screen.getByText('SIGNAL_RECEIVED')).toBeInTheDocument()
    expect(screen.getByText('ORDER_FILLED')).toBeInTheDocument()
  })

  it('pauses Engine Log autoscroll while reading history and offers Jump to latest', async () => {
    const user = userEvent.setup()
    api.engine.mockResolvedValue(feed([{
      id: 'log-1',
      occurred_at: '2026-07-26T09:00:00Z',
      level: 'INFO',
      event_type: 'ENGINE_STARTED',
      message: 'Engine started.',
    }]))
    const { container } = render(<TradingActivityTabs mode="paper" />)
    await user.click(screen.getByRole('tab', { name: 'Engine Log' }))
    await screen.findByText('Engine started.')

    const wrap = container.querySelector('.terminal-table-wrap') as HTMLDivElement
    Object.defineProperties(wrap, {
      scrollHeight: { configurable: true, value: 1000 },
      scrollTop: { configurable: true, value: 100, writable: true },
      clientHeight: { configurable: true, value: 200 },
    })
    fireEvent.scroll(wrap)
    expect(await screen.findByText(/paused while you read history/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Jump to latest' })).toBeInTheDocument()
  })

  it('keeps active alerts separate and acknowledges only historical alerts', async () => {
    const user = userEvent.setup()
    const active = {
      id: 'active-1',
      occurred_at: '2026-07-26T09:00:00Z',
      severity: 'WARNING',
      category: 'POSITION_QUOTE_STALE',
      message: 'Quote stale.',
      active: true,
      acknowledged: false,
    }
    const historical = {
      id: 'history-1',
      occurred_at: '2026-07-26T08:00:00Z',
      severity: 'ERROR',
      category: 'RISK_BLOCK',
      message: 'Daily loss cap reached.',
      active: false,
      acknowledged: false,
    }
    api.alerts.mockResolvedValue({
      ...feed([historical, active]),
      active_items: [active],
      historical_items: [historical],
      unacknowledged_count: 2,
    })
    const { container } = render(<TradingActivityTabs mode="paper" />)
    await user.click(screen.getByRole('tab', { name: 'Alerts' }))
    await screen.findByText('POSITION QUOTE STALE')
    expect(container.querySelector('.terminal-alert-summary .is-active')).toHaveTextContent('1 Active conditions')
    expect(container.querySelector('.terminal-alert-summary span:not(.is-active)')).toHaveTextContent('1 Historical alerts')
    await user.click(screen.getByRole('button', { name: 'Acknowledge historical' }))
    await waitFor(() => expect(api.acknowledge).toHaveBeenCalledWith(['history-1']))
  })

  it('presents executions with compact signal-style badges and quantity', async () => {
    const user = userEvent.setup()
    api.executions.mockResolvedValue(feed([{
      id: 'execution-1', occurred_at: '2026-07-26T09:00:00Z', mode: 'paper',
      instrument: 'NIFTY TEST CE', action: 'ENTRY', requested_qty: 75, filled_qty: 50,
      average_price: 101.5, charges: 7.25, status: 'TRADED', order_id: 'ORDER-1',
    }]))
    render(<TradingActivityTabs mode="paper" />)
    await user.click(screen.getByRole('tab', { name: 'Executions' }))
    expect(await screen.findByText('50 / 75')).toBeInTheDocument()
    expect(screen.getByText('PAPER')).toHaveClass('terminal-badge', 'is-paper')
    expect(screen.getByText('ENTRY')).toHaveClass('terminal-signal-value', 'is-positive')
    expect(screen.queryByRole('columnheader', { name: 'Slippage' })).toBeNull()
  })
})
