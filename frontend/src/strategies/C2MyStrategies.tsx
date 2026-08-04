import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { toast } from '@/components/ui/toast'
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Copy, KeyRound, RefreshCw, RotateCw, ShieldOff } from 'lucide-react'
import {
  generateSelfC2Credential,
  getMyC2Installation,
  listMyC2Installations,
  revokeSelfC2Credential,
  rotateSelfC2Credential,
} from '../api'
import type { C2Installation, C2IssuedCredential } from '../api'


export function C2MyStrategies() {
  const [installations, setInstallations] = useState<C2Installation[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<C2Installation | null>(null)
  const [issued, setIssued] = useState<C2IssuedCredential | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')

  const refresh = useCallback(async (preferredId?: string) => {
    const rows = await listMyC2Installations()
    setInstallations(rows)
    const id = preferredId ?? selectedId ?? rows[0]?.id ?? ''
    setSelectedId(id)
    setDetail(id ? await getMyC2Installation(id) : null)
  }, [selectedId])

  useEffect(() => {
    let active = true
    listMyC2Installations()
      .then(async (rows) => {
        if (!active) return
        setInstallations(rows)
        const id = rows[0]?.id ?? ''
        setSelectedId(id)
        if (id) {
          const value = await getMyC2Installation(id)
          if (active) setDetail(value)
        }
      })
      .catch((reason) => { if (active) setError(messageOf(reason)) })
    return () => { active = false }
  }, [])

  async function open(id: string) {
    setSelectedId(id); setIssued(null); setRevealed(false); setError('')
    try { setDetail(await getMyC2Installation(id)) }
    catch (reason) { setError(messageOf(reason)) }
  }

  async function run(name: string, action: () => Promise<void>) {
    setBusy(name)
    try {
      await action()
      toast.add({ title: `${name} completed.`, type: 'success' })
      await refresh(selectedId)
    } catch (reason) {
      toast.add({ title: messageOf(reason), type: 'error' })
    } finally {
      setBusy('')
    }
  }

  async function issue(rotation: boolean) {
    if (!detail) return
    const value = rotation
      ? await rotateSelfC2Credential(detail.id)
      : await generateSelfC2Credential(detail.id)
    setIssued(value)
    setRevealed(false)
  }

  return (
    <div className="c2-my-strategies" aria-label="My Strategies">
      <div className="ps-toolbar-row">
        <p className="ps-note">Only Paper-eligible strategies appear in your engine picker. Selecting one never starts the engine, and Live eligibility remains unavailable.</p>
        <Button variant="unstyled" className="secondary-button" type="button" disabled={!!busy} onClick={() => void refresh()}><RefreshCw size={14} /> Refresh</Button>
      </div>
      {error ? <div className="ps-message error" role="alert">{error}</div> : null}

      {installations.length ? <div className="pine-managed-grid">
        <aside className="ps-list" aria-label="My strategy installations">
          <div className="ps-list-toolbar"><Input variant="unstyled" aria-label="Search installations" placeholder="Search strategies…" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
          {installations.filter((item) => item.strategy_name.toLowerCase().includes(search.toLowerCase())).map((item) => <Button variant="unstyled" type="button" className={`ps-list-item${item.id === selectedId ? ' active' : ''}`} key={item.id} onClick={() => void open(item.id)}>
            <strong>{item.strategy_name}</strong>
            <span>{item.instance_label} · {item.mode}</span>
            <span>{ownerStatus(item)}</span>
          </Button>)}
        </aside>
        {detail ? <article className="pine-managed-detail">
          <div className="ps-card-head"><div><span>{detail.strategy_version} · {detail.candidate_sha256.slice(0, 12)}…</span><h2>{detail.instance_label}</h2></div><span className="ps-status">{ownerStatus(detail)}</span></div>
          <div className="ps-summary-grid">
            <div><span>Setup</span><strong>{detail.mode}</strong></div>
            <div><span>Credential</span><strong>{detail.credential_status}</strong></div>
            <div><span>HOLD</span><strong>{detail.hold_status}</strong></div>
            <div><span>Paper</span><strong>{detail.paper_eligible ? 'READY' : 'NOT READY'}</strong></div>
            <div><span>Live</span><strong>UNAVAILABLE</strong></div>
          </div>
          {!detail.paper_eligible ? <div className="ps-message">{detail.blocking_reasons.join(' · ') || 'Waiting for setup'}</div> : <div className="ps-message success"><CheckCircle2 size={16} /> Paper ready. The engine remains stopped until you explicitly start it elsewhere.</div>}

          {detail.mode === 'SELF' && detail.setup_package ? <>
            <div className="c1-provenance">
              <strong>Self setup package</strong>
              <span>Webhook: {detail.setup_package.webhook_url}</span>
              <span>{detail.setup_package.instructions}</span>
              <span>{detail.setup_package.expected_hold_behavior}</span>
            </div>
            <div className="ps-actions">
              <Button variant="unstyled" className="secondary-button" type="button" onClick={() => void navigator.clipboard.writeText(detail.setup_package?.approved_pine ?? '')}><Copy size={14} /> Copy approved Pine</Button>
              <Button variant="unstyled" className="secondary-button" type="button" onClick={() => void navigator.clipboard.writeText(detail.setup_package?.alert_message ?? '')}><Copy size={14} /> Copy alert template</Button>
            </div>
            <details><summary>Exact approved Pine</summary><pre className="pine-review-source">{detail.setup_package.approved_pine}</pre></details>
            <details><summary>HOLD alert JSON template</summary><pre className="pine-review-source">{detail.setup_package.alert_message}</pre></details>
            <div className="ps-actions">
              {detail.credential_status !== 'ACTIVE' ? <Button variant="unstyled" className="ps-primary" type="button" disabled={!!busy} onClick={() => void run('Credential generation', async () => issue(false))}><KeyRound size={14} /> Generate one-time credential</Button> : <Button variant="unstyled" className="secondary-button" type="button" disabled={!!busy} onClick={() => { if (window.confirm('Rotate this credential? The old TradingView alert will stop and HOLD must be verified again.')) void run('Credential rotation', async () => issue(true)) }}><RotateCw size={14} /> Rotate credential</Button>}
              {detail.credential_status === 'ACTIVE' ? <Button variant="unstyled" className="ps-danger" type="button" disabled={!!busy} onClick={() => { if (window.confirm('Revoke this credential and remove Paper eligibility?')) void run('Credential revocation', async () => { await revokeSelfC2Credential(detail.id) }) }}><ShieldOff size={14} /> Revoke credential</Button> : null}
            </div>
            <p className="ps-note">After installing the exact Pine, configure the alert with the one-time JSON and send one real HOLD. Do not put the credential in the webhook URL.</p>
          </> : <p className="ps-note">Managed setup is handled by NOVA administrators. Its private credential and admin notes are never shown here.</p>}
        </article> : null}
      </div> : <div className="ps-empty"><KeyRound size={28} /><h2>No C2 installations yet</h2><p>An administrator must compile and install an approved candidate first.</p></div>}

      {issued ? <div className="ps-secret-panel" role="dialog" aria-label="One-time self credential">
        <div><strong>Shown only now</strong><span>Copy the alert JSON before dismissing. This secret cannot be reopened.</span></div>
        <code>{revealed ? issued.token : '••••••••••••••••••••••••••••••••'}</code>
        <Button variant="unstyled" type="button" className="secondary-button" onClick={() => setRevealed((value) => !value)}>{revealed ? 'Hide' : 'Reveal'}</Button>
        <Button variant="unstyled" type="button" className="secondary-button" onClick={() => void navigator.clipboard.writeText(issued.setup_package.alert_message)}><Copy size={14} /> Copy complete alert JSON</Button>
        <Button variant="unstyled" type="button" className="ps-primary" onClick={() => { setIssued(null); setRevealed(false) }}>Dismiss permanently</Button>
      </div> : null}
    </div>
  )
}


function ownerStatus(item: C2Installation) {
  if (item.suspended_at) return 'Setup suspended'
  if (item.credential_status === 'REVOKED') return 'Credential revoked'
  if (item.paper_eligible) return 'Paper ready'
  if (item.hold_status !== 'VERIFIED' && item.credential_status === 'ACTIVE') return 'Waiting for TradingView HOLD'
  if (item.credential_status === 'NOT_GENERATED') return item.mode === 'MANAGED' ? 'Waiting for setup' : 'Credential required'
  return 'Waiting for admin compile'
}


function messageOf(reason: unknown) {
  return reason instanceof Error ? reason.message : 'Request failed.'
}
