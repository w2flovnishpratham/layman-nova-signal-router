import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({ getAutomations: vi.fn(), saveAutomations: vi.fn() }))
const toastApi = vi.hoisted(() => ({ add: vi.fn() }))
vi.mock('./automationsApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./automationsApi')>(),
  ...apiMocks,
}))
vi.mock('@/components/ui/toast', () => ({ toast: toastApi }))

import { AutomationsPage } from './AutomationsPage'

const overview = (over: Record<string, unknown> = {}) => ({
  ok: true,
  storage: 'configuration_revision',
  configuration_id: 'config-1',
  configuration_revision: 4,
  mode: 'paper',
  editable: [
    {
      key: 'max_trades_per_day', label: 'Maximum trades per day', value: 3,
      unit: 'entries', minimum: 0, maximum: 50, zero_means: 'no cap',
      basis: 'Counts entries recorded for the current IST trading day.',
      effect: 'Applies to next entry', requires_restart: false, affects_open_position: false,
    },
    {
      key: 'max_daily_loss', label: 'Daily loss cap', value: 25000,
      unit: 'rupees', minimum: 0, maximum: 10000000, zero_means: 'no cap',
      basis: 'Compared against session P&L.',
      effect: 'Applies to next entry', requires_restart: false, affects_open_position: false,
    },
  ],
  protected: [
    {
      key: 'duplicate_exit_protection', label: 'Duplicate-exit protection',
      description: 'Exits are claimed as durable operations.',
      effect: 'Protected system rule',
      why_protected: 'A duplicate exit books phantom profit.',
    },
    {
      key: 'confirmed_flat_handling', label: 'Confirmed-flat handling',
      description: 'A confirmed-flat position is tombstoned.',
      effect: 'Protected system rule',
      why_protected: 'Position resurrection caused the incident this safeguard exists for.',
    },
  ],
  ...over,
})

afterEach(() => {
  cleanup()
  apiMocks.getAutomations.mockReset()
  apiMocks.saveAutomations.mockReset()
})

describe('AutomationsPage', () => {
  it('offers no way to disable a protected rule', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    render(<AutomationsPage />)
    const section = within(await screen.findByRole('region', { name: 'Protected rules' }))
    expect(section.getByText('Duplicate-exit protection')).toBeInTheDocument()
    expect(section.queryByRole('checkbox')).toBeNull()
    expect(section.queryByRole('button')).toBeNull()
    expect(section.queryByRole('textbox')).toBeNull()
  })

  it('labels every editable rule as future-entry only', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    render(<AutomationsPage />)
    expect(await screen.findAllByText('Applies to next entry')).toHaveLength(2)
    expect(screen.getAllByText(/does not touch the position that is open now/i)).toHaveLength(2)
  })

  it('reviews multiple draft changes and confirms exactly one revision', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    apiMocks.saveAutomations.mockResolvedValue(overview({
      configuration_id: 'config-2',
      configuration_revision: 5,
      editable: [
        { ...overview().editable[0], value: 6 },
        { ...overview().editable[1], value: 10000 },
      ],
    }))
    render(<AutomationsPage />)

    fireEvent.change(await screen.findByLabelText('Maximum trades per day'), { target: { value: '6' } })
    fireEvent.change(screen.getByLabelText('Daily loss cap'), { target: { value: '10000' } })
    expect(apiMocks.saveAutomations).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Review Changes' }))
    const review = screen.getByRole('region', { name: 'Review automation changes' })
    expect(within(review).getByText(/revision 4/i)).toBeInTheDocument()
    expect(within(review).getByText('25000 → 10000')).toBeInTheDocument()
    fireEvent.click(within(review).getByRole('button', { name: 'Confirm Update' }))

    await waitFor(() => expect(apiMocks.saveAutomations).toHaveBeenCalledTimes(1))
    expect(apiMocks.saveAutomations).toHaveBeenCalledWith(
      'config-1',
      4,
      { max_trades_per_day: 6, max_daily_loss: 10000 },
    )
  })

  it('canceling review persists nothing', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    render(<AutomationsPage />)
    fireEvent.change(await screen.findByLabelText('Daily loss cap'), { target: { value: '10000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review Changes' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Review automation changes' })).toBeNull()
    expect(apiMocks.saveAutomations).not.toHaveBeenCalled()
  })

  it('surfaces a stale confirmation conflict', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    apiMocks.saveAutomations.mockRejectedValue(new Error('This configuration was changed elsewhere.'))
    render(<AutomationsPage />)
    fireEvent.change(await screen.findByLabelText('Daily loss cap'), { target: { value: '10000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review Changes' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Update' }))
    await waitFor(() => expect(toastApi.add).toHaveBeenCalledWith(expect.objectContaining({
      title: expect.stringMatching(/changed elsewhere/i),
      type: 'error',
    })))
  })
})
