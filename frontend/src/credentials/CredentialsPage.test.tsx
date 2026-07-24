import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getCredentialsOverview: vi.fn(),
  verifyBroker: vi.fn(),
  disconnectBroker: vi.fn(),
}))
vi.mock('./credentialsApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./credentialsApi')>(),
  ...apiMocks,
}))

import { CredentialsPage } from './CredentialsPage'

const overview = (over: Record<string, unknown> = {}) => ({
  ok: true,
  broker: {
    key: 'dhan', name: 'Dhan', connected: true, client_id_masked: '••••1097',
    has_client_id: true, has_access_token: true, has_api_secret: true, has_webhook_secret: true,
    token_saved_at: '2026-07-24T09:00:00Z', token_expires_at: null, expiry_known: false,
    last_updated_at: '2026-07-24T09:00:00Z',
  },
  static_ip: { available: true, status: 'verified', ip: '203.0.113.9', verified_at: '2026-07-24T09:00:00Z' },
  eligibility: { paper: true, paper_reason: null, live: false, live_blockers: ['Live orders are disabled on this deployment.'] },
  mode: { dhan_mode: 'MOCK', live_orders_enabled: false },
  supported_brokers: ['dhan'],
  ...over,
})

afterEach(() => {
  cleanup()
  apiMocks.getCredentialsOverview.mockReset()
  apiMocks.verifyBroker.mockReset()
  apiMocks.disconnectBroker.mockReset()
})

describe('CredentialsPage', () => {
  it('shows masked values and offers no way to reveal a secret', async () => {
    apiMocks.getCredentialsOverview.mockResolvedValue(overview())
    render(<CredentialsPage />)
    expect(await screen.findByText('••••1097')).toBeInTheDocument()
    expect(screen.getByText(/cannot be revealed here/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reveal|show (secret|token)/i })).toBeNull()
    // Stored secrets are reported as presence only.
    expect(screen.getAllByText('Stored').length).toBeGreaterThan(0)
  })

  it('does not invent a token expiry the broker never provided', async () => {
    apiMocks.getCredentialsOverview.mockResolvedValue(overview())
    render(<CredentialsPage />)
    expect(await screen.findByText(/not recorded by the broker/i)).toBeInTheDocument()
  })

  it('reports the server verdict from Verify again', async () => {
    apiMocks.getCredentialsOverview.mockResolvedValue(overview())
    apiMocks.verifyBroker.mockResolvedValue({ success: false, message: 'Token rejected by Dhan.' })
    render(<CredentialsPage />)
    fireEvent.click(await screen.findByRole('button', { name: /verify again/i }))
    expect(await screen.findByText('Token rejected by Dhan.')).toBeInTheDocument()
  })

  it('requires an explicit confirmation before disconnecting', async () => {
    apiMocks.getCredentialsOverview.mockResolvedValue(overview())
    apiMocks.disconnectBroker.mockResolvedValue(undefined)
    render(<CredentialsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }))
    expect(apiMocks.disconnectBroker).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /yes, disconnect/i }))
    await waitFor(() => expect(apiMocks.disconnectBroker).toHaveBeenCalledTimes(1))
  })

  it('states every reason Live is unavailable', async () => {
    apiMocks.getCredentialsOverview.mockResolvedValue(overview())
    render(<CredentialsPage />)
    expect(await screen.findByText('Live orders are disabled on this deployment.')).toBeInTheDocument()
  })

  it('advertises no broker other than Dhan', async () => {
    apiMocks.getCredentialsOverview.mockResolvedValue(overview())
    render(<CredentialsPage />)
    await screen.findByText('Dhan')
    for (const other of [/zerodha/i, /upstox/i, /angel/i, /fyers/i]) {
      expect(screen.queryByText(other)).toBeNull()
    }
  })
})
