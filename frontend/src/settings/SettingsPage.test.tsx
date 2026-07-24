import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getPreferences: vi.fn(),
  savePreferences: vi.fn(),
  resetPaperSession: vi.fn(),
}))
vi.mock('./settingsApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./settingsApi')>(),
  ...apiMocks,
}))

import { SettingsPage } from './SettingsPage'

const prefs = (over: Record<string, unknown> = {}) => ({
  timezone: 'Asia/Kolkata',
  reduced_motion: false,
  table_density: 'comfortable',
  default_chart_timeframe: '5m',
  notification_preferences: {},
  revision: 1,
  stored: true,
  channels: [
    { key: 'entry_exit', label: 'Entry and exit fills', available: false, reason: 'No delivery channel is configured yet; this preference is stored only.' },
  ],
  ...over,
})

afterEach(() => {
  cleanup()
  apiMocks.getPreferences.mockReset()
  apiMocks.savePreferences.mockReset()
  apiMocks.resetPaperSession.mockReset()
})

describe('SettingsPage', () => {
  it('saves a changed preference and shows the stored value', async () => {
    apiMocks.getPreferences.mockResolvedValue(prefs())
    apiMocks.savePreferences.mockResolvedValue(prefs({ table_density: 'compact', revision: 2 }))
    render(<SettingsPage />)

    fireEvent.change(await screen.findByLabelText('Table density'), { target: { value: 'compact' } })
    await waitFor(() => expect(apiMocks.savePreferences).toHaveBeenCalledWith({ table_density: 'compact' }))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    expect((screen.getByLabelText('Table density') as HTMLSelectElement).value).toBe('compact')
  })

  it('restores stored preferences on load', async () => {
    apiMocks.getPreferences.mockResolvedValue(prefs({ reduced_motion: true, default_chart_timeframe: '15m' }))
    render(<SettingsPage />)
    expect((await screen.findByLabelText('Reduced motion') as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText('Default chart timeframe') as HTMLSelectElement).value).toBe('15m')
  })

  it('admits that notification channels deliver nothing yet', async () => {
    apiMocks.getPreferences.mockResolvedValue(prefs())
    render(<SettingsPage />)
    expect(await screen.findByText(/no delivery channel is configured yet/i)).toBeInTheDocument()
  })

  it('requires confirmation before resetting Paper', async () => {
    apiMocks.getPreferences.mockResolvedValue(prefs())
    apiMocks.resetPaperSession.mockResolvedValue(undefined)
    render(<SettingsPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'Reset Paper account' }))
    expect(apiMocks.resetPaperSession).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /yes, reset paper/i }))
    await waitFor(() => expect(apiMocks.resetPaperSession).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/final snapshot was recorded first/i)).toBeInTheDocument()
  })

  it('surfaces the server refusal when Paper cannot be reset', async () => {
    apiMocks.getPreferences.mockResolvedValue(prefs())
    apiMocks.resetPaperSession.mockRejectedValue(new Error('Square off the Paper position before resetting Paper.'))
    render(<SettingsPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'Reset Paper account' }))
    fireEvent.click(screen.getByRole('button', { name: /yes, reset paper/i }))
    expect(await screen.findByText(/square off the paper position/i)).toBeInTheDocument()
  })

  it('offers no account deletion or unsupported control', async () => {
    apiMocks.getPreferences.mockResolvedValue(prefs())
    render(<SettingsPage />)
    await screen.findByText('Display')
    expect(screen.queryByRole('button', { name: /delete account/i })).toBeNull()
    expect(screen.queryByText(/two-factor|api key|billing/i)).toBeNull()
  })
})
