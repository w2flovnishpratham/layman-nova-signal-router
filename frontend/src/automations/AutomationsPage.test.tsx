import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({ getAutomations: vi.fn(), saveAutomations: vi.fn() }))
vi.mock('./automationsApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./automationsApi')>(),
  ...apiMocks,
}))

import { AutomationsPage } from './AutomationsPage'

const overview = (over: Record<string, unknown> = {}) => ({
  ok: true,
  storage: 'runtime_settings',
  editable: [
    {
      key: 'cooldown_after_loss_minutes', label: 'Cooldown after a losing trade', value: 30,
      unit: 'minutes', minimum: 0, maximum: 390, zero_means: 'no cooldown',
      basis: 'Measured from the exit that booked a negative realised P&L.',
      effect: 'Applies to next entry', requires_restart: false, affects_open_position: false,
    },
    {
      key: 'max_daily_loss', label: 'Daily loss cap', value: 25000,
      unit: 'rupees', minimum: 0, maximum: 10000000, zero_means: 'no cap',
      basis: 'Compared against the session P&L; hitting it hard-stops and squares off.',
      effect: 'Applies immediately', requires_restart: false, affects_open_position: true,
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

afterEach(() => { cleanup(); apiMocks.getAutomations.mockReset(); apiMocks.saveAutomations.mockReset() })

describe('AutomationsPage', () => {
  it('offers no way to disable a protected rule', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    render(<AutomationsPage />)
    const section = within(await screen.findByRole('region', { name: 'Protected rules' }))
    expect(section.getByText('Duplicate-exit protection')).toBeInTheDocument()
    expect(section.getByText('Confirmed-flat handling')).toBeInTheDocument()
    // No control of any kind inside the protected section.
    expect(section.queryByRole('checkbox')).toBeNull()
    expect(section.queryByRole('button')).toBeNull()
    expect(section.queryByRole('textbox')).toBeNull()
  })

  it('labels when each editable rule takes effect', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    render(<AutomationsPage />)
    expect(await screen.findByText('Applies to next entry')).toBeInTheDocument()
    expect(screen.getByText('Applies immediately')).toBeInTheDocument()
    expect(screen.getAllByText('Protected system rule')).toHaveLength(2)
  })

  it('says whether a rule can act on the position that is open now', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    render(<AutomationsPage />)
    expect(await screen.findByText(/can act on the position that is open now/i)).toBeInTheDocument()
    expect(screen.getByText(/does not touch the position that is open now/i)).toBeInTheDocument()
  })

  it('saves a cooldown change', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    apiMocks.saveAutomations.mockResolvedValue(overview())
    render(<AutomationsPage />)

    fireEvent.change(await screen.findByLabelText('Cooldown after a losing trade'), { target: { value: '45' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[0])
    await waitFor(() => expect(apiMocks.saveAutomations).toHaveBeenCalledWith({ cooldown_after_loss_minutes: 45 }))
  })

  it('saves a daily limit change', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    apiMocks.saveAutomations.mockResolvedValue(overview())
    render(<AutomationsPage />)

    fireEvent.change(await screen.findByLabelText('Daily loss cap'), { target: { value: '10000' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[1])
    await waitFor(() => expect(apiMocks.saveAutomations).toHaveBeenCalledWith({ max_daily_loss: 10000 }))
  })

  it('surfaces a rejected out-of-bounds value from the server', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    apiMocks.saveAutomations.mockRejectedValue(new Error('Cooldown after a losing trade must be between 0 and 390 minutes.'))
    render(<AutomationsPage />)

    fireEvent.change(await screen.findByLabelText('Cooldown after a losing trade'), { target: { value: '5000' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[0])
    expect(await screen.findByRole('alert')).toHaveTextContent(/between 0 and 390 minutes/i)
  })

  it('states that no separate rules engine backs these controls', async () => {
    apiMocks.getAutomations.mockResolvedValue(overview())
    render(<AutomationsPage />)
    expect(await screen.findByText(/there is no separate rules engine/i)).toBeInTheDocument()
  })
})
