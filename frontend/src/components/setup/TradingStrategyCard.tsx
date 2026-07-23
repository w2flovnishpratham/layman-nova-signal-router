import { AlertTriangle, Check, ChevronDown, Loader2, Play, Settings2 } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import type { EngineStrategy, RuntimeStatus } from '../../api'
import { blockerText } from '../../strategies/strategyBlockers'

interface Props {
  runtime: RuntimeStatus | null
  loading: boolean
  error: string
  onManage: (instanceId: string) => void
  onConfigure: (
    instanceId: string,
    lots: number,
    stopLossPercent: number,
    targetProfitPercent: number,
  ) => Promise<void>
  onSelect: (instanceId: string) => Promise<void>
  onStart: (instanceId: string) => Promise<void>
  onConfigureRequested?: () => void
  onChangeStrategy?: () => void
}

function numberSetting(settings: Record<string, unknown>, key: string, fallback: number): number {
  const value = Number(settings[key])
  return Number.isFinite(value) ? value : fallback
}

function credentialLabel(strategy: EngineStrategy): string {
  if (strategy.credential_status === 'active') return 'Credential active'
  if (strategy.credential_status === 'not_required') return 'Connection managed by NOVA'
  if (strategy.credential_status === 'revoked') return 'Credential revoked'
  return 'Credential missing'
}

function sourceLabel(strategy: EngineStrategy): string {
  return strategy.source_type === 'NOVA_SHARED' ? 'NOVA built-in' : 'Imported'
}

