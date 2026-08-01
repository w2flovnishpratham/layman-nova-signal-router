import { Button } from '@/components/ui/button'
import { Loader2, LogIn, ShieldCheck, Zap } from 'lucide-react'
import { MotionPulseText, MotionSpinner } from './MotionPrimitives'

interface Props {
  loading: boolean
  error: string
  onLogin: () => void
  onRetry: () => void
}

// Illustrative only — a static sample of what routed signals look like. This is
// the pre-auth screen, so there is no real user data to show; the feed is
// marked "sample" so it is never mistaken for a live feed.
const SAMPLE_FEED = [
  { time: '13:37:45', tag: 'BUY CE', tone: 'buy', detail: '22950 CE routed' },
  { time: '12:58:31', tag: 'TP HIT', tone: 'buy', detail: '+₹3,105 booked' },
  { time: '11:05:18', tag: 'SL HIT', tone: 'exit', detail: '−₹2,255 · 22800 PE' },
  { time: '10:34:22', tag: 'EXIT PE', tone: 'exit', detail: '22800 PE squared off' },
] as const

export function AuthScreen({ loading, error, onLogin, onRetry }: Props) {
  return (
    <main className="auth-split">
      {/* Left: brand and an illustrative signal feed. Decorative marketing. */}
      <section className="auth-hero" aria-label="About NOVA Signal Router">
        <div className="auth-hero-brand">
          <span className="auth-hero-mark"><Zap size={18} /></span>
          <span><strong>NOVA</strong> Signal Router</span>
        </div>

        <figure className="auth-feed" aria-label="Illustrative sample of routed signals">
          <figcaption className="auth-feed-head">
            <span><span className="auth-feed-dot" /> SIGNAL FEED</span>
            <span className="auth-feed-sample">SAMPLE · IST</span>
          </figcaption>
          <ul>
            {SAMPLE_FEED.map((row) => (
              <li key={row.time}>
                <span className="auth-feed-time">{row.time}</span>
                <span className={`auth-feed-tag is-${row.tone}`}>{row.tag}</span>
                <span className="auth-feed-detail">{row.detail}</span>
              </li>
            ))}
          </ul>
        </figure>

        <p className="auth-hero-pill"><span className="auth-hero-pill-dot" /> Automated NIFTY options routing · via Dhan</p>
        <h1 className="auth-hero-title">From TradingView alert to live order — in <em>milliseconds.</em></h1>
        <p className="auth-hero-copy">
          NOVA receives your strategy&apos;s webhooks, runs every hard risk check, and routes CE/PE entries to your
          broker — paper or live. You stay in control with a kill switch that&apos;s never more than one click away.
        </p>
      </section>

      {/* Right: the actual sign-in. */}
      <section className="auth-panel" aria-live="polite">
        <div className="auth-panel-inner">
          <div className="auth-icon"><ShieldCheck size={26} /></div>
          <h2>{loading ? 'Checking your session' : 'Sign in to continue'}</h2>
          <p className="auth-copy">
            Use any verified Google account. Your session is stored server-side — the browser only ever holds a secure cookie.
          </p>

          {error ? <div className="error-banner" role="alert">{error}</div> : null}

          {loading ? (
            <div className="flex flex-col items-center gap-3 mt-4">
              <MotionSpinner className="text-[#2F6BED]"><Loader2 size={30} /></MotionSpinner>
              <MotionPulseText className="text-xs text-white/50 font-medium">Verifying login, please wait…</MotionPulseText>
            </div>
          ) : (
            <div className="auth-actions">
              <Button variant="unstyled" className="google-login-button" type="button" onClick={onLogin}>
                <LogIn size={17} /> Continue with Google
              </Button>
              {error ? <Button variant="unstyled" className="secondary-button" type="button" onClick={onRetry}>Retry</Button> : null}
            </div>
          )}

          <p className="auth-reverify"><ShieldCheck size={14} /> New device sign-ins require broker re-verification.</p>
          <p className="auth-terms">
            By continuing you agree to the <a href="/terms">Terms</a> &amp; <a href="/risk">Risk Disclosure</a>.
            Trading in derivatives carries risk of loss.
          </p>
        </div>
        <p className="auth-footer">© 2026 NOVA SIGNAL ROUTER · SECURED WITH GOOGLE OAUTH</p>
      </section>
    </main>
  )
}
