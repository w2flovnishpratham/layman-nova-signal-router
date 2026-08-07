import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getRiskPageData: vi.fn(),
  saveRiskConfiguration: vi.fn(),
  triggerRiskKillSwitch: vi.fn(),
}))
vi.mock('./riskApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./riskApi')>(),
  ...apiMocks,
}))

import { RiskPage } from './RiskPage'
import type { RuntimeStatus } from '../api'
import type { RiskConfiguration, RiskPreset, RiskValues } from './riskApi'
import { withQueryClient } from '../test/testQueryClient'

const balanced: RiskValues = {
  daily_loss_cap: 25_000,
  max_loss_per_trade: 2_500,
  max_trades_per_day: 6,
  max_open_positions: 3,
  lots_per_trade_min: 1,
  lots_per_trade_max: 2,
  cooldown_minutes: 30,
  exit_mode: 'CUSTOM_SL_TP',
  stop_loss_value: 75,
  take_profit_value: 150,
  stop_loss_basis: 'POINTS',
  take_profit_basis: 'POINTS',
  margin_exposure_cap: null,
}

const presets: RiskPreset[] = [
  { key: 'CONSERVATIVE', name: 'Conservative', description: 'Lower exposure.', mode: 'PAPER', values: { ...balanced, daily_loss_cap: 10_000, max_trades_per_day: 3, lots_per_trade_max: 1, cooldown_minutes: 60 } },
  { key: 'BALANCED', name: 'Balanced', description: 'Suggested default.', mode: 'PAPER', values: balanced },
  { key: 'AGGRESSIVE', name: 'Aggressive', description: 'Higher capacity.', mode: 'PAPER', values: { ...balanced, daily_loss_cap: 50_000, max_trades_per_day: 12, lots_per_trade_max: 5, cooldown_minutes: 15 } },
]

const configuration: RiskConfiguration = {
  mode: 'PAPER',
  activeVersion: 8,
  activeVersionId: 'risk-v8',
  profileType: 'CUSTOM',
  basedOnPreset: 'BALANCED',
  values: { ...balanced, daily_loss_cap: 20_000, max_trades_per_day: 4 },
  updatedAt: '2026-07-29T16:02:00Z',
  changeSource: 'TRADING_TERMINAL',
  suggestedDefault: false,
}

function pageData(over: Partial<RiskConfiguration> = {}) {
  const active = { ...configuration, ...over }
  return {
    presets,
    configuration: active,
    overview: {
      ok: true,
      available: true,
      trade_date_ist: '2026-07-29',
      mode: 'paper',
      configuration: active,
      today_usage: {
        daily_loss: { used: 168_000, limit: 2_000_000, unlimited: false, pct: 8.4 },
        trades: { used: 3, limit: 4, unlimited: false, pct: 75 },
        open_positions: { used: 1, limit: 3, unlimited: false, pct: 33.3 },
        margin_exposure: { used: 0, limit: 0, unlimited: true, pct: null },
      },
      breaker_history: [],
    },
  }
}

function runtime(running = false, mode: 'paper' | 'live' | null = null): RuntimeStatus {
  return {
    engine: { running, mode },
  } as RuntimeStatus
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  window.history.replaceState(null, '', '/app/risk')
})

describe('RiskPage', () => {
  it('loads the saved owner/mode configuration and real usage', async () => {
    apiMocks.getRiskPageData.mockResolvedValue(pageData())
    render(withQueryClient(<RiskPage runtime={runtime()} />))
    expect(await screen.findByText(/^Custom$/)).toBeInTheDocument()
    expect(screen.getByText(/Updated from Trading Terminal/)).toBeInTheDocument()
    expect(screen.getByDisplayValue('20000')).toBeInTheDocument()
    const dailyUsage = screen.getByText('Daily loss').closest('.nova-risk-usage-row')
    expect(dailyUsage).toHaveTextContent('₹1,680 / ₹20,000 · 8.4%')
    expect(dailyUsage).toHaveStyle({
      '--usage-color': 'hsl(41.312 92% 62%)',
    })
    expect(screen.getByText('Trades taken').closest('.nova-risk-usage-row')).toHaveStyle({
      '--usage-color': 'hsl(14 92% 62%)',
    })
    expect(screen.getByText('Open positions').closest('.nova-risk-usage-row')).not.toHaveAttribute('data-pressure')
  })

  it('loads a fixed preset into the editor without saving it', async () => {
    const user = userEvent.setup()
    apiMocks.getRiskPageData.mockResolvedValue(pageData())
    render(withQueryClient(<RiskPage runtime={runtime()} />))
    await user.click(await screen.findByRole('button', { name: /Conservative.*Lower exposure/i }))
    expect(screen.getByDisplayValue('10000')).toBeInTheDocument()
    expect(screen.getByText(/Unsaved changes/)).toBeInTheDocument()
    expect(apiMocks.saveRiskConfiguration).not.toHaveBeenCalled()
  })

  it('saves a new version with optimistic concurrency', async () => {
    const user = userEvent.setup()
    apiMocks.getRiskPageData.mockResolvedValue(pageData())
    apiMocks.saveRiskConfiguration.mockResolvedValue({ ...configuration, activeVersion: 9 })
    render(withQueryClient(<RiskPage runtime={runtime()} />))
    const daily = await screen.findByLabelText('Maximum daily loss (₹, 0 for no limit)')
    await user.clear(daily)
    await user.type(daily, '18000')
    await user.click(screen.getByRole('button', { name: 'Save Risk Settings' }))
    await waitFor(() => expect(apiMocks.saveRiskConfiguration).toHaveBeenCalledWith(expect.objectContaining({
      mode: 'paper',
      basedOnPreset: 'BALANCED',
      expectedVersion: 8,
      changeSource: 'RISK_PAGE',
      values: expect.objectContaining({ daily_loss_cap: 18_000 }),
    })))
  })

  it('keeps Paper and Live as separate requests', async () => {
    const user = userEvent.setup()
    apiMocks.getRiskPageData.mockResolvedValue(pageData())
    render(withQueryClient(<RiskPage runtime={runtime()} />))
    await user.click(await screen.findByRole('tab', { name: 'Live' }))
    await waitFor(() => expect(apiMocks.getRiskPageData).toHaveBeenCalledWith('live'))
    expect(window.location.search).toContain('mode=live')
    expect(screen.getByRole('tablist')).toHaveAttribute('data-mode', 'live')
  })

  it('only enables the kill switch for the running engine mode', async () => {
    apiMocks.getRiskPageData.mockResolvedValue(pageData())
    // Same provider element across rerenders, same underlying QueryClient --
    // rewrapping with a fresh client per rerender would drop the cache and
    // force a spurious refetch between assertions.
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { rerender } = render(
      <QueryClientProvider client={queryClient}><RiskPage runtime={runtime()} /></QueryClientProvider>,
    )
    expect(await screen.findByRole('button', { name: 'Engine Stopped' })).toBeDisabled()

    rerender(<QueryClientProvider client={queryClient}><RiskPage runtime={runtime(true, 'paper')} /></QueryClientProvider>)
    expect(screen.getByRole('button', { name: 'Hold to Stop & Square Off' })).toBeEnabled()

    rerender(<QueryClientProvider client={queryClient}><RiskPage runtime={runtime(true, 'live')} /></QueryClientProvider>)
    expect(screen.getByRole('button', { name: 'Engine Stopped' })).toBeDisabled()
  })
})
