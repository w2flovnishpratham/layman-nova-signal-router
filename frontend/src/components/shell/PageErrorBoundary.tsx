import { Button } from '@/components/ui/button'
import { AlertTriangle } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  /** Changing this resets the boundary — used to clear the error on navigation. */
  resetKey: string
  children: ReactNode
}

interface State {
  error: Error | null
}

/** Catches a render/runtime error in the routed page so a single page failure
    shows a retry card instead of blanking the whole authenticated shell. The
    sidebar and header live outside this boundary and keep working. */
export class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidUpdate(prev: Props) {
    // A new route clears a stale error so navigation always recovers.
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null })
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Bounded, non-sensitive: never log request payloads or state here.
    console.error('Page render failed:', error.message, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="nova-page-error" role="alert">
          <AlertTriangle size={20} />
          <h2>This page hit an error</h2>
          <p>The rest of the app is still working. Try again, or switch to another page from the sidebar.</p>
          <Button variant="unstyled" type="button" className="conv-pill conv-pill--primary" onClick={() => this.setState({ error: null })}>
            Try again
          </Button>
        </div>
      )
    }
    return this.props.children
  }
}
