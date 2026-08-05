import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Button } from '@/components/ui/button'
import { AlertTriangle, Check, Copy, Eye, EyeOff, KeyRound, Loader2, Shield } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import type { AuthUser } from '../api'
import { Skeleton } from '@/components/ui/skeleton'
import {
  getManagedStrategyWebhookSecret,
  getWebhooksOverview,
  revealManagedStrategyWebhookSecret,
  rotateManagedStrategyWebhookSecret,
  rotateWebhookSecret,
  type ManagedStrategySecretMeta,
  type WebhooksOverview,
} from './webhooksApi'

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

const WEBHOOK_COLUMNS = ['Received', 'Event', 'Status', 'Signature']

export function WebhooksPage({ user }: { user?: AuthUser } = {}) {
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
        <div className="nova-signals-table-wrap" role="status" aria-busy="true" aria-label="Loading webhooks">
          <Table variant="unstyled" className="nova-signals-table">
            <TableHeader>
              <TableRow>
                {WEBHOOK_COLUMNS.map((column) => <TableHead scope="col" key={column}>{column}</TableHead>)}
              </TableRow>
            </TableHeader>
            <TableBody>
              {Array.from({ length: 6 }, (_, row) => (
                <TableRow key={row}>
                  {WEBHOOK_COLUMNS.map((column) => (
                    <TableCell key={column}><Skeleton className="h-3.5 w-full max-w-28" /></TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
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

          {user?.is_admin ? <ManagedStrategySecretCard copied={copied} onCopy={copy} /> : null}

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

function ManagedStrategySecretCard({ copied, onCopy }: { copied: string | null; onCopy: (value: string) => Promise<void> }) {
  const [meta, setMeta] = useState<ManagedStrategySecretMeta | null>(null)
  const [revealed, setRevealed] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setMeta(await getManagedStrategyWebhookSecret())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the managed secret.')
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  async function reveal() {
    setBusy(true)
    setError('')
    try {
      setRevealed(await revealManagedStrategyWebhookSecret())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not reveal the managed secret.')
    } finally {
      setBusy(false)
    }
  }

  async function rotate() {
    if (!window.confirm(
      'Rotate the NOVA managed secret? Every broadcast chart pasted with the current value stops authenticating the moment this lands — each must be updated with the new value before its next signal, or that signal is rejected.',
    )) return
    setBusy(true)
    setError('')
    try {
      const secret = await rotateManagedStrategyWebhookSecret()
      setRevealed(secret)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not rotate the managed secret.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-label="NOVA managed strategy secret" className="nova-hooks-card">
      <div className="nova-hooks-card-head"><Shield size={14} /><strong>NOVA managed secret (admin-only)</strong></div>
      <p>
        Shared by every admin-run broadcast chart for a NOVA_SHARED catalog strategy — paste it into the
        indicator's "NOVA Managed Secret" setting, never into the source itself.
      </p>
      {error ? <p className="nova-signals-state" role="alert"><AlertTriangle size={16} /> {error}</p> : null}
      {meta ? (
        <div className="nova-hooks-url">
          <code className="nova-hooks-secret">{revealed ?? meta.masked ?? 'Not configured'}</code>
          {meta.set ? (
            <Button variant="unstyled" type="button" className="conv-pill" disabled={busy} onClick={() => (revealed ? setRevealed(null) : void reveal())}>
              {revealed ? <><EyeOff size={13} /> Hide</> : <><Eye size={13} /> Reveal</>}
            </Button>
          ) : null}
          {revealed ? (
            <Button variant="unstyled" type="button" className="conv-pill" onClick={() => void onCopy(revealed)}>
              {copied === revealed ? <><Check size={13} /> Copied</> : <><Copy size={13} /> Copy</>}
            </Button>
          ) : null}
        </div>
      ) : null}
      <Button variant="unstyled" type="button" className="conv-pill" disabled={busy} onClick={() => void rotate()}>
        {busy ? <><Loader2 size={13} /> Working...</> : <><KeyRound size={13} /> {meta?.set ? 'Rotate' : 'Generate'}</>}
      </Button>
    </section>
  )
}
