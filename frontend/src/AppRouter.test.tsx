import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRouter } from './AppRouter'
import { navigate } from './navigation'

vi.mock('./App', () => ({ default: () => <div data-testid="app-view">APP</div> }))
vi.mock('./landing/LandingPage', () => ({
  LandingPage: ({ onEnterApp }: { onEnterApp?: () => void }) => (
    <div data-testid="landing-view">
      <button onClick={onEnterApp}>enter app</button>
    </div>
  ),
}))

function setPath(p: string) {
  window.history.pushState({}, '', p)
}

afterEach(() => {
  cleanup()
  setPath('/')
})

describe('AppRouter route boundary', () => {
  it('serves the public landing page at /', () => {
    setPath('/')
    render(<AppRouter />)
    expect(screen.getByTestId('landing-view')).toBeInTheDocument()
    expect(screen.queryByTestId('app-view')).toBeNull()
  })

  it('serves the authenticated app at /app', () => {
    setPath('/app')
    render(<AppRouter />)
    expect(screen.getByTestId('app-view')).toBeInTheDocument()
    expect(screen.queryByTestId('landing-view')).toBeNull()
  })

  it('serves the app on a nested /app/* path (deep refresh works)', () => {
    setPath('/app/strategies')
    render(<AppRouter />)
    expect(screen.getByTestId('app-view')).toBeInTheDocument()
  })

  it('landing CTA navigates into the app without a full reload', () => {
    setPath('/')
    render(<AppRouter />)
    fireEvent.click(screen.getByText('enter app'))
    // /app canonicalises to the default authenticated route.
    expect(window.location.pathname).toBe('/app/trading')
    expect(screen.getByTestId('app-view')).toBeInTheDocument()
  })

  it('redirects bare /app to the default route, and an unknown /app/* resolves safely', () => {
    setPath('/app')
    render(<AppRouter />)
    expect(window.location.pathname).toBe('/app/trading')
    expect(screen.getByTestId('app-view')).toBeInTheDocument()
    act(() => { window.history.pushState({}, '', '/app/does-not-exist'); window.dispatchEvent(new PopStateEvent('popstate')) })
    expect(window.location.pathname).toBe('/app/trading')
    expect(screen.getByTestId('app-view')).toBeInTheDocument()
  })

  it('returns to the correct view on simulated Back/Forward without duplicating', () => {
    setPath('/')
    render(<AppRouter />)
    act(() => navigate('/app'))
    expect(screen.getAllByTestId('app-view')).toHaveLength(1)
    // Back: browser restores the previous path and fires popstate.
    act(() => { window.history.pushState({}, '', '/'); window.dispatchEvent(new PopStateEvent('popstate')) })
    expect(screen.getAllByTestId('landing-view')).toHaveLength(1)
    expect(screen.queryByTestId('app-view')).toBeNull()
    // Forward again — still exactly one app view, no duplicate transcript/view.
    act(() => { window.history.pushState({}, '', '/app'); window.dispatchEvent(new PopStateEvent('popstate')) })
    expect(screen.getAllByTestId('app-view')).toHaveLength(1)
  })

  it('navigate() updates history and re-renders via popstate', () => {
    setPath('/')
    render(<AppRouter />)
    act(() => navigate('/app/dashboard'))
    expect(window.location.pathname).toBe('/app/dashboard')
    expect(screen.getByTestId('app-view')).toBeInTheDocument()
    act(() => window.history.back())
  })
})
