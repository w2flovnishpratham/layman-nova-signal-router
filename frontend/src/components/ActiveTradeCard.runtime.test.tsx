import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import type { ActiveTrade } from '../types'
import { ActiveTradeCard } from './ActiveTradeCard'

const trade: ActiveTrade = {
  mode: 'paper', symbol: 'NIFTY TEST CE', strike: 24200, optType: 'CE', qty: 65,
  avgPrice: 20.45, ltp: 20.8, pnl: 22.75, pnlPct: 1.71, orderId: 'PAPER-1',
  correlationId: '', status: 'OPEN', riskArmed: true, riskSource: 'server_monitor',
  activeExitLevels: { source: 'server_monitor', stopLossPrice: 18.41, targetPrice: 24.54 },
}

afterEach(() => cleanup())

describe('ActiveTradeCard runtime truth', () => {
  it('shows server-managed risk and Paper terminology without exchange pending labels', async () => {
    const user = userEvent.setup()
    render(<ActiveTradeCard trade={trade} lotSize={65} />)
    expect(screen.getByText(/SL ₹18.41 · TP ₹24.54/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /view paper order/i }))
    expect(screen.getByText('Confirmed')).toBeInTheDocument()
    expect(screen.getByText(/Simulated order · position persisted/)).toBeInTheDocument()
    expect(screen.queryByText('Exchange order')).not.toBeInTheDocument()
    expect(screen.queryByText('Correlation')).not.toBeInTheDocument()
  })

  it('does not claim risk is unarmed when status is unavailable', () => {
    render(<ActiveTradeCard trade={{ ...trade, activeExitLevels: null, riskArmed: undefined }} lotSize={65} />)
    expect(screen.getByText('Risk status unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Not armed')).not.toBeInTheDocument()
  })

  it('distinguishes an open-position quote failure and reports its source and age', () => {
    render(<ActiveTradeCard trade={{
      ...trade,
      quoteStatus: 'ltp_error',
      quoteSource: 'dhan_marketfeed_ws',
      quoteAgeSeconds: 19,
    }} lotSize={65} />)
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    expect(screen.getByText(/Live option quote unavailable · dhan_marketfeed_ws · 19s old/)).toBeInTheDocument()
  })
})
