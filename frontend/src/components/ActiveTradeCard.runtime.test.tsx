import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import type { ActiveTrade } from '../types'
import { ActiveTradeCard } from './ActiveTradeCard'
import { exitLotOptions } from './ActiveTradeCard.helpers'

const trade: ActiveTrade = {
  mode: 'paper', symbol: 'NIFTY TEST CE', strike: 24200, optType: 'CE', qty: 65,
  avgPrice: 20.45, ltp: 20.8, pnl: 22.75, pnlPct: 1.71, orderId: 'PAPER-1',
  correlationId: '', status: 'OPEN', riskArmed: true, riskSource: 'server_monitor',
  activeExitLevels: { source: 'server_monitor', stopLossPrice: 18.41, targetPrice: 24.54 },
}

afterEach(() => cleanup())

describe('ActiveTradeCard runtime truth', () => {
  it('offers Exit 1 through n-1 lots and one Exit All action', () => {
    expect(exitLotOptions(1)).toEqual([{ lots: null, label: 'Exit All' }])
    expect(exitLotOptions(2)).toEqual([
      { lots: 1, label: 'Exit 1 lot' },
      { lots: null, label: 'Exit All' },
    ])
    expect(exitLotOptions(4)).toEqual([
      { lots: 1, label: 'Exit 1 lot' },
      { lots: 2, label: 'Exit 2 lots' },
      { lots: 3, label: 'Exit 3 lots' },
      { lots: null, label: 'Exit All' },
    ])
    expect(exitLotOptions(4).some((option) => option.label === 'Exit 4 lots')).toBe(false)
  })

  it('keeps Live Add Lots and partial reductions disabled until broker verification', async () => {
    const user = userEvent.setup()
    render(
      <ActiveTradeCard
        trade={{ ...trade, mode: 'live', qty: 195, positionVersion: 1 }}
        lotSize={65}
      />,
    )
    expect(screen.getByRole('button', { name: 'Unavailable until broker verification' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Exit / Reduce' }))
    expect(screen.getByRole('button', { name: 'Exit 1 lot' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Exit 2 lots' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Exit All' })).toBeEnabled()
  })

  it('requires a Paper Add Lots protection choice', async () => {
    const user = userEvent.setup()
    render(<ActiveTradeCard trade={{ ...trade, positionVersion: 1 }} lotSize={65} />)
    await user.click(screen.getByRole('button', { name: 'Add Lots' }))
    const protection = screen.getByRole('combobox', { name: 'Protection after adding lots' })
    expect(protection).toHaveValue('KEEP_EXISTING')
    expect(screen.getByRole('option', { name: 'Keep existing trigger prices' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Recalculate from new average' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Custom SL/TP' })).toBeInTheDocument()
  })

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
