import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({ getWebhooksOverview: vi.fn() }))
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

afterEach(() => { cleanup(); apiMocks.getWebhooksOverview.mockReset() })

describe('WebhooksPage', () => {
  it('shows the real endpoint and never reveals the secret value', async () => {
    apiMocks.getWebhooksOverview.mockResolvedValue(overview())
    render(<WebhooksPage />)
    expect(await screen.findByText('TradingView alerts')).toBeInTheDocument()
    expect(screen.getByText('https://example.test/webhook/tradingview')).toBeInTheDocument()
    // Masked only, with an explicit statement that it cannot be revealed.
    expect(screen.getByText('••••9d4f')).toBeInTheDocument()
    expect(screen.getByText(/cannot be revealed here/i)).toBeInTheDocument()
    // No reveal affordance exists.
    expect(screen.queryByRole('button', { name: /reveal|show secret/i })).toBeNull()
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
})
