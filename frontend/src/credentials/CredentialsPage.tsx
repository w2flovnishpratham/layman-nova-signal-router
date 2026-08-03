import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { toast } from '@/components/ui/toast'
import { PageSkeleton } from '../components/PageSkeleton'
import {
  AlertTriangle,
  Check,
  Copy,
  KeyRound,
  Loader2,
  RefreshCw,
  ShieldCheck,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  disconnectBroker,
  getCredentialsOverview,
  saveCredentials,
  verifyBroker,
  type CredentialsOverview,
  type DhanConnectionStatus,
} from './credentialsApi'

const CONNECTION_STATUS_LABEL: Record<DhanConnectionStatus, string> = {
  NOT_CONFIGURED: 'Not configured',
  CONNECTED: 'Connected',
  INVALID_CREDENTIALS: 'Invalid credentials',
  TOKEN_EXPIRED: 'Access token expired',
  BROKER_UNAVAILABLE: 'Broker unavailable',
  DISCONNECTED: 'Disconnected',
  ERROR: 'Error',
}

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

function formatDate(value: string | null): string {
  if (!value) return 'Never'
  return new Intl.DateTimeFormat('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Asia/Kolkata',
  }).format(new Date(value))
}

function expiryDays(value: string | null): number | null {
  if (!value) return null
  return Math.max(0, Math.ceil((new Date(value).getTime() - Date.now()) / 86_400_000))
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
  const tokenInput = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const overview = await getCredentialsOverview()
      setData(overview)
      setClientIdDraft(overview.broker.client_id ?? '')
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Could not load credentials.')
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
      const result = await toast.promise(verifyBroker(), {
        loading: { title: 'Contacting Dhan to verify credentials…', type: 'loading', timeout: 0 },
        success: (value) => ({ title: value.message, type: value.success ? 'success' : 'error' }),
        error: (reason) => ({ title: reason instanceof Error ? reason.message : 'Verification failed.', type: 'error' }),
      })
      setVerifyResult(result)
    } catch (verifyError) {
      const message = verifyError instanceof Error ? verifyError.message : 'Verification failed.'
      setVerifyResult({ success: false, message })
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
      const result = await toast.promise((async () => {
        await saveCredentials({ clientId: clientIdDraft, accessToken: accessTokenDraft })
        setAccessTokenDraft('')
        setVerifying(true)
        return verifyBroker()
      })(), {
        loading: { title: 'Saving and verifying Dhan credentials…', type: 'loading', timeout: 0 },
        success: (value) => ({ title: value.message, type: value.success ? 'success' : 'error' }),
        error: (reason) => ({ title: reason instanceof Error ? reason.message : 'Save failed.', type: 'error' }),
      })
      setVerifyResult(result)
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Save failed.')
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
      toast.add({ title: 'Dhan account disconnected.', type: 'success' })
    } catch (disconnectError) {
      toast.add({
        title: disconnectError instanceof Error ? disconnectError.message : 'Disconnect failed.',
        type: 'error',
      })
    }
  }

  const daysToExpiry = data?.broker.expiry_known
    ? expiryDays(data.broker.token_expires_at)
    : null

  return (
    <div className="nova-signals nova-credentials-page">
      <header className="nova-signals-head nova-credentials-head">
        <div>
          <h1>Credentials</h1>
          <p>
            Broker accounts the router can place orders through. Stored server-side in an
            encrypted vault — the browser only ever sees masked values.
          </p>
        </div>
      </header>

      {loading ? (
        <PageSkeleton label="Loading credentials" variant="split-form" />
      ) : error ? (
        <p className="nova-signals-state" role="alert">
          <AlertTriangle size={16} /> {error}
          <Button variant="unstyled" type="button" className="conv-pill" onClick={() => void load()}>Retry</Button>
        </p>
      ) : !data ? null : (
        <div className="nova-credentials-layout">
          <div className="nova-credentials-main">
            <section
              className={`nova-credentials-card nova-credentials-account${data.broker.connected ? ' is-connected' : ''}`}
              aria-label="Dhan primary account"
            >
              <div className="nova-credentials-account-head">
                <img className="nova-credentials-broker-mark" src="/dhan.png" alt="Dhan logo" />
                <div>
                  <h2>{data.broker.name} — Primary Account</h2>
                  <p>
                    {data.broker.connected
                      ? `Verified ${formatDate(data.broker.last_verified_at)}`
                      : CONNECTION_STATUS_LABEL[data.broker.connection_status] ?? data.broker.connection_status}
                  </p>
                </div>
                <Badge
                  variant="unstyled"
                  className={`nova-credentials-status${data.broker.connected ? ' is-connected' : ''}`}
                >
                  <span aria-hidden="true" />
                  {data.broker.connected ? 'CONNECTED' : 'NOT CONNECTED'}
                </Badge>
              </div>

              <div className="nova-credentials-account-grid">
                <div className="nova-credentials-account-fact">
                  <span>Client ID</span>
                  <strong className="nova-credentials-mono">
                    {data.broker.client_id_masked ?? (data.broker.has_client_id ? 'Configured' : 'Not configured')}
                  </strong>
                </div>
                <div className="nova-credentials-account-fact">
                  <span>Access Token</span>
                  <strong className="nova-credentials-secret">
                    {data.broker.has_access_token ? '••••••••••••' : 'Not set'}
                    {daysToExpiry !== null ? <small>Expires in {daysToExpiry} days</small> : null}
                  </strong>
                </div>
                <div className="nova-credentials-account-fact">
                  <span>Nova Static IP {data.static_ip.available ? '(allow-listed)' : ''}</span>
                  <strong className="nova-credentials-mono">
                    {data.static_ip.ip ?? data.static_ip.reason ?? 'Not assigned'}
                    {data.static_ip.ip ? (
                      <Button
                        variant="unstyled"
                        type="button"
                        className="nova-credentials-icon-button"
                        aria-label="Copy Nova Static IP"
                        onClick={() => {
                          void navigator.clipboard.writeText(data.static_ip.ip ?? '')
                          toast.add({ title: 'Static IP copied.', type: 'success' })
                        }}
                      >
                        <Copy size={13} />
                      </Button>
                    ) : null}
                  </strong>
                </div>
                <div className="nova-credentials-account-fact">
                  <span>Last Verified</span>
                  <strong>{formatDate(data.broker.last_verified_at)}</strong>
                </div>
              </div>

              {data.broker.last_verification_error ? (
                <p className="nova-credentials-inline-error" role="alert">
                  <AlertTriangle size={14} /> {data.broker.last_verification_error}
                </p>
              ) : null}

              <div className="nova-credentials-account-actions">
                <Button
                  variant="unstyled"
                  type="button"
                  className="nova-credentials-button"
                  onClick={() => void runVerify()}
                  disabled={verifying || saving || !data.broker.has_client_id || !data.broker.has_access_token}
                >
                  {verifying ? <Loader2 size={14} /> : <RefreshCw size={14} />}
                  {verifying ? 'Verifying…' : 'Re-verify Now'}
                </Button>
                <Button
                  variant="unstyled"
                  type="button"
                  className="nova-credentials-button"
                  onClick={() => {
                    tokenInput.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                    tokenInput.current?.focus()
                  }}
                >
                  Rotate Token
                </Button>
                <Button
                  variant="unstyled"
                  type="button"
                  className="nova-credentials-button is-danger"
                  onClick={() => setConfirmDisconnect(true)}
                  disabled={!data.broker.has_client_id && !data.broker.has_access_token}
                >
                  Disconnect
                </Button>
              </div>
            </section>

            <section className="nova-credentials-card nova-credentials-connect" aria-labelledby="connect-broker-title">
              <div>
                <h2 id="connect-broker-title">Connect a Broker Account</h2>
                <p>Credentials are verified against the broker before saving. Live routing stays subject to server safety checks.</p>
              </div>
              <div className="nova-credentials-broker-tabs" role="tablist" aria-label="Supported brokers">
                <Button variant="unstyled" type="button" role="tab" aria-selected="true">Dhan</Button>
              </div>
              <form
                className="nova-cred-form"
                onSubmit={(event) => {
                  event.preventDefault()
                  void runSaveAndVerify()
                }}
              >
                <label className="nova-cred-field">
                  <span>Dhan Client ID</span>
                  <Input
                    variant="unstyled"
                    type="text"
                    value={clientIdDraft}
                    placeholder="e.g. 1100261097"
                    onChange={(event) => setClientIdDraft(event.target.value)}
                    autoComplete="off"
                  />
                </label>
                <label className="nova-cred-field">
                  <span>Access Token</span>
                  <Input
                    ref={tokenInput}
                    variant="unstyled"
                    type="password"
                    value={accessTokenDraft}
                    onChange={(event) => setAccessTokenDraft(event.target.value)}
                    placeholder={data.broker.has_access_token
                      ? 'Leave blank to keep the existing token'
                      : 'Paste token from Dhan developer console'}
                    autoComplete="off"
                  />
                </label>
                <div className="nova-cred-actions">
                  <Button
                    variant="unstyled"
                    type="submit"
                    className="nova-credentials-primary-button"
                    disabled={
                      saving
                      || verifying
                      || !clientIdDraft.trim()
                      || (!accessTokenDraft.trim() && !data.broker.has_access_token)
                    }
                  >
                    <KeyRound size={14} />
                    {saving ? 'Saving…' : 'Connect & Verify Dhan Account'}
                  </Button>
                </div>
              </form>
              {verifyResult ? (
                <p className={verifyResult.success ? 'nova-sig-ok' : 'nova-sig-bad'} role="status">
                  {verifyResult.message}
                </p>
              ) : null}
            </section>
          </div>

          <aside className="nova-credentials-aside">
            <section className="nova-credentials-card nova-credentials-security" aria-labelledby="security-posture-title">
              <h2 id="security-posture-title"><ShieldCheck size={16} /> Security Posture</h2>
              <ul>
                <li><Check size={14} /> Tokens are encrypted at rest in the server vault.</li>
                <li><Check size={14} /> Session stays server-side; the browser receives a secure cookie.</li>
                <li className={data.static_ip.available ? '' : 'is-muted'}>
                  {data.static_ip.available ? <Check size={14} /> : <X size={14} />}
                  {data.static_ip.available
                    ? 'Orders egress only through the assigned static IP.'
                    : 'No static egress IP is assigned yet.'}
                </li>
                <li><Check size={14} /> Broker credentials are verified without returning the token.</li>
              </ul>
            </section>

            {daysToExpiry !== null && daysToExpiry <= 7 ? (
              <section className="nova-credentials-card nova-credentials-expiry" aria-label="Token expiring soon">
                <h2><AlertTriangle size={16} /> Token expiring soon</h2>
                <p>Your Dhan access token expires in {daysToExpiry} days. Live routing will pause automatically if it lapses.</p>
                <Button
                  variant="unstyled"
                  type="button"
                  className="nova-credentials-button"
                  onClick={() => tokenInput.current?.focus()}
                >
                  Rotate Token Now
                </Button>
              </section>
            ) : null}

            <section className="nova-credentials-card nova-credentials-eligibility" aria-label="Eligibility">
              <h2>Eligibility</h2>
              <div className="nova-cred-grid">
                <Fact label="Paper" value={data.eligibility.paper ? 'Available' : 'Unavailable'} ok={data.eligibility.paper} />
                <Fact label="Live" value={data.eligibility.live ? 'Available' : 'Unavailable'} ok={data.eligibility.live} />
                <Fact
                  label="Static IP"
                  value={data.static_ip.available
                    ? `${data.static_ip.status}${data.static_ip.ip ? ` · ${data.static_ip.ip}` : ''}`
                    : data.static_ip.reason ?? 'Unknown'}
                  ok={data.static_ip.available && data.static_ip.status === 'verified'}
                />
                <Fact label="Broker mode" value={data.mode.dhan_mode || 'Unset'} />
              </div>
              {data.eligibility.live_blockers.length > 0 ? (
                <ul className="nova-cred-blockers">
                  {data.eligibility.live_blockers.map((reason) => <li key={reason}>{reason}</li>)}
                </ul>
              ) : null}
            </section>

            <section className="nova-credentials-card nova-credentials-log" aria-labelledby="verification-log-title">
              <h2 id="verification-log-title">Verification Log</h2>
              {data.broker.last_verified_at || data.broker.last_verification_error ? (
                <div>
                  {data.broker.last_verified_at ? (
                    <article>
                      <time>{formatDate(data.broker.last_verified_at)}</time>
                      <span>Latest broker verification</span>
                      <strong className={data.broker.connected ? 'is-pass' : 'is-warn'}>
                        {data.broker.connected ? 'Pass' : 'Warn'}
                      </strong>
                    </article>
                  ) : null}
                  {data.broker.last_verification_error ? (
                    <article>
                      <time>{formatDate(data.broker.last_updated_at)}</time>
                      <span>{data.broker.last_verification_error}</span>
                      <strong className="is-warn">Warn</strong>
                    </article>
                  ) : null}
                </div>
              ) : (
                <p className="nova-credentials-empty">No verification events recorded yet.</p>
              )}
            </section>
          </aside>

          <AlertDialog open={confirmDisconnect} onOpenChange={setConfirmDisconnect}>
            <AlertDialogContent className="border border-border bg-popover shadow-2xl">
              <AlertDialogHeader>
                <AlertDialogTitle>Disconnect Dhan?</AlertDialogTitle>
                <AlertDialogDescription>
                  Disconnecting removes the stored Dhan credentials and stops any active
                  Live run. You will need to enter them again to trade.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel variant="unstyled" className="conv-pill">Cancel</AlertDialogCancel>
                <AlertDialogAction variant="unstyled" className="conv-pill nova-cred-danger" onClick={() => void runDisconnect()}>
                  Yes, disconnect Dhan
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      )}
    </div>
  )
}
