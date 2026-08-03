import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({ getSignals: vi.fn() }))
vi.mock('./signalsApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./signalsApi')>(),
  ...apiMocks,
}))

import { SignalsPage } from './SignalsPage'

const page = (over: Record<string, unknown> = {}) => ({
  ok: true,
  available: true,
  items: [{
    id: 'r1', received_at: '2026-07-23T08:07:45.128Z', provider: 'tradingview',
    event_id: 'evt-1', signature_ok: true, replay_status: 'fresh',
    processed_status: 'accepted', error: null, strategy_name: 'nova-supertrend',
    signal_status: 'accepted', summary: { action: 'ENTRY' }, raw_body_sha256: 'a'.repeat(64),
  }],
  next_cursor: null,
  counts: { accepted: 1, total: 1 },
  counts_window_hours: 24,
  ...over,
})

afterEach(() => { cleanup(); apiMocks.getSignals.mockReset() })

describe('SignalsPage', () => {
  it('renders only real rows returned by the API', async () => {
    apiMocks.getSignals.mockResolvedValue(page({
      items: [{
        ...page().items[0],
        summary: {
          action: 'ENTRY',
          side: 'BUY',
          option_side: 'CE',
          symbol: 'NIFTY',
          strike: 22950,
          alert: 'ST_FLIP_UP',
        },
      }],
    }))
    render(<SignalsPage />)
    expect(await screen.findByText('nova-supertrend')).toBeInTheDocument()
    expect(screen.getByText('ST_FLIP_UP')).toBeInTheDocument()
    expect(screen.getByText('BUY CE')).toBeInTheDocument()
    expect(screen.getByText('NIFTY 22950 CE')).toBeInTheDocument()
    expect(screen.getByText('verified')).toBeInTheDocument() // non-colour label
    const row = screen.getByText('ST_FLIP_UP').closest('tr')!
    expect(row.textContent).toContain('accepted')
    expect(screen.queryByPlaceholderText(/search/i)).toBeNull()
    expect(screen.queryByText(/latency/i)).toBeNull()
  })

  it('shows a truthful empty state rather than placeholder rows', async () => {
    apiMocks.getSignals.mockResolvedValue(page({ items: [], counts: {} }))
    render(<SignalsPage />)
    expect(await screen.findByText(/no signals recorded yet/i)).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('surfaces a load failure with a working Retry', async () => {
    const user = userEvent.setup()
    apiMocks.getSignals.mockRejectedValueOnce(new Error('boom')).mockResolvedValue(page())
    render(<SignalsPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByText('nova-supertrend')).toBeInTheDocument())
  })

  it('filters by a persisted status and never offers the mockup vocabulary', async () => {
    const user = userEvent.setup()
    apiMocks.getSignals.mockResolvedValue(page())
    render(<SignalsPage />)
    await screen.findByText('nova-supertrend')
    await user.click(screen.getByRole('button', { name: 'rejected' }))
    await waitFor(() => expect(apiMocks.getSignals).toHaveBeenCalledWith(expect.objectContaining({ status: 'rejected' })))
    // "routed"/"duplicate" are not persisted states, so they are not offered.
    expect(screen.queryByRole('button', { name: 'routed' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'duplicate' })).toBeNull()
  })

  it('pages through the real cursor API and can return to the previous page', async () => {
    const user = userEvent.setup()
    const firstItem = page().items[0]
    const secondItem = { ...firstItem, id: 'r2', event_id: 'evt-2' }
    apiMocks.getSignals
      .mockResolvedValueOnce(page({ next_cursor: 'cursor-2' }))
      .mockResolvedValueOnce(page({ items: [secondItem], next_cursor: null }))
      .mockResolvedValueOnce(page({ next_cursor: 'cursor-2' }))

    render(<SignalsPage />)
    await screen.findByText('evt-1')
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(apiMocks.getSignals).toHaveBeenCalledWith({
      status: 'all',
      cursor: 'cursor-2',
      limit: 10,
    }))
    expect(await screen.findByText('evt-2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Previous page' }))
    await waitFor(() => expect(apiMocks.getSignals).toHaveBeenLastCalledWith({
      status: 'all',
      cursor: null,
      limit: 10,
    }))
    expect(await screen.findByText('evt-1')).toBeInTheDocument()
  })
})
