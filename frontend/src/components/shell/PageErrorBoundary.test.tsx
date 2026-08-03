import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PageErrorBoundary } from './PageErrorBoundary'

function Boom(): never {
  throw new Error('page exploded')
}

afterEach(cleanup)

describe('PageErrorBoundary', () => {
  it('shows a retry card instead of letting a page crash bubble up', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <div>
        <nav>Sidebar survives</nav>
        <PageErrorBoundary resetKey="reports"><Boom /></PageErrorBoundary>
      </div>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent(/hit an error/i)
    // The shell outside the boundary is untouched.
    expect(screen.getByText('Sidebar survives')).toBeInTheDocument()
    spy.mockRestore()
  })

  it('recovers when the route changes', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { rerender } = render(
      <PageErrorBoundary resetKey="reports"><Boom /></PageErrorBoundary>,
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
    // Navigating to a healthy page clears the error.
    rerender(<PageErrorBoundary resetKey="settings"><div>Healthy page</div></PageErrorBoundary>)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText('Healthy page')).toBeInTheDocument()
    spy.mockRestore()
  })

  it('renders children unchanged when nothing throws', () => {
    render(<PageErrorBoundary resetKey="risk"><div>All good</div></PageErrorBoundary>)
    expect(screen.getByText('All good')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
