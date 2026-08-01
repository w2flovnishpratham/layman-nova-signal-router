import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getPreferences: vi.fn(),
  getCredentialsSummary: vi.fn(),
  savePreferences: vi.fn(),
  resetPaperSession: vi.fn(),
}))
const toastApi = vi.hoisted(() => ({ add: vi.fn() }))
vi.mock('./settingsApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./settingsApi')>(),
  ...apiMocks,
}))
vi.mock('@/components/ui/toast', () => ({ toast: toastApi }))

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
    { key: 'entry_exit', label: 'Entry and exit fills', available: true, reason: 'Delivered by this browser when notification permission is granted.' },
  ],
  ...over,
})

afterEach(() => {
  cleanup()
  apiMocks.getPreferences.mockReset()
  apiMocks.getCredentialsSummary.mockReset()
  apiMocks.savePreferences.mockReset()
  apiMocks.resetPaperSession.mockReset()
})

beforeEach(() => {
  apiMocks.getCredentialsSummary.mockResolvedValue(null)
})

describe('SettingsPage', () => {
  it('saves a changed preference and shows the stored value', async () => {
    const user = userEvent.setup()
    apiMocks.getPreferences.mockResolvedValue(prefs())
    apiMocks.savePreferences.mockResolvedValue(prefs({ table_density: 'compact', revision: 2 }))
    render(<SettingsPage />)

    await user.click(await screen.findByRole('combobox', { name: 'Table density' }))
    await user.click(screen.getByRole('option', { name: 'compact' }))
    await waitFor(() => expect(apiMocks.savePreferences).toHaveBeenCalledWith({ table_density: 'compact' }))
    expect(toastApi.add).toHaveBeenCalledWith(expect.objectContaining({ title: 'Settings saved.', type: 'success' }))
    expect(screen.getByLabelText('Table density')).toHaveTextContent('compact')
  })

  it('restores stored preferences on load', async () => {
    apiMocks.getPreferences.mockResolvedValue(prefs({ reduced_motion: true, default_chart_timeframe: '15m' }))
    render(<SettingsPage />)
    expect(await screen.findByLabelText('Reduced motion')).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByLabelText('Default chart timeframe')).toHaveTextContent('15m')
  })

  it('describes browser-controlled notification delivery truthfully', async () => {
    apiMocks.getPreferences.mockResolvedValue(prefs())
    render(<SettingsPage />)
    expect(await screen.findByText(/delivered by this browser/i)).toBeInTheDocument()
    expect(screen.getByText(/browser permission:/i)).toBeInTheDocument()
  })

  it('requires confirmation before resetting Paper', async () => {
    apiMocks.getPreferences.mockResolvedValue(prefs())
    apiMocks.resetPaperSession.mockResolvedValue(undefined)
    render(<SettingsPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'Reset Paper account' }))
    expect(apiMocks.resetPaperSession).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /yes, reset paper/i }))
    await waitFor(() => expect(apiMocks.resetPaperSession).toHaveBeenCalledTimes(1))
    expect(toastApi.add).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Paper session reset.',
      description: 'A final snapshot was recorded first.',
      type: 'success',
    }))
  })

  it('surfaces the server refusal when Paper cannot be reset', async () => {
    apiMocks.getPreferences.mockResolvedValue(prefs())
    apiMocks.resetPaperSession.mockRejectedValue(new Error('Square off the Paper position before resetting Paper.'))
    render(<SettingsPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'Reset Paper account' }))
    fireEvent.click(screen.getByRole('button', { name: /yes, reset paper/i }))
    await waitFor(() => expect(toastApi.add).toHaveBeenCalledWith(expect.objectContaining({
      title: expect.stringMatching(/square off the paper position/i),
      type: 'error',
    })))
  })

  it('offers no account deletion or unsupported control', async () => {
    apiMocks.getPreferences.mockResolvedValue(prefs())
    render(<SettingsPage />)
    await screen.findByText('Display Preferences')
    expect(screen.queryByRole('button', { name: /delete account/i })).toBeNull()
    expect(screen.queryByText(/two-factor|api key|billing/i)).toBeNull()
  })
})
