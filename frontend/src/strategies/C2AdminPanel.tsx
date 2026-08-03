import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { NativeSelect } from "@/components/ui/native-select"
import { Button } from '@/components/ui/button'
import { toast } from '@/components/ui/toast'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, Copy, Download, KeyRound, RotateCw, ShieldOff } from 'lucide-react'
import {
  createC2Installation,
  downloadC2ApprovedPine,
  generateAdminC2Credential,
  getAdminC2Conversion,
  getC2Config,
  listAdminC2Installations,
  listAdminUsers,
  markAdminC2Ready,
  promoteAdminC2PaperVerification,
  recordC2CompileFailure,
  recordC2CompileSuccess,
  revokeAdminC2Credential,
  rotateAdminC2Credential,
  suspendAdminC2Installation,
} from '../api'
import type {
  AdminPineConversion,
  AdminUserSummary,
  C2Installation,
  C2IssuedCredential,
} from '../api'


export function C2AdminPanel({ conversion }: { conversion: AdminPineConversion }) {
  const approved = conversion.conversion_status === 'APPROVED_FOR_TRADINGVIEW_COMPILE'
  const [enabled, setEnabled] = useState(false)
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof getAdminC2Conversion>> | null>(null)
  const [installations, setInstallations] = useState<C2Installation[]>([])
  const [users, setUsers] = useState<AdminUserSummary[]>([])
  const [ownerId, setOwnerId] = useState('')
  const [mode, setMode] = useState<'MANAGED' | 'SELF'>('SELF')
  const [label, setLabel] = useState('')
  const [compileNotes, setCompileNotes] = useState('')
  const [compilerError, setCompilerError] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [search, setSearch] = useState('')
  const [issued, setIssued] = useState<C2IssuedCredential | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    const [config, status, allInstallations, owners] = await Promise.all([
      getC2Config(),
      getAdminC2Conversion(conversion.id),
      listAdminC2Installations(),
      listAdminUsers(),
    ])
    setEnabled(config.enabled)
    setDetail(status)
    const matching = allInstallations.installations.filter((item) => item.conversion_id === conversion.id)
    setInstallations(matching)
    setUsers(owners)
    setOwnerId((current) => current || owners[0]?.id || '')
    setLabel((current) => current || `${conversion.strategy_name} Paper`)
    setSelectedId((current) => matching.some((item) => item.id === current) ? current : matching[0]?.id ?? '')
  }, [conversion.id, conversion.strategy_name])

  useEffect(() => {
    if (!approved) return
    let active = true
    Promise.all([getC2Config(), getAdminC2Conversion(conversion.id), listAdminC2Installations(), listAdminUsers()])
      .then(([config, status, allInstallations, owners]) => {
        if (!active) return
        setEnabled(config.enabled)
        setDetail(status)
        const matching = allInstallations.installations.filter((item) => item.conversion_id === conversion.id)
        setInstallations(matching)
        setUsers(owners)
        setOwnerId(owners[0]?.id ?? '')
        setLabel(`${conversion.strategy_name} Paper`)
        setSelectedId(matching[0]?.id ?? '')
      })
      .catch((reason) => { if (active) setError(messageOf(reason)) })
    return () => { active = false }
  }, [approved, conversion.id, conversion.strategy_name])

  const selected = useMemo(
    () => installations.find((item) => item.id === selectedId) ?? null,
    [installations, selectedId],
  )

  if (!approved || !enabled || !detail) return null

  async function run(name: string, action: () => Promise<void>) {
    setBusy(name)
    try {
      await action()
      toast.add({ title: `${name} completed.`, type: 'success' })
      await refresh()
    } catch (reason) {
      toast.add({ title: messageOf(reason), type: 'error' })
    } finally {
      setBusy('')
    }
  }

  async function downloadPine() {
    const { blob, filename } = await downloadC2ApprovedPine(conversion.id)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
    URL.revokeObjectURL(url)
  }

  async function issue(action: 'generate' | 'rotate') {
    if (!selected) return
    const credential = action === 'generate'
      ? await generateAdminC2Credential(selected.id)
      : await rotateAdminC2Credential(selected.id)
    setIssued(credential.credential)
    setRevealed(false)
  }

  const compiled = detail.compile?.result === 'SUCCESS'
  return (
    <section className="ps-card c2-admin-panel" aria-label="C2 TradingView installation">
      <div className="ps-card-head">
        <div><span>C2 · admin-only manual setup</span><h2>TradingView installation and Paper eligibility</h2></div>
        <span className="ps-status">{detail.compile?.result ?? 'AWAITING COMPILE'}</span>
      </div>
      <p className="ps-note">Compilation is human-confirmed. No browser automation, engine start, order, or Live eligibility is created here.</p>
      {error ? <div className="ps-message error" role="alert">{error}</div> : null}

      <div className="c1-provenance">
        <strong>Exact approved candidate</strong>
        <span>Candidate SHA: {detail.candidate.candidate_sha256}</span>
        <span>Source SHA: {detail.candidate.source_sha256}</span>
        <span>Prompt {detail.candidate.prompt_version} · Transport {detail.candidate.transport_version}</span>
      </div>
      <div className="ps-actions">
        <Button variant="unstyled" className="secondary-button" type="button" onClick={() => void navigator.clipboard.writeText(detail.candidate.pine)}><Copy size={14} /> Copy approved Pine</Button>
        <Button variant="unstyled" className="secondary-button" type="button" onClick={() => void downloadPine()}><Download size={14} /> Download approved Pine</Button>
      </div>
      <details><summary>View exact approved Pine</summary><pre className="pine-review-source">{detail.candidate.pine}</pre></details>

      {!detail.compile ? (
        <div className="c2-compile-controls">
          <label>Safe compile/setup notes<Textarea variant="unstyled" aria-label="TradingView compile notes" value={compileNotes} maxLength={1000} onChange={(event) => setCompileNotes(event.target.value)} /></label>
          <div className="ps-actions">
            <Button variant="unstyled" className="ps-primary" disabled={!!busy} type="button" onClick={() => void run('Compile success', async () => { await recordC2CompileSuccess(conversion.id, compileNotes) })}><Check size={14} /> Record Compile Successful</Button>
          </div>
          <label>Sanitized compiler-error summary<Textarea variant="unstyled" aria-label="TradingView compiler error" value={compilerError} maxLength={1000} onChange={(event) => setCompilerError(event.target.value)} /></label>
          <Button variant="unstyled" className="ps-danger" disabled={!compilerError.trim() || !!busy} type="button" onClick={() => void run('Compile failure', async () => { await recordC2CompileFailure(conversion.id, compilerError) })}>Record Compile Failed</Button>
        </div>
      ) : detail.compile.result === 'FAILURE' ? (
        <div className="ps-message error">Compile failed: {detail.compile.compiler_error_summary}. Approve a new corrected candidate before retrying.</div>
      ) : null}

      {compiled ? (
        <>
          {!installations.length ? (
            <>
              <div className="c1-submit-grid">
                <label>Installation mode<NativeSelect variant="unstyled" aria-label="C2 installation mode" value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}><option value="SELF">SELF</option><option value="MANAGED">MANAGED</option></NativeSelect></label>
                <label>Owner<NativeSelect variant="unstyled" aria-label="C2 installation owner" value={ownerId} onChange={(event) => setOwnerId(event.target.value)}>{users.map((user) => <option key={user.id} value={user.id}>{user.name ?? user.email} · {user.email}</option>)}</NativeSelect></label>
                <label>Instance label<Input variant="unstyled" aria-label="C2 instance label" value={label} maxLength={120} onChange={(event) => setLabel(event.target.value)} /></label>
              </div>
              <Button variant="unstyled" className="ps-primary" type="button" disabled={!ownerId || !label.trim() || !!busy} onClick={() => void run('Installation creation', async () => {
                const created = await createC2Installation({ conversion_id: conversion.id, owner_user_id: ownerId, mode, instance_label: label })
                setSelectedId(created.installation.id)
              })}>Create Installation</Button>
            </>
          ) : <div className="ps-message success">The approved conversion is installed for its bound owner. Continue with credential and HOLD verification.</div>}

          {installations.length ? (
            <div className="pine-managed-grid">
              <aside className="ps-list">
                <div className="ps-list-toolbar"><Input variant="unstyled" aria-label="Search installations" placeholder="Search installations…" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
                {installations.filter((item) => item.instance_label.toLowerCase().includes(search.toLowerCase())).map((item) => <Button variant="unstyled" type="button" className={`ps-list-item${selectedId === item.id ? ' active' : ''}`} key={item.id} onClick={() => setSelectedId(item.id)}><strong>{item.instance_label}</strong><span>{item.mode} · {item.status.replaceAll('_', ' ')}</span><span>{item.credential_status} · HOLD {item.hold_status}</span></Button>)}
              </aside>
              {selected ? <div className="pine-managed-detail">
                <p><strong>Owner:</strong> {selected.owner_user_id}</p>
                <p><strong>Instance:</strong> {selected.strategy_instance_id}</p>

                {selected.live_market_paper_test_ready || selected.status === 'PAPER_VERIFICATION' || selected.status === 'READY' ? (
                  <div className="c2-verified-summary">
                    <p><span>Strategy routing</span><strong>VERIFIED</strong></p>
                    <p><span>HOLD connectivity</span><strong>PASSED</strong></p>
                    <p><span>Installed strategy</span><strong>{selected.strategy_name} · v{selected.strategy_version}</strong></p>
                    <p><span>Symbol</span><strong>{selected.symbol}</strong></p>
                    <p><span>Timeframe</span><strong>{selected.timeframe ?? 'Not recorded'}</strong></p>
                    <p><span>Paper verification</span><strong>{selected.status === 'PAPER_ELIGIBLE' ? 'READY' : selected.status.replaceAll('_', ' ')}</strong></p>
                  </div>
                ) : (
                  <p><strong>Paper:</strong> {selected.paper_eligible ? 'Eligible' : selected.blocking_reasons.join(', ')}</p>
                )}

                {selected.status === 'PAPER_VERIFICATION' ? (
                  <div className="c2-progress-tracker" aria-label="Live-market Paper test progress">
                    <p className="c2-progress-title">LIVE-MARKET PAPER TEST — LISTENING</p>
                    <p className="ps-note">Waiting for the next genuine TradingView strategy signal.</p>
                    <ol>
                      {selected.progress.map((step) => (
                        <li key={step.key} className={step.status === 'PASSED' ? 'is-passed' : 'is-waiting'}>
                          {step.label} — {step.status}
                        </li>
                      ))}
                    </ol>
                  </div>
                ) : null}

                <p><strong>Live:</strong> {selected.live_eligible ? 'Eligible after explicit Live start confirmation' : 'Complete Ready status and owner Live readiness first'}</p>
                <div className="ps-actions">
                  {selected.credential_status !== 'ACTIVE' ? <Button variant="unstyled" className="secondary-button" type="button" onClick={() => void run('Credential generation', async () => issue('generate'))}><KeyRound size={14} /> Generate Credential</Button> : <Button variant="unstyled" className="secondary-button" type="button" onClick={() => { if (window.confirm('Rotate this credential and require a new HOLD?')) void run('Credential rotation', async () => issue('rotate')) }}><RotateCw size={14} /> Rotate Credential</Button>}
                  {selected.credential_status === 'ACTIVE' ? <Button variant="unstyled" className="ps-danger" type="button" onClick={() => { if (window.confirm('Revoke this credential and remove Paper eligibility?')) void run('Credential revocation', async () => { await revokeAdminC2Credential(selected.id) }) }}><ShieldOff size={14} /> Revoke Credential</Button> : null}
                  {selected.live_market_paper_test_ready ? <Button variant="unstyled" className="ps-primary" disabled={!!busy} type="button" onClick={() => void run('Live-market Paper test start', async () => { await promoteAdminC2PaperVerification(selected.id) })}><Check size={14} /> Start Live-Market Paper Test</Button> : null}
                  {selected.status === 'PAPER_VERIFICATION' ? <Button variant="unstyled" className="ps-primary" disabled={!!busy || !selected.paper_entry_verified_at || !selected.paper_exit_verified_at} type="button" onClick={() => void run('Ready approval', async () => { await markAdminC2Ready(selected.id) })}><Check size={14} /> Mark Ready After Evidence</Button> : null}
                  {!selected.suspended_at ? <Button variant="unstyled" className="ps-danger" type="button" onClick={() => { if (window.confirm('Suspend this installation?')) void run('Installation suspension', async () => { await suspendAdminC2Installation(selected.id, 'Suspended by administrator') }) }}>Suspend Installation</Button> : null}
                </div>
              </div> : null}
            </div>
          ) : null}
        </>
      ) : null}

      {issued ? <div className="ps-secret-panel" role="dialog" aria-label="One-time C2 credential">
        <div><strong>Shown only now</strong><span>Copy the complete alert JSON before dismissing. NOVA stores only the credential hash.</span></div>
        <code>{revealed ? issued.token : '••••••••••••••••••••••••••••••••'}</code>
        <Button variant="unstyled" type="button" className="secondary-button" onClick={() => setRevealed((value) => !value)}>{revealed ? 'Hide' : 'Reveal'}</Button>
        <Button variant="unstyled" type="button" className="secondary-button" onClick={() => void navigator.clipboard.writeText(issued.setup_package.alert_message)}><Copy size={14} /> Copy alert JSON</Button>
        <Button variant="unstyled" type="button" className="ps-primary" onClick={() => { setIssued(null); setRevealed(false) }}>Dismiss permanently</Button>
      </div> : null}
    </section>
  )
}


function messageOf(reason: unknown) {
  return reason instanceof Error ? reason.message : 'Request failed.'
}
