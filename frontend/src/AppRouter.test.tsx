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

  it('landing CTA navigates to /app without a full reload', () => {
    setPath('/')
    render(<AppRouter />)
    fireEvent.click(screen.getByText('enter app'))
    expect(window.location.pathname).toBe('/app')
    expect(screen.getByTestId('app-view')).toBeInTheDocument()
  })

  it('navigate() updates history and re-renders via popstate', () => {
    setPath('/')
    render(<AppRouter />)
    act(() => navigate('/app'))
    expect(window.location.pathname).toBe('/app')
    expect(screen.getByTestId('app-view')).toBeInTheDocument()
    act(() => window.history.back())
  })
})
