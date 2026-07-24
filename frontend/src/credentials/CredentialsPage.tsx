import { AlertTriangle, Check, Loader2, ShieldCheck, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import {
  disconnectBroker,
  getCredentialsOverview,
  verifyBroker,
  type CredentialsOverview,
} from './credentialsApi'

function Fact({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="nova-cred-fact">
      <span>{label}</span>
      <strong>
        {ok === undefined ? null : ok ? <Check size={13} /> : <X size={13} />}
        {value}
      </strong>
    </div>
  )
}

export function CredentialsPage() {
  const [data, setData] = useState<CredentialsOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [verifying, setVerifying] = useState(false)
  const [verifyResult, setVerifyResult] = useState<{ success: boolean; message: string } | null>(null)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await getCredentialsOverview())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load credentials.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  async function runVerify() {
    setVerifying(true)
    setVerifyResult(null)
    try {
      setVerifyResult(await verifyBroker())
    } catch (e) {
      setVerifyResult({ success: false, message: e instanceof Error ? e.message : 'Verification failed.' })
    } finally {
      setVerifying(false)
    }
  }

  async function runDisconnect() {
    setConfirmDisconnect(false)
    try {
      await disconnectBroker()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Disconnect failed.')
    }
  }

  return (
    <div className="nova-signals">
      <header className="nova-signals-head">
        <div>
          <h1>Credentials</h1>
          <p>
            The broker account NOVA places orders through. Stored secrets cannot be
            revealed here — only whether each one is present.
          </p>
        </div>
      </header>

      {loading ? (
        <p className="nova-signals-state" role="status"><Loader2 size={16} /> Loading credentials…</p>
      ) : error ? (
        <p className="nova-signals-state" role="alert">
          <AlertTriangle size={16} /> {error}
          <button type="button" className="conv-pill" onClick={() => void load()}>Retry</button>
        </p>
      ) : !data ? null : (
        <>
          <section className="nova-hooks-card" aria-label="Dhan">
            <div className="nova-hooks-card-head">
              <ShieldCheck size={15} />
              <strong>{data.broker.name}</strong>
              <span className={data.broker.connected ? 'nova-sig-ok' : 'nova-sig-bad'}>
                {data.broker.connected ? 'connected' : 'not connected'}
              </span>
            </div>

            <div className="nova-cred-grid">
              <Fact label="Client ID" value={data.broker.client_id_masked ?? 'Not set'} ok={data.broker.has_client_id} />
              <Fact label="Access token" value={data.broker.has_access_token ? 'Stored' : 'Not set'} ok={data.broker.has_access_token} />
              <Fact label="API secret" value={data.broker.has_api_secret ? 'Stored' : 'Not set'} ok={data.broker.has_api_secret} />
              <Fact label="Webhook secret" value={data.broker.has_webhook_secret ? 'Stored' : 'Not set'} ok={data.broker.has_webhook_secret} />
              <Fact
                label="Token saved"
                value={data.broker.token_saved_at ? new Date(data.broker.token_saved_at).toLocaleString() : 'Never'}
              />
              <Fact
                label="Token expiry"
                // The vault stores no expiry, so none is shown rather than guessed.
                value={data.broker.expiry_known && data.broker.token_expires_at
                  ? new Date(data.broker.token_expires_at).toLocaleString()
                  : 'Not recorded by the broker'}
              />
            </div>

            <p className="nova-risk-note">
              Values are masked by the server. NOVA never returns a stored token or secret to the browser.
            </p>

            <div className="nova-cred-actions">
              <button type="button" className="conv-pill conv-pill--primary" onClick={() => void runVerify()} disabled={verifying}>
                {verifying ? 'Verifying…' : 'Verify again'}
              </button>
              <a className="conv-pill" href="/app/setup">Update credentials</a>
              <button type="button" className="conv-pill nova-cred-danger" onClick={() => setConfirmDisconnect(true)}>
                Disconnect
              </button>
            </div>

            {verifyResult ? (
              <p className={verifyResult.success ? 'nova-sig-ok' : 'nova-sig-bad'} role="status">
                {verifyResult.message}
              </p>
            ) : null}

            {confirmDisconnect ? (
              <div className="nova-cred-confirm" role="alertdialog" aria-label="Confirm disconnect">
                <p>
                  Disconnecting removes the stored Dhan credentials and stops any active
                  Live run. You will need to enter them again to trade.
                </p>
                <div className="nova-cred-actions">
                  <button type="button" className="conv-pill nova-cred-danger" onClick={() => void runDisconnect()}>
                    Yes, disconnect Dhan
                  </button>
                  <button type="button" className="conv-pill" onClick={() => setConfirmDisconnect(false)}>Cancel</button>
                </div>
              </div>
            ) : null}
          </section>

          <section className="nova-hooks-card" aria-label="Eligibility">
            <div className="nova-hooks-card-head"><strong>Eligibility</strong></div>
            <div className="nova-cred-grid">
              <Fact label="Paper" value={data.eligibility.paper ? 'Available' : 'Unavailable'} ok={data.eligibility.paper} />
              <Fact label="Live" value={data.eligibility.live ? 'Available' : 'Unavailable'} ok={data.eligibility.live} />
              <Fact label="Static IP" value={
                data.static_ip.available
                  ? `${data.static_ip.status}${data.static_ip.ip ? ` · ${data.static_ip.ip}` : ''}`
                  : data.static_ip.reason ?? 'Unknown'
              } />
              <Fact label="Broker mode" value={data.mode.dhan_mode || 'Unset'} />
            </div>
            {data.eligibility.live_blockers.length > 0 ? (
              <ul className="nova-cred-blockers">
                {data.eligibility.live_blockers.map((reason) => <li key={reason}>{reason}</li>)}
              </ul>
            ) : null}
          </section>

          <p className="nova-risk-note">
            Dhan is the only broker with an execution adapter today, so it is the only
            one shown. Other brokers are not supported.
          </p>
        </>
      )}
    </div>
  )
}
