import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getOrderQuotes: vi.fn().mockResolvedValue({ CE: null, PE: null }),
  postManualExit: vi.fn().mockResolvedValue({
    ok: true,
    message: 'Paper position closed.',
    operationState: 'POSITION_CLOSED',
    position: { has_open_position: false },
    portfolio: { available_balance: 99817.18, utilized_amount: 0, session_pnl: -182.82 },
  }),
  postManualEntry: vi.fn(),
  postManualReverse: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api')>(),
  ...apiMocks,
}))

import { ManualOrderPanel } from './ManualOrderPanel'

afterEach(() => {
  cleanup()
  apiMocks.postManualExit.mockClear()
})

describe('ManualOrderPanel runtime exposure fallback', () => {
  it('keeps Exit Position visible from backend runtime proof and emits one exit request', async () => {
    const user = userEvent.setup()
    render(<ManualOrderPanel engineMode="paper" activeTrade={null} runtimePositionOpen />)
    const exit = screen.getByRole('button', { name: 'Exit Position' })
    expect(exit).toBeVisible()
    await user.click(exit)
    expect(screen.getByRole('dialog', { name: 'Confirm Paper position exit' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: /confirm exit/i }))
    await waitFor(() => expect(apiMocks.postManualExit).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('Position closed')).toBeVisible()
  })
})

describe('trading action colour semantics', () => {
  it('offers direct CE and PE buy actions and accepts whole-number lots', async () => {
    const user = userEvent.setup()
    render(<ManualOrderPanel engineMode="paper" activeTrade={null} />)
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Buy CE market' })).toHaveClass('is-ce')
    expect(screen.getByRole('button', { name: 'Buy PE market' })).toHaveClass('is-pe')

    const lots = screen.getByRole('spinbutton', { name: 'Manual order lots' })
    await user.clear(lots)
    await user.type(lots, '3')
    expect(lots).toHaveValue(3)
  })

  it('keeps Exit Position out of the buy-green treatment', () => {
    render(<ManualOrderPanel engineMode="paper" activeTrade={null} runtimePositionOpen />)
    const exit = screen.getByRole('button', { name: 'Exit Position' })
    expect(exit.className).not.toMatch(/emerald/)
  })
})
