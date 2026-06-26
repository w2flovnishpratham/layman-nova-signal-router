import { LogIn, ShieldCheck, Loader2 } from 'lucide-react'
import { MotionPulseText, MotionSpinner } from './MotionPrimitives'

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
          <div className="flex flex-col items-center gap-3 mt-4">
            <MotionSpinner className="text-purple-400">
              <Loader2 size={32} />
            </MotionSpinner>
            <MotionPulseText className="text-xs text-white/50 font-medium">Verifying login, please wait...</MotionPulseText>
          </div>
        ) : (
          <div className="auth-actions">
            <button className="google-login-button" type="button" onClick={onLogin}>
              <LogIn size={17} />
              Continue with Google address
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
