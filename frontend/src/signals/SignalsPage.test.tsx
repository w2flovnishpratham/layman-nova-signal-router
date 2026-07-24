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
    apiMocks.getSignals.mockResolvedValue(page())
    render(<SignalsPage />)
    expect(await screen.findByText('nova-supertrend')).toBeInTheDocument()
    expect(screen.getByText('evt-1')).toBeInTheDocument()
    expect(screen.getByText('verified')).toBeInTheDocument() // non-colour label
    // Status renders inside the row (the word also appears as a filter/count chip).
    const row = screen.getByText('evt-1').closest('tr')!
    expect(row.textContent).toContain('accepted')
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
})
