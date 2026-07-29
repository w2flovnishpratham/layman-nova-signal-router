import { AlertTriangle, Check, Loader2, ShieldCheck, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import {
  disconnectBroker,
  getCredentialsOverview,
  saveCredentials,
  verifyBroker,
  type CredentialsOverview,
  type DhanConnectionStatus,
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

const CONNECTION_STATUS_LABEL: Record<DhanConnectionStatus, string> = {
  NOT_CONFIGURED: 'Not configured',
  CONNECTED: 'Connected',
  INVALID_CREDENTIALS: 'Invalid credentials',
  TOKEN_EXPIRED: 'Access token expired',
  BROKER_UNAVAILABLE: 'Broker unavailable',
  DISCONNECTED: 'Disconnected',
  ERROR: 'Error',
}

export function CredentialsPage() {
  const [data, setData] = useState<CredentialsOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [verifying, setVerifying] = useState(false)
  const [saving, setSaving] = useState(false)
  const [verifyResult, setVerifyResult] = useState<{ success: boolean; message: string } | null>(null)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)
  const [clientIdDraft, setClientIdDraft] = useState('')
  const [accessTokenDraft, setAccessTokenDraft] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const overview = await getCredentialsOverview()
      setData(overview)
      // Client ID is not a secret: autofill it. Access Token is always left blank.
      setClientIdDraft(overview.broker.client_id ?? '')
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
      await load()
    }
  }

  async function runSaveAndVerify() {
    setSaving(true)
    setVerifyResult(null)
    setError('')
    try {
      await saveCredentials({ clientId: clientIdDraft, accessToken: accessTokenDraft })
      setAccessTokenDraft('')
      setVerifying(true)
      setVerifyResult(await verifyBroker())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed.')
    } finally {
      setSaving(false)
      setVerifying(false)
      await load()
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
              <Fact label="Client ID" value={data.broker.has_client_id ? 'Configured' : 'Not configured'} ok={data.broker.has_client_id} />
              <Fact label="Access token" value={data.broker.has_access_token ? 'Saved' : 'Not set'} ok={data.broker.has_access_token} />
              <Fact label="Webhook secret" value={data.broker.has_webhook_secret ? 'Stored' : 'Not set'} ok={data.broker.has_webhook_secret} />
              <Fact
                label="Connection"
                value={CONNECTION_STATUS_LABEL[data.broker.connection_status] ?? data.broker.connection_status}
                ok={data.broker.connection_status === 'CONNECTED'}
              />
              <Fact
                label="Last verified"
                value={data.broker.last_verified_at ? new Date(data.broker.last_verified_at).toLocaleString() : 'Never'}
              />
              <Fact
                label="Token expiry"
                // The vault stores no expiry, so none is shown rather than guessed.
                value={data.broker.expiry_known && data.broker.token_expires_at
                  ? new Date(data.broker.token_expires_at).toLocaleString()
                  : 'Not recorded by the broker'}
              />
            </div>

            {data.broker.connection_status !== 'NOT_CONFIGURED' && data.broker.connection_status !== 'CONNECTED' ? (
              <p className="nova-sig-bad" role="alert">
                {data.broker.last_verification_error ?? CONNECTION_STATUS_LABEL[data.broker.connection_status]}
              </p>
            ) : null}

            <form
              className="nova-cred-form"
              onSubmit={(e) => {
                e.preventDefault()
                void runSaveAndVerify()
              }}
            >
              <label className="nova-cred-field">
                <span>Dhan Client ID</span>
                <input
                  type="text"
                  value={clientIdDraft}
                  onChange={(e) => setClientIdDraft(e.target.value)}
                  autoComplete="off"
                />
              </label>
              <label className="nova-cred-field">
                <span>Dhan Access Token</span>
                <input
                  type="password"
                  value={accessTokenDraft}
                  onChange={(e) => setAccessTokenDraft(e.target.value)}
                  placeholder="Leave blank to keep the existing token"
                  autoComplete="off"
                />
              </label>

              <div className="nova-cred-actions">
                <button type="submit" className="conv-pill conv-pill--primary" disabled={saving || verifying}>
                  {saving ? 'Saving…' : 'Save and verify'}
                </button>
                <button type="button" className="conv-pill" onClick={() => void runVerify()} disabled={verifying || saving}>
                  {verifying ? 'Verifying…' : 'Verify again'}
                </button>
                <button type="button" className="conv-pill nova-cred-danger" onClick={() => setConfirmDisconnect(true)}>
                  Disconnect
                </button>
              </div>
            </form>

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
