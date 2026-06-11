import { BotMessageSquare, FlaskConical, KeyRound, LockKeyhole, ShieldAlert, Zap } from 'lucide-react'
import { googleLoginUrl } from '../api'

interface Props {
  googleConfigured: boolean
  error: string
}

export function AuthLanding({ googleConfigured, error }: Props) {
  return (
    <main className="auth-landing">
      <section className="auth-hero" aria-label="Layman NOVA login">
        <div className="auth-copy">
          <div className="auth-brand"><span className="nova-mark" /> Layman NOVA</div>
          <h1>The trading bot that talks back.</h1>
          <p>Automate your TradingView Nifty options strategy on Dhan. Start with isolated paper mode, then move to approved live routing when ready.</p>
          {error ? <div className="auth-error">{error}</div> : null}
          <a className={`google-login ${googleConfigured ? '' : 'disabled'}`} href={googleConfigured ? googleLoginUrl() : undefined} aria-disabled={!googleConfigured}>
            <KeyRound size={18} />
            Continue with Google
          </a>
          {!googleConfigured ? <small>Google OAuth is not configured on this backend yet.</small> : null}
        </div>
        <div className="auth-terminal" aria-hidden="true">
          <div className="terminal-top"><span /> <span /> <span /></div>
          <div className="terminal-line"><BotMessageSquare size={16} /> NOVA is listening for TradingView alerts</div>
          <div className="terminal-metric"><span>Mode</span><strong>Paper isolated</strong></div>
          <div className="terminal-metric"><span>Broker</span><strong>Dhan read-only data</strong></div>
          <div className="terminal-metric"><span>Control</span><strong>Stop & square off always visible</strong></div>
        </div>
      </section>

      <section className="auth-steps" aria-label="How Layman NOVA works">
        <div><LockKeyhole size={20} /><strong>Sign in</strong><span>Google login creates your private workspace.</span></div>
        <div><FlaskConical size={20} /><strong>Paper test</strong><span>Wallet, trades, risk and alerts stay user-scoped.</span></div>
        <div><Zap size={20} /><strong>Go live later</strong><span>Live routing stays gated until approval.</span></div>
        <div><ShieldAlert size={20} /><strong>Stay in control</strong><span>Pause, reconfigure, or square off from one screen.</span></div>
      </section>
    </main>
  )
}

