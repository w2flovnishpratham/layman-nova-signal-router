import { AlertTriangle, Check, Copy, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getWebhooksOverview, type WebhooksOverview } from './webhooksApi'

function when(iso: string | null): string {
  if (!iso) return 'never'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? 'never' : d.toLocaleString()
}

export function WebhooksPage() {
  const [data, setData] = useState<WebhooksOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState<string | null>(null)

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

  return (
    <div className="nova-signals">
      <header className="nova-signals-head">
        <div>
          <h1>Webhooks</h1>
          <p>Where TradingView alerts are delivered, and what arrived recently.</p>
        </div>
      </header>

      {loading ? (
        <p className="nova-signals-state" role="status"><Loader2 size={16} /> Loading webhooks…</p>
      ) : error ? (
        <p className="nova-signals-state" role="alert"><AlertTriangle size={16} /> {error}
          <button type="button" className="conv-pill" onClick={() => void load()}>Retry</button>
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
                  <button type="button" className="conv-pill" onClick={() => void copy(ep.url)}>
                    {copied === ep.url ? <><Check size={13} /> Copied</> : <><Copy size={13} /> Copy</>}
                  </button>
                </div>
              </div>
            ))}
          </section>

          <section aria-label="Webhook secret" className="nova-hooks-card">
            <div className="nova-hooks-card-head"><strong>Webhook secret</strong></div>
            {/* One secret per account. The value is never sent to the browser. */}
            <p>
              {data.secret.set
                ? `Configured (${data.secret.source ?? 'stored'}). Shown masked — the value is never sent to the browser and cannot be revealed here.`
                : 'Not configured. Alerts will be rejected until a secret is set.'}
            </p>
            {data.secret.set ? <code className="nova-hooks-secret">{data.secret.masked}</code> : null}
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
              <table className="nova-signals-table">
                <thead>
                  <tr>
                    <th scope="col">Received</th>
                    <th scope="col">Event</th>
                    <th scope="col">Status</th>
                    <th scope="col">Signature</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent.map((row) => (
                    <tr key={row.id}>
                      <td>{when(row.received_at)}</td>
                      <td className="nova-signals-event">{row.event_id}</td>
                      <td>
                        <span className={row.processed_status === 'rejected' ? 'nova-sig-bad' : 'nova-sig-ok'}>
                          {row.processed_status}
                        </span>
                      </td>
                      <td>{row.signature_ok ? 'verified' : 'unverified'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
