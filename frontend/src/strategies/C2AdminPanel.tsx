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
  const [issued, setIssued] = useState<C2IssuedCredential | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
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
    setBusy(name); setMessage(''); setError('')
    try {
      await action()
      setMessage(`${name} completed.`)
      await refresh()
    } catch (reason) {
      setError(messageOf(reason))
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
      {message ? <div className="ps-message success" role="status">{message}</div> : null}

      <div className="c1-provenance">
        <strong>Exact approved candidate</strong>
        <span>Candidate SHA: {detail.candidate.candidate_sha256}</span>
        <span>Source SHA: {detail.candidate.source_sha256}</span>
        <span>Prompt {detail.candidate.prompt_version} · Transport {detail.candidate.transport_version}</span>
      </div>
      <div className="ps-actions">
        <button className="secondary-button" type="button" onClick={() => void navigator.clipboard.writeText(detail.candidate.pine)}><Copy size={14} /> Copy approved Pine</button>
        <button className="secondary-button" type="button" onClick={() => void downloadPine()}><Download size={14} /> Download approved Pine</button>
      </div>
      <details><summary>View exact approved Pine</summary><pre className="pine-review-source">{detail.candidate.pine}</pre></details>

      {!detail.compile ? (
        <div className="c2-compile-controls">
          <label>Safe compile/setup notes<textarea aria-label="TradingView compile notes" value={compileNotes} maxLength={1000} onChange={(event) => setCompileNotes(event.target.value)} /></label>
          <div className="ps-actions">
            <button className="ps-primary" disabled={!!busy} type="button" onClick={() => void run('Compile success', async () => { await recordC2CompileSuccess(conversion.id, compileNotes) })}><Check size={14} /> Record Compile Successful</button>
          </div>
          <label>Sanitized compiler-error summary<textarea aria-label="TradingView compiler error" value={compilerError} maxLength={1000} onChange={(event) => setCompilerError(event.target.value)} /></label>
          <button className="ps-danger" disabled={!compilerError.trim() || !!busy} type="button" onClick={() => void run('Compile failure', async () => { await recordC2CompileFailure(conversion.id, compilerError) })}>Record Compile Failed</button>
        </div>
      ) : detail.compile.result === 'FAILURE' ? (
        <div className="ps-message error">Compile failed: {detail.compile.compiler_error_summary}. Approve a new corrected candidate before retrying.</div>
      ) : null}

      {compiled ? (
        <>
          {!installations.length ? (
            <>
              <div className="c1-submit-grid">
                <label>Installation mode<select aria-label="C2 installation mode" value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}><option value="SELF">SELF</option><option value="MANAGED">MANAGED</option></select></label>
                <label>Owner<select aria-label="C2 installation owner" value={ownerId} onChange={(event) => setOwnerId(event.target.value)}>{users.map((user) => <option key={user.id} value={user.id}>{user.name ?? user.email} · {user.email}</option>)}</select></label>
                <label>Instance label<input aria-label="C2 instance label" value={label} maxLength={120} onChange={(event) => setLabel(event.target.value)} /></label>
              </div>
              <button className="ps-primary" type="button" disabled={!ownerId || !label.trim() || !!busy} onClick={() => void run('Installation creation', async () => {
                const created = await createC2Installation({ conversion_id: conversion.id, owner_user_id: ownerId, mode, instance_label: label })
                setSelectedId(created.installation.id)
              })}>Create Installation</button>
            </>
          ) : <div className="ps-message success">The approved conversion is installed for its bound owner. Continue with credential and HOLD verification.</div>}

          {installations.length ? (
            <div className="pine-managed-grid">
              <aside className="ps-list">{installations.map((item) => <button type="button" className={`ps-list-item${selectedId === item.id ? ' active' : ''}`} key={item.id} onClick={() => setSelectedId(item.id)}><strong>{item.instance_label}</strong><span>{item.mode} · {item.status.replaceAll('_', ' ')}</span><span>{item.credential_status} · HOLD {item.hold_status}</span></button>)}</aside>
              {selected ? <div className="pine-managed-detail">
                <p><strong>Owner:</strong> {selected.owner_user_id}</p>
                <p><strong>Instance:</strong> {selected.strategy_instance_id}</p>
                <p><strong>Paper:</strong> {selected.paper_eligible ? 'Eligible' : selected.blocking_reasons.join(', ')}</p>
                <p><strong>Execution gate:</strong> {selected.status.replaceAll('_', ' ')}</p>
                <p><strong>Paper evidence:</strong> {selected.paper_entry_verified_at ? 'Entry confirmed' : 'Entry pending'} · {selected.paper_exit_verified_at ? 'Exit confirmed' : 'Exit pending'}</p>
                <p><strong>Live:</strong> {selected.live_eligible ? 'Eligible after explicit Live start confirmation' : 'Complete Ready status and owner Live readiness first'}</p>
                <div className="ps-actions">
                  {selected.credential_status !== 'ACTIVE' ? <button className="secondary-button" type="button" onClick={() => void run('Credential generation', async () => issue('generate'))}><KeyRound size={14} /> Generate Credential</button> : <button className="secondary-button" type="button" onClick={() => { if (window.confirm('Rotate this credential and require a new HOLD?')) void run('Credential rotation', async () => issue('rotate')) }}><RotateCw size={14} /> Rotate Credential</button>}
                  {selected.credential_status === 'ACTIVE' ? <button className="ps-danger" type="button" onClick={() => { if (window.confirm('Revoke this credential and remove Paper eligibility?')) void run('Credential revocation', async () => { await revokeAdminC2Credential(selected.id) }) }}><ShieldOff size={14} /> Revoke Credential</button> : null}
                  {selected.status === 'PAPER_ELIGIBLE' && selected.hold_status === 'VERIFIED' ? <button className="ps-primary" disabled={!!busy} type="button" onClick={() => void run('Paper verification promotion', async () => { await promoteAdminC2PaperVerification(selected.id) })}><Check size={14} /> Start Controlled Paper Verification</button> : null}
                  {selected.status === 'PAPER_VERIFICATION' ? <button className="ps-primary" disabled={!!busy || !selected.paper_entry_verified_at || !selected.paper_exit_verified_at} type="button" onClick={() => void run('Ready approval', async () => { await markAdminC2Ready(selected.id) })}><Check size={14} /> Mark Ready After Evidence</button> : null}
                  {!selected.suspended_at ? <button className="ps-danger" type="button" onClick={() => { if (window.confirm('Suspend this installation?')) void run('Installation suspension', async () => { await suspendAdminC2Installation(selected.id, 'Suspended by administrator') }) }}>Suspend Installation</button> : null}
                </div>
              </div> : null}
            </div>
          ) : null}
        </>
      ) : null}

      {issued ? <div className="ps-secret-panel" role="dialog" aria-label="One-time C2 credential">
        <div><strong>Shown only now</strong><span>Copy the complete alert JSON before dismissing. NOVA stores only the credential hash.</span></div>
        <code>{revealed ? issued.token : '••••••••••••••••••••••••••••••••'}</code>
        <button type="button" className="secondary-button" onClick={() => setRevealed((value) => !value)}>{revealed ? 'Hide' : 'Reveal'}</button>
        <button type="button" className="secondary-button" onClick={() => void navigator.clipboard.writeText(issued.setup_package.alert_message)}><Copy size={14} /> Copy alert JSON</button>
        <button type="button" className="ps-primary" onClick={() => { setIssued(null); setRevealed(false) }}>Dismiss permanently</button>
      </div> : null}
    </section>
  )
}


function messageOf(reason: unknown) {
  return reason instanceof Error ? reason.message : 'Request failed.'
}
