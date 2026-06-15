import { LogIn, ShieldCheck } from 'lucide-react'

interface Props {
  loading: boolean
  error: string
  onLogin: () => void
  onRetry: () => void
}

export function AuthScreen({ loading, error, onLogin, onRetry }: Props) {
  return (
    <main className="auth-shell">
      <section className="auth-card" aria-live="polite">
        <div className="auth-brand">
          <span className="nova-mark" />
          <strong>NOVA SIGNAL ROUTER</strong>
        </div>
        <div className="auth-icon"><ShieldCheck size={28} /></div>
        <p className="eyebrow">Secure trading workspace</p>
        <h1>{loading ? 'Checking your session' : 'Sign in to continue'}</h1>
        <p className="auth-copy">
          Use any verified Google account. Your session is stored server-side and the browser receives only a secure cookie.
        </p>
        {error ? <div className="error-banner">{error}</div> : null}
        {loading ? (
          <div className="system-chip">Verifying login</div>
        ) : (
          <div className="auth-actions">
            <button className="google-login-button" type="button" onClick={onLogin}>
              <LogIn size={17} />
              Continue with Google
            </button>
            {error ? (
              <button className="secondary-button" type="button" onClick={onRetry}>Retry</button>
            ) : null}
          </div>
        )}
        <p className="auth-footnote">Real order routing remains subject to server safety checks.</p>
      </section>
    </main>
  )
}
