import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Button } from '@/components/ui/button'
import { AlertTriangle, Check, Copy, KeyRound, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { PageSkeleton } from '../components/PageSkeleton'
import { getWebhooksOverview, rotateWebhookSecret, type WebhooksOverview } from './webhooksApi'

function when(iso: string | null): string {
  if (!iso) return 'never'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? 'never' : d.toLocaleString()
}

function statusTone(status: string): string {
  if (status === 'received') return 'nova-sig-info'
  if (status === 'queued') return 'nova-sig-warn'
  if (status === 'accepted' || status === 'fanned_out') return 'nova-sig-ok'
  if (status === 'rejected') return 'nova-sig-bad'
  return 'nova-sig-neutral'
}

export function WebhooksPage() {
  const [data, setData] = useState<WebhooksOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState<string | null>(null)
  const [rotatedSecret, setRotatedSecret] = useState('')
  const [rotating, setRotating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await getWebhooksOverview())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load webhooks.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(url)
      window.setTimeout(() => setCopied(null), 1500)
    } catch {
      /* clipboard unavailable — the URL is visible for manual copy */
    }
  }

  async function rotateSecret() {
    if (data?.secret.set && !window.confirm(
      'Rotating this secret immediately invalidates the old one. Continue?',
    )) return
    setRotating(true)
    setError('')
    try {
      const secret = await rotateWebhookSecret()
      setRotatedSecret(secret)
      setData((current) => current ? {
        ...current,
        secret: { set: true, masked: null, source: 'account' },
      } : current)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not rotate the webhook secret.')
    } finally {
      setRotating(false)
    }
  }

  return (
    <div className="nova-signals">
      <header className="nova-signals-head">
        <div>
          <h1>Webhooks</h1>
          <p>Where TradingView alerts are delivered, and what arrived recently.</p>
        </div>
      </header>

      {loading ? (
        <PageSkeleton label="Loading webhooks" variant="table" />
      ) : error ? (
        <p className="nova-signals-state" role="alert"><AlertTriangle size={16} /> {error}
          <Button variant="unstyled" type="button" className="conv-pill" onClick={() => void load()}>Retry</Button>
        </p>
      ) : !data ? null : (
        <>
          <section aria-label="Endpoints" className="nova-hooks-endpoints">
            {data.endpoints.length === 0 ? (
              <p className="nova-signals-state">No public base URL is configured, so no inbound endpoint can be shown.</p>
            ) : data.endpoints.map((ep) => (
              <div key={ep.key} className="nova-hooks-card">
                <div className="nova-hooks-card-head">
                  <strong>{ep.label}</strong>
                  <span className="nova-hooks-method">{ep.method}</span>
                </div>
                <p>{ep.description}</p>
                <div className="nova-hooks-url">
                  <code>{ep.url}</code>
                  <Button variant="unstyled" type="button" className="conv-pill" onClick={() => void copy(ep.url)}>
                    {copied === ep.url ? <><Check size={13} /> Copied</> : <><Copy size={13} /> Copy</>}
                  </Button>
                </div>
              </div>
            ))}
          </section>

          <section aria-label="Webhook secret" className="nova-hooks-card">
            <div className="nova-hooks-card-head"><strong>Webhook secret</strong></div>
            <p>
              {data.secret.set
                ? 'Configured. Rotate it if the current value is lost or compromised.'
                : 'Not configured. Generate one before sending signed alerts.'}
            </p>
            <Button
              variant="unstyled"
              type="button"
              className="conv-pill"
              disabled={rotating}
              onClick={() => void rotateSecret()}
            >
              {rotating
                ? <><Loader2 size={13} /> Rotating...</>
                : <><KeyRound size={13} /> {data.secret.set ? 'Rotate secret' : 'Generate secret'}</>}
            </Button>
            {rotatedSecret ? (
              <>
                <div className="nova-hooks-url nova-hooks-secret-once">
                  <code className="nova-hooks-secret">{rotatedSecret}</code>
                  <Button variant="unstyled" type="button" className="conv-pill" onClick={() => void copy(rotatedSecret)}>
                    {copied === rotatedSecret
                      ? <><Check size={13} /> Copied</>
                      : <><Copy size={13} /> Copy secret</>}
                  </Button>
                </div>
                <p role="status">Shown once. Copy it now and update every sender before leaving this page.</p>
              </>
            ) : null}
          </section>

          <section aria-label="Delivery activity" className="nova-signals-counts">
            {Object.entries(data.deliveries.counts).map(([k, v]) => (
              <div key={k} className="nova-signals-count"><span>{k}</span><strong>{v}</strong></div>
            ))}
            <div className="nova-signals-count">
              <span>signature verified</span><strong>{data.deliveries.signature_verified}</strong>
            </div>
            <span className="nova-signals-window">
              last {data.window_hours}h · last delivery {when(data.deliveries.last_delivery_at)}
            </span>
          </section>

          {data.recent.length === 0 ? (
            <p className="nova-signals-state" role="status">No deliveries recorded yet for this account.</p>
          ) : (
            <div className="nova-signals-table-wrap">
              <Table variant="unstyled" className="nova-signals-table">
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Received</TableHead>
                    <TableHead scope="col">Event</TableHead>
                    <TableHead scope="col">Status</TableHead>
                    <TableHead scope="col">Signature</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.recent.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell>{when(row.received_at)}</TableCell>
                      <TableCell className="nova-signals-event">{row.event_id}</TableCell>
                      <TableCell>
                        <span className={statusTone(row.processed_status)}>
                          {row.processed_status}
                        </span>
                      </TableCell>
                      <TableCell>
                        <span className={row.signature_ok ? 'nova-sig-ok' : 'nova-sig-bad'}>
                          {row.signature_ok ? 'verified' : 'unverified'}
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
