import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getCredentialsOverview: vi.fn(),
  verifyBroker: vi.fn(),
  disconnectBroker: vi.fn(),
  saveCredentials: vi.fn(),
}))
vi.mock('./credentialsApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./credentialsApi')>(),
  ...apiMocks,
}))

import { CredentialsPage } from './CredentialsPage'

const overview = (over: Record<string, unknown> = {}) => ({
  ok: true,
  broker: {
    key: 'dhan', name: 'Dhan', connected: true,
    client_id: '9107xxxx1097', client_id_masked: '••••1097',
    has_client_id: true, has_access_token: true, has_webhook_secret: true,
    token_saved_at: '2026-07-24T09:00:00Z', token_expires_at: null, expiry_known: false,
    last_updated_at: '2026-07-24T09:00:00Z',
    connection_status: 'CONNECTED', last_verified_at: '2026-07-24T09:00:00Z',
    last_verification_error: null, last_wallet_snapshot_at: '2026-07-24T09:00:00Z',
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
  apiMocks.saveCredentials.mockReset()
})

describe('CredentialsPage', () => {
  it('offers no way to reveal a secret', async () => {
    apiMocks.getCredentialsOverview.mockResolvedValue(overview())
    render(<CredentialsPage />)
    await screen.findByText('Configured')
    expect(screen.getByText(/cannot be revealed here/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reveal|show (secret|token)/i })).toBeNull()
    // Stored secrets are reported as presence only.
    expect(screen.getByText('Saved')).toBeInTheDocument()
  })

  it('autofills the stored Client ID but never the Access Token', async () => {
    apiMocks.getCredentialsOverview.mockResolvedValue(overview())
    render(<CredentialsPage />)
    const clientIdInput = await screen.findByLabelText(/dhan client id/i) as HTMLInputElement
    await waitFor(() => expect(clientIdInput.value).toBe('9107xxxx1097'))
    const tokenInput = screen.getByLabelText(/dhan access token/i) as HTMLInputElement
    expect(tokenInput.value).toBe('')
    expect(tokenInput.placeholder).toMatch(/leave blank to keep the existing token/i)
  })

  it('saves only changed fields and never re-verifies with a blank token clearing the saved one', async () => {
    apiMocks.getCredentialsOverview.mockResolvedValue(overview())
    apiMocks.saveCredentials.mockResolvedValue(undefined)
    apiMocks.verifyBroker.mockResolvedValue({ success: true, message: 'Dhan token valid.' })
    render(<CredentialsPage />)
    await screen.findByText('Configured')
    fireEvent.click(screen.getByRole('button', { name: /save and verify/i }))
    await waitFor(() => expect(apiMocks.saveCredentials).toHaveBeenCalledWith({ clientId: '9107xxxx1097', accessToken: '' }))
    await waitFor(() => expect(apiMocks.verifyBroker).toHaveBeenCalledTimes(1))
  })

  it('shows the connection status label', async () => {
    apiMocks.getCredentialsOverview.mockResolvedValue(overview({
      broker: { ...overview().broker, connection_status: 'TOKEN_EXPIRED', connected: false },
    }))
    render(<CredentialsPage />)
    expect(await screen.findByText('Access token expired')).toBeInTheDocument()
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
