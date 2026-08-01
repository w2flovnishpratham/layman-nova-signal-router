import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getWebhooksOverview: vi.fn(),
  rotateWebhookSecret: vi.fn(),
}))
vi.mock('./webhooksApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./webhooksApi')>(),
  ...apiMocks,
}))

import { WebhooksPage } from './WebhooksPage'

const overview = (over: Record<string, unknown> = {}) => ({
  ok: true,
  available: true,
  endpoints: [{
    key: 'tradingview', label: 'TradingView alerts', url: 'https://example.test/webhook/tradingview',
    method: 'POST', description: 'Public TradingView alert receiver, verified with your webhook secret.',
  }],
  secret: { set: true, masked: '••••9d4f', source: 'vault' },
  window_hours: 24,
  deliveries: { counts: { accepted: 2, total: 2 }, signature_verified: 2, last_delivery_at: '2026-07-23T08:07:45.128Z' },
  recent: [{
    id: 'r1', received_at: '2026-07-23T08:07:45.128Z', provider: 'tradingview', event_id: 'evt-1',
    signature_ok: true, replay_status: 'fresh', processed_status: 'accepted', error: null,
    strategy_name: null, signal_status: null, summary: {}, raw_body_sha256: 'a'.repeat(64),
  }],
  ...over,
})

afterEach(() => {
  cleanup()
  apiMocks.getWebhooksOverview.mockReset()
  apiMocks.rotateWebhookSecret.mockReset()
  vi.restoreAllMocks()
})

describe('WebhooksPage', () => {
  it('rotates in the backend and only copies the newly returned secret', async () => {
    apiMocks.getWebhooksOverview.mockResolvedValue(overview())
    apiMocks.rotateWebhookSecret.mockResolvedValue('new-full-secret-9274')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<WebhooksPage />)
    expect(await screen.findByText('TradingView alerts')).toBeInTheDocument()
    expect(screen.getByText('https://example.test/webhook/tradingview')).toBeInTheDocument()
    expect(screen.queryByText('••••9d4f')).toBeNull()
    expect(screen.queryByRole('button', { name: /copy masked/i })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /rotate secret/i }))
    expect(await screen.findByText('new-full-secret-9274')).toBeInTheDocument()
    expect(apiMocks.rotateWebhookSecret).toHaveBeenCalledOnce()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    fireEvent.click(screen.getByRole('button', { name: /copy secret/i }))
    expect(writeText).toHaveBeenCalledWith('new-full-secret-9274')
  })

  it('does not invent per-strategy endpoints', async () => {
    apiMocks.getWebhooksOverview.mockResolvedValue(overview())
    render(<WebhooksPage />)
    await screen.findByText('TradingView alerts')
    // The mockup showed nova-supertrend / nova-orb endpoints; those do not exist.
    expect(screen.queryByText(/nova-supertrend/i)).toBeNull()
    expect(screen.queryByText(/nova-orb/i)).toBeNull()
  })

  it('states truthfully when no secret is configured', async () => {
    apiMocks.getWebhooksOverview.mockResolvedValue(overview({ secret: { set: false, masked: null, source: null } }))
    render(<WebhooksPage />)
    expect(await screen.findByText(/not configured/i)).toBeInTheDocument()
  })

  it('shows a truthful empty delivery state', async () => {
    apiMocks.getWebhooksOverview.mockResolvedValue(overview({
      recent: [], deliveries: { counts: {}, signature_verified: 0, last_delivery_at: null },
    }))
    render(<WebhooksPage />)
    expect(await screen.findByText(/no deliveries recorded yet/i)).toBeInTheDocument()
    expect(screen.getByText(/last delivery never/i)).toBeInTheDocument()
  })

  it('uses distinct colors for delivery and signature states', async () => {
    const base = overview().recent[0]
    apiMocks.getWebhooksOverview.mockResolvedValue(overview({
      recent: [
        { ...base, id: 'received', event_id: 'evt-received', processed_status: 'received', signature_ok: false },
        { ...base, id: 'queued', event_id: 'evt-queued', processed_status: 'queued' },
        { ...base, id: 'accepted', event_id: 'evt-accepted', processed_status: 'accepted' },
        { ...base, id: 'fanned', event_id: 'evt-fanned', processed_status: 'fanned_out' },
        { ...base, id: 'rejected', event_id: 'evt-rejected', processed_status: 'rejected' },
      ],
    }))

    render(<WebhooksPage />)
    const statusFor = (eventId: string) =>
      screen.getByText(eventId).closest('tr')?.querySelector('td:nth-child(3) span')
    const signatureFor = (eventId: string) =>
      screen.getByText(eventId).closest('tr')?.querySelector('td:nth-child(4) span')

    await screen.findByText('evt-received')
    expect(statusFor('evt-received')).toHaveClass('nova-sig-info')
    expect(statusFor('evt-queued')).toHaveClass('nova-sig-warn')
    expect(statusFor('evt-accepted')).toHaveClass('nova-sig-ok')
    expect(statusFor('evt-fanned')).toHaveClass('nova-sig-ok')
    expect(statusFor('evt-rejected')).toHaveClass('nova-sig-bad')
    expect(signatureFor('evt-received')).toHaveClass('nova-sig-bad')
  })
})