export function TradingStrategyCard({
  runtime,
  loading,
  error,
  onManage,
  onConfigure,
  onSelect,
  onStart,
  onConfigureRequested,
  onChangeStrategy,
}: Props) {
  const selected = runtime?.selected_strategy ?? null
  const paper = runtime?.config.paper ?? {}
  const [showChoices, setShowChoices] = useState(false)
  const [showConfig, setShowConfig] = useState(false)
  const [confirmStartOpen, setConfirmStartOpen] = useState(false)
  const [busy, setBusy] = useState('')
  const [actionError, setActionError] = useState('')
  const serverDraft = {
    instanceId: selected?.instance_id ?? '',
    lots: numberSetting(paper, 'configured_lots', selected?.lots ?? 1),
    stopLoss: numberSetting(paper, 'option_sl_percent', 10),
    takeProfit: numberSetting(paper, 'option_tp_percent', 20),
  }
  const [draft, setDraft] = useState(serverDraft)
  const editDraft = draft.instanceId === serverDraft.instanceId ? draft : serverDraft

  async function run(name: string, action: () => Promise<void>) {
    setBusy(name)
    setActionError('')
    try {
      await action()
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'The request could not be completed.')
    } finally {
      setBusy('')
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!selected) return
    await run('configure', async () => {
      await onConfigure(
        selected.instance_id,
        editDraft.lots,
        editDraft.stopLoss,
        editDraft.takeProfit,
      )
      setShowConfig(false)
    })
  }

  if (loading && !runtime) {
    return (
      <article className="setup-card trading-strategy-card trading-strategy-state" role="status">
        <Loader2 className="strategy-card-spin" size={18} />
        <span>Loading your selected strategy…</span>
      </article>
    )
  }

  if (error && !runtime) {
    return (
      <article className="setup-card trading-strategy-card trading-strategy-state" role="alert">
        <AlertTriangle size={18} />
        <div><strong>Strategy state unavailable</strong><span>{error}</span></div>
      </article>
    )
  }

  const switchingBlocked = Boolean(
    runtime && (runtime.engine.state !== 'STOPPED' || runtime.position.has_open_position),
  )
  const alternatives = (runtime?.eligible_strategies ?? []).filter(
    (strategy) => strategy.instance_id !== selected?.instance_id,
  )

  if (!selected) {
    const hasEligible = alternatives.length > 0
    return (
      <article className="setup-card trading-strategy-card">
        <div className="trading-strategy-empty">
          <AlertTriangle size={20} />
          <div>
            <strong>{hasEligible ? 'Select a Paper-ready strategy.' : 'No Paper-ready strategies available.'}</strong>
            <span>{hasEligible
              ? 'Choose one of your verified strategies below. Selection will not start the engine.'
              : 'Complete Pine conversion, TradingView installation and genuine HOLD verification before selecting a strategy.'}</span>
          </div>
        </div>
        {alternatives.length ? (
          <StrategyChoices
            strategies={alternatives}
            blocked={switchingBlocked}
            busy={busy}
            onSelect={(id) => void run(`select-${id}`, async () => {
              await onSelect(id)
              setShowChoices(false)
            })}
          />
        ) : null}
      </article>
    )
  }

  const ready = selected.selectable && selected.paper_eligible
  const engineStopped = runtime?.engine.state === 'STOPPED'
  const canStart = ready && engineStopped && !runtime?.position.has_open_position
  const configuredLots = numberSetting(paper, 'configured_lots', selected.lots)
  const configuredSl = numberSetting(paper, 'option_sl_percent', 10)
  const configuredTp = numberSetting(paper, 'option_tp_percent', 20)

  return (
    <article className="setup-card trading-strategy-card" data-strategy-instance-id={selected.instance_id}>
      <header className="trading-strategy-head">
        <div>
          <span className="trading-strategy-eyebrow"><Check size={12} /> Selected strategy</span>
          <h3>{selected.display_name}</h3>
          <p>
            {selected.strategy_version ? `Version ${selected.strategy_version} · ` : ''}
            {sourceLabel(selected)} · {selected.mode === 'paper' ? 'Paper' : 'Live'}
          </p>
        </div>
        <span className={`trading-readiness ${ready ? 'ready' : 'blocked'}`}>
          {ready ? 'Ready for Paper' : blockerText(selected.blocking_reason)}
        </span>
      </header>

      <dl className="trading-strategy-facts">
        <div><dt>Engine</dt><dd>{runtime?.engine.display ?? 'Unavailable'}</dd></div>
        <div><dt>Lots</dt><dd>{configuredLots}</dd></div>
        <div><dt>Stop loss</dt><dd>{configuredSl}%</dd></div>
        <div><dt>Take profit</dt><dd>{configuredTp}%</dd></div>
        <div><dt>Connection</dt><dd>{credentialLabel(selected)}</dd></div>
      </dl>

      {!ready ? (
        <div className="trading-strategy-warning" role="status">
          <AlertTriangle size={15} />
          <span>{blockerText(selected.blocking_reason)}. This selection will not start.</span>
        </div>
      ) : null}
      {switchingBlocked ? (
        <div className="trading-strategy-warning" role="status">
          <AlertTriangle size={15} />
          <span>Stop the engine and confirm the tracked position is flat before changing strategy.</span>
        </div>
      ) : null}
      {error || actionError ? <div className="trading-strategy-error" role="alert">{actionError || error}</div> : null}

      {showConfig ? (
        <form className="trading-strategy-config" onSubmit={(event) => void save(event)}>
          <span className="sr-only">Configure {selected.display_name}</span>
          <label>Lots<input aria-label="Paper lots" type="number" min={1} max={20} value={editDraft.lots} onChange={(event) => setDraft({ ...editDraft, lots: Number(event.target.value) })} /></label>
          <label>SL %<input aria-label="Paper stop loss" type="number" min={0} max={100} step="0.1" value={editDraft.stopLoss} onChange={(event) => setDraft({ ...editDraft, stopLoss: Number(event.target.value) })} /></label>
          <label>TP %<input aria-label="Paper take profit" type="number" min={0} max={1000} step="0.1" value={editDraft.takeProfit} onChange={(event) => setDraft({ ...editDraft, takeProfit: Number(event.target.value) })} /></label>
          <button type="submit" disabled={busy === 'configure' || switchingBlocked}>
            {busy === 'configure' ? 'Saving…' : 'Save Paper Settings'}
          </button>
        </form>
      ) : null}

      <div className="trading-strategy-actions">
        <button type="button" onClick={() => onManage(selected.instance_id)}><Settings2 size={14} /> Manage Strategy</button>
        <button
          type="button"
          disabled={switchingBlocked}
          onClick={() => onConfigureRequested ? onConfigureRequested() : setShowConfig((visible) => !visible)}
        >
          Configure Paper Settings
        </button>
        <button
          type="button"
          className="strategy-start"
          disabled={!canStart || busy === 'start'}
          onClick={() => setConfirmStartOpen(true)}
        >
          {busy === 'start' ? <Loader2 className="strategy-card-spin" size={14} /> : <Play size={14} />}
          {runtime?.engine.running ? 'Paper Engine Running' : 'Start Paper Engine'}
        </button>
        <button
          type="button"
          disabled={switchingBlocked || (!onChangeStrategy && alternatives.length === 0)}
          onClick={() => onChangeStrategy ? onChangeStrategy() : setShowChoices((visible) => !visible)}
        >
          Change Strategy <ChevronDown size={14} />
        </button>
      </div>

      {confirmStartOpen && canStart ? (
        <section className="strategy-start-confirm" role="dialog" aria-modal="true" aria-label="Confirm selected strategy start">
          <div>
            <span>NOVA plans to start</span>
            <strong>{selected.display_name}</strong>
            <small>{selected.strategy_version ? `Version ${selected.strategy_version} · ` : ''}Paper mode</small>
          </div>
          <dl>
            <div><dt>Lots</dt><dd>{configuredLots}</dd></div>
            <div><dt>Stop loss</dt><dd>{configuredSl}%</dd></div>
            <div><dt>Take profit</dt><dd>{configuredTp}%</dd></div>
          </dl>
          <div className="strategy-start-confirm-actions">
            <button type="button" className="strategy-start" disabled={busy === 'start'} onClick={() => void run('start', async () => {
              await onStart(selected.instance_id)
              setConfirmStartOpen(false)
            })}>
              {busy === 'start' ? <Loader2 className="strategy-card-spin" size={14} /> : <Play size={14} />}
              Confirm and Start
            </button>
            <button type="button" onClick={() => { setConfirmStartOpen(false); setShowConfig(true) }}>Edit Settings</button>
            <button type="button" onClick={() => setConfirmStartOpen(false)}>Cancel</button>
          </div>
        </section>
      ) : null}

      {showChoices && !onChangeStrategy ? (
        <StrategyChoices
          strategies={alternatives}
          blocked={switchingBlocked}
          busy={busy}
          onSelect={(id) => void run(`select-${id}`, async () => {
            await onSelect(id)
            setShowChoices(false)
          })}
        />
      ) : null}
    </article>
  )
}

function StrategyChoices({
  strategies,
  blocked,
  busy,
  onSelect,
}: {
  strategies: EngineStrategy[]
  blocked: boolean
  busy: string
  onSelect: (instanceId: string) => void
}) {
  return (
    <div className="trading-strategy-choices" aria-label="Paper-ready strategies">
      {strategies.map((strategy) => (
        <button
          key={strategy.instance_id}
          type="button"
          disabled={blocked || busy === `select-${strategy.instance_id}`}
          onClick={() => onSelect(strategy.instance_id)}
        >
          <span><strong>{strategy.display_name}</strong><small>Ready for Paper · {strategy.lots} lot{strategy.lots === 1 ? '' : 's'}</small></span>
          <span>Select</span>
        </button>
      ))}
    </div>
  )
}
