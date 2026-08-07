import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PortfolioDashboard } from './PortfolioDashboard'
import { dashboardPreviewBundle, dashboardPreviewHealth, dashboardPreviewRuntime } from './previewData'
import { withQueryClient } from '../test/testQueryClient'

function renderDashboard(overrides: { onKill?: () => void } = {}) {
  render(withQueryClient(
    <PortfolioDashboard
      runtime={dashboardPreviewRuntime}
      health={dashboardPreviewHealth}
      preview={dashboardPreviewBundle}
      onKill={overrides.onKill ?? vi.fn()}
      onManageStrategies={vi.fn()}
      onViewHealth={vi.fn()}
    />,
  ))
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('PortfolioDashboard', () => {
  it('renders the reference dashboard structure and switches equity range', async () => {
    const user = userEvent.setup()
    renderDashboard()

    expect(screen.getByRole('heading', { name: 'Portfolio Dashboard' })).toBeInTheDocument()
    expect(screen.getByText('Strategy Performance')).toBeInTheDocument()
    expect(screen.getByText('System Health')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Export Report/i })).toHaveAttribute('href', expect.stringContaining('/api/reports/export.csv'))

    const oneWeek = screen.getByRole('button', { name: '1W' })
    await user.click(oneWeek)
    expect(oneWeek).toHaveClass('active')
  })

  it('requires the full hold duration before invoking square off', () => {
    vi.useFakeTimers()
    const onKill = vi.fn()
    renderDashboard({ onKill })
    const button = screen.getByRole('button', { name: 'Hold to Stop & Square Off' })

    fireEvent.pointerDown(button)
    act(() => vi.advanceTimersByTime(799))
    expect(onKill).not.toHaveBeenCalled()

    act(() => vi.advanceTimersByTime(1))
    expect(onKill).toHaveBeenCalledTimes(1)
  })
})
