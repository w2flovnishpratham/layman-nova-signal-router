import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NovaSidebar } from './NovaSidebar'
import { PlaceholderPage } from './PlaceholderPage'

afterEach(() => cleanup())

function sidebar(over: Partial<Parameters<typeof NovaSidebar>[0]> = {}) {
  return (
    <NovaSidebar
      route="trading"
      open={false}
      collapsed={false}
      onNavigate={vi.fn()}
      onToggleCollapsed={vi.fn()}
      onClose={vi.fn()}
      {...over}
    />
  )
}

describe('NovaSidebar', () => {
  it('renders the authenticated navigation as a semantic nav with all sections', () => {
    render(sidebar())
    expect(screen.getByRole('navigation', { name: 'Application' })).toBeInTheDocument()
    for (const label of ['Dashboard', 'Trading', 'Strategies', 'Signals', 'Automations', 'Webhooks', 'Credentials', 'Risk', 'Reports', 'Settings']) {
      expect(screen.getByRole('button', { name: new RegExp(`^${label}`) })).toBeInTheDocument()
    }
  })

  it('marks the active route with aria-current (not colour alone)', () => {
    render(sidebar({ route: 'dashboard' }))
    expect(screen.getByRole('button', { name: /^Dashboard/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('button', { name: /^Trading/ })).not.toHaveAttribute('aria-current')
  })

  it('treats strategies/new as within Strategies', () => {
    render(sidebar({ route: 'strategies/new' }))
    expect(screen.getByRole('button', { name: /^Strategies/ })).toHaveAttribute('aria-current', 'page')
  })

  it('labels not-yet-built pages truthfully rather than faking them', () => {
    render(sidebar())
    // Implemented pages carry no "Soon" marker; unbuilt ones do.
    expect(screen.getByRole('button', { name: /^Signals/ })).toHaveTextContent('Soon')
    expect(screen.getByRole('button', { name: /^Trading/ })).not.toHaveTextContent('Soon')
  })

  it('navigates by keyboard', async () => {
    const onNavigate = vi.fn()
    const user = userEvent.setup()
    render(sidebar({ onNavigate }))
    const reports = screen.getByRole('button', { name: /^Reports/ })
    reports.focus()
    await user.keyboard('{Enter}')
    expect(onNavigate).toHaveBeenCalledWith('reports')
  })

  it('exposes an accessible collapse control', async () => {
    const onToggleCollapsed = vi.fn()
    const user = userEvent.setup()
    render(sidebar({ onToggleCollapsed }))
    await user.click(screen.getByRole('button', { name: /collapse navigation/i }))
    expect(onToggleCollapsed).toHaveBeenCalled()
  })
})

describe('PlaceholderPage', () => {
  it('states the page is not built yet and shows no fabricated metrics', () => {
    const { container } = render(<PlaceholderPage route="signals" />)
    expect(screen.getByText('Coming in the next build')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Signals' })).toBeInTheDocument()
    // No fake currency/percentage/latency figures anywhere on a placeholder.
    expect(container.textContent).not.toMatch(/[₹$]\s?\d/)
    expect(container.textContent).not.toMatch(/\d+\s?(ms|%)/)
  })

  it('describes which existing authoritative data the page will use', () => {
    render(<PlaceholderPage route="risk" />)
    expect(screen.getByText(/limits the backend already enforces/i)).toBeInTheDocument()
  })
})
