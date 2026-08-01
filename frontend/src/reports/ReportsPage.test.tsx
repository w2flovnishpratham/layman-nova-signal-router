import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({ getReport: vi.fn() }))
vi.mock('./reportsApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./reportsApi')>(),
  ...apiMocks,
}))

import { ReportsPage } from './ReportsPage'

const report = (over: Record<string, unknown> = {}) => ({
  ok: true,
  mode: 'paper',
  trade_origin: 'all',
  period: { start: '2026-07-01', end: '2026-07-31', timezone: 'Asia/Kolkata' },
  totals: {
    trades: 3, sessions: 1, winning_trades: 1, losing_trades: 1, scratch_trades: 1,
    gross_profit: 500, gross_loss: 100, net_pnl: 400,
  },
  win_rate: { value: 50, reason: null },
  average_winner: { value: 500, reason: null },
  average_loser: { value: -100, reason: null },
  profit_factor: { value: 5, reason: null },
  max_drawdown: { value: 100, reason: null, basis: 'closed_trade_equity' },
  daily_sessions: [{
    date: '2026-07-24',
    strategy_mix: ['NOVA Supertrend', 'Manual Orders'],
    trades: 3,
    wins: 1,
    losses: 1,
    win_rate: { value: 50, reason: null },
    max_drawdown: { value: 100, reason: null, basis: 'closed_trade_equity' },
    net_pnl: 400,
    manual_orders: 1,
    mode: 'paper',
  }],
  by_strategy: [
    { display_name: 'NOVA Supertrend', realized_pnl: 300, closed_trades: 2, wins: 1, losses: 1, win_rate: { value: 50, reason: null }, contribution_percentage: 75 },
    { display_name: 'Manual Orders', realized_pnl: -100, closed_trades: 1, wins: 0, losses: 1, win_rate: { value: 0, reason: null }, contribution_percentage: 25 },
  ],
  ...over,
})

afterEach(() => {
  cleanup()
  apiMocks.getReport.mockReset()
  window.history.replaceState(null, '', '/app/reports')
})

describe('ReportsPage', () => {
  it('renders monthly metrics, daily sessions, calendar and manual attribution', async () => {
    apiMocks.getReport.mockResolvedValue(report())
    window.history.replaceState(null, '', '/app/reports?month=2026-07')
    render(<ReportsPage initialMode="paper" />)
    expect((await screen.findAllByText('+₹400')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('50%').length).toBeGreaterThan(0)
    expect(screen.getByText('NOVA Supertrend · Manual Orders')).toBeInTheDocument()
    expect(screen.getAllByText('Manual Orders').length).toBeGreaterThan(0)
    const strategyContribution = screen.getByText('75%', { selector: '.nova-report-contribution' })
    expect(strategyContribution.closest('[data-slot="tooltip-trigger"]')).toBeNull()
    expect(strategyContribution).not.toHaveAttribute('title')
    expect(screen.getByText('-25%', { selector: '.nova-report-contribution' })).toHaveClass('is-loss')
    const calendarDay = screen.getByLabelText(/24 Jul.*400 realized.*3 closed trades/i)
    expect(calendarDay).toHaveAttribute('data-slot', 'tooltip-trigger')
    expect(calendarDay).toHaveAttribute('tabindex', '0')
    expect(calendarDay).not.toHaveAttribute('title')
  })

  it('persists mode, origin and month in the URL and sends them to the API', async () => {
    const user = userEvent.setup()
    apiMocks.getReport.mockResolvedValue(report())
    window.history.replaceState(null, '', '/app/reports?month=2026-07')
    render(<ReportsPage initialMode="paper" />)
    await screen.findAllByText('+₹400')
    await user.click(screen.getByRole('button', { name: 'Live' }))
    await user.click(screen.getByRole('button', { name: 'Manual Only' }))
    await waitFor(() => expect(apiMocks.getReport).toHaveBeenLastCalledWith('2026-07-01', '2026-07-31', 'live', 'manual'))
    expect(window.location.search).toContain('mode=live')
    expect(window.location.search).toContain('origin=manual')
    expect(window.location.search).toContain('month=2026-07')
  })

  it('shows a filter-specific truthful empty state', async () => {
    apiMocks.getReport.mockResolvedValue(report({
      trade_origin: 'manual',
      totals: { trades: 0, sessions: 0, winning_trades: 0, losing_trades: 0, scratch_trades: 0, gross_profit: 0, gross_loss: 0, net_pnl: 0 },
      daily_sessions: [],
      by_strategy: [],
    }))
    window.history.replaceState(null, '', '/app/reports?mode=paper&origin=manual&month=2026-07')
    render(<ReportsPage />)
    expect(await screen.findByText('No manual trades recorded for this period.')).toBeInTheDocument()
  })

  it('offers filter-scoped CSV and PDF downloads', async () => {
    apiMocks.getReport.mockResolvedValue(report())
    render(<ReportsPage />)
    const csv = await screen.findByRole('link', { name: /Download CSV/i })
    const pdf = screen.getByRole('link', { name: /Download PDF/i })
    expect(csv.getAttribute('href')).toContain('/api/reports/export.csv')
    expect(pdf.getAttribute('href')).toContain('/api/reports/export.pdf')
    expect(csv.getAttribute('href')).toContain('trade_origin=all')
  })

})
