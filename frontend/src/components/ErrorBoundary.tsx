/**
 * FE-C5 — Global React error boundary.
 *
 * Without this, any thrown error during render anywhere in the page tree
 * crashes the whole app to a blank white screen. For a real-money trading
 * UI that's unacceptable — operator loses ability to see positions, fire
 * emergency stop, or navigate to recover.
 *
 * This boundary catches render-phase errors, logs them to the console with
 * full stack, and shows a friendly recovery screen with a Reload button.
 * It does NOT catch errors thrown inside event handlers or async code —
 * those still need explicit .catch() blocks (handled separately).
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertOctagon, RefreshCw } from 'lucide-react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
  errorInfo: ErrorInfo | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, errorInfo: null }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    // Log for debugging. In future this is where we'd ship to Sentry / Rollbar.
    console.error('ErrorBoundary caught:', error, errorInfo)
    this.setState({ errorInfo })
  }

  handleReload = () => {
    // Hard reload — clears React state, refetches everything.
    window.location.reload()
  }

  handleGoHome = () => {
    window.location.href = '/app/dashboard'
  }

  render() {
    if (!this.state.hasError) return this.props.children

    const message = this.state.error?.message || 'Unknown error'
    const stack = this.state.error?.stack || ''
    // Show only the top of the stack to keep the UI clean. Full stack is in console.
    const shortStack = stack.split('\n').slice(0, 5).join('\n')

    return (
      <div
        style={{ background: '#0f0f0f', color: '#f0f0f0', minHeight: '100vh' }}
        className="flex items-center justify-center p-6"
      >
        <div
          className="w-full max-w-lg rounded-2xl p-8"
          style={{ background: '#151513', border: '1px solid #2b2a26' }}
        >
          <div className="flex items-start gap-4">
            <div
              className="p-3 rounded-xl flex-shrink-0"
              style={{
                background: 'rgba(239,68,68,0.08)',
                border: '1px solid rgba(239,68,68,0.25)',
                color: '#f87171',
              }}
            >
              <AlertOctagon size={22} />
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="text-lg font-bold" style={{ color: '#f4f1ea' }}>
                Something went wrong
              </h1>
              <p className="text-sm mt-2" style={{ color: '#9a968f' }}>
                NOVA hit an unexpected error and stopped rendering this view.
                Your positions and orders on Dhan are unaffected — this is a UI
                problem only.
              </p>
              <div
                className="mt-4 p-3 rounded-lg text-xs font-mono overflow-x-auto"
                style={{ background: '#0a0a0a', border: '1px solid #2b2a26', color: '#9a968f' }}
              >
                <div style={{ color: '#f87171' }}>{message}</div>
                {shortStack && (
                  <pre className="mt-2 whitespace-pre-wrap" style={{ color: '#666' }}>
                    {shortStack}
                  </pre>
                )}
              </div>
              <p className="text-xs mt-3" style={{ color: '#666' }}>
                Full details have been logged to the browser console.
              </p>
              <div className="flex gap-3 mt-6">
                <button
                  type="button"
                  onClick={this.handleReload}
                  className="px-4 py-2 text-xs font-bold rounded-full flex items-center gap-2 transition-all"
                  style={{ background: '#98e94d', color: '#000' }}
                >
                  <RefreshCw size={13} />
                  Reload page
                </button>
                <button
                  type="button"
                  onClick={this.handleGoHome}
                  className="px-4 py-2 text-xs font-semibold rounded-full transition-all"
                  style={{
                    background: '#141412',
                    border: '1px solid #2b2a26',
                    color: '#d8d3c8',
                  }}
                >
                  Go to Dashboard
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    )
  }
}
