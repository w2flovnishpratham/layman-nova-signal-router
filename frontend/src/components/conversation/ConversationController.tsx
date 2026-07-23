import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Check, Loader2, Pencil } from 'lucide-react'
import type { CatalogStrategy, RuntimeStatus, StrategySetupField } from '../../api'
import type { EngineMode } from '../../types'
import { BotBubble } from '../messages/BotBubble'
import { UserBubble } from '../messages/UserBubble'
import { TypingDots } from '../TypingDots'
import { useAppReducedMotion } from '../MotionPrimitives'
import { useConversation } from '../../state/useConversation'
import { applicableFields, type SetupValues } from '../../state/conversationMachine'

interface Props {
  runtime: RuntimeStatus | null
  loading: boolean
  error: string
  onManage: (instanceId: string) => void
  onSelect: (strategyKey: string) => Promise<void>
  onSave: (strategyKey: string, values: Record<string, string | number>) => Promise<void>
  onStart: (instanceId: string) => Promise<void>
  onUserReply: (text: string) => void
  /** Sync the backend when the machine picks a mode (setup.mode command + draft). */
  onModeSelect?: (mode: EngineMode, paperStartingBalance: number) => void
  liveAvailable?: boolean
  paperStartingBalance?: number
  strategyPromptPresent?: boolean
}

function labelFor(fields: StrategySetupField[], key: string): string {
  return fields.find((f) => f.key === key)?.label ?? key
}

function toSaveValues(draft: SetupValues): Record<string, string | number> {
  const out: Record<string, string | number> = {}
  for (const [k, v] of Object.entries(draft)) {
    if (typeof v === 'string' || typeof v === 'number') out[k] = v
  }
  return out
}

/** One interactive question rendered generically from the schema field. */
function ActiveQuestion({ field, onCommit }: { field: StrategySetupField; onCommit: (value: string | number) => void }) {
  const [value, setValue] = useState<string>(field.default !== undefined ? String(field.default) : '')
  const [err, setErr] = useState('')

  if (field.type === 'choice') {
    return (
      <div className="conv-question" role="group" aria-label={field.label}>
        <div className="conv-choices">
          {field.options.map((opt) => (
            <button key={opt} type="button" className="conv-pill" onClick={() => onCommit(opt)}>
              {opt}
            </button>
          ))}
        </div>
      </div>
    )
  }

  const commitNumber = () => {
    const num = Number(value)
    if (!Number.isFinite(num) || num < field.minimum || num > field.maximum || (field.type === 'integer' && !Number.isInteger(num))) {
      setErr(`Enter ${field.type === 'integer' ? 'a whole number' : 'a value'} between ${field.minimum} and ${field.maximum}.`)
      return
    }
    setErr('')
    onCommit(field.type === 'integer' ? Math.trunc(num) : num)
  }

  return (
    <div className="conv-question">
      <div className="conv-numeric">
        <label className="sr-only" htmlFor={`q-${field.key}`}>{field.label}</label>
        <input
          id={`q-${field.key}`}
          type="number"
          inputMode="decimal"
          min={field.minimum}
          max={field.maximum}
          step={field.type === 'integer' ? 1 : 'any'}
          value={value}
          aria-invalid={err ? true : undefined}
          aria-describedby={err ? `q-${field.key}-err` : undefined}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') commitNumber() }}
        />
        <button type="button" className="conv-pill conv-pill--primary" onClick={commitNumber}>Confirm</button>
        {field.default !== undefined ? (
          <span className="conv-suggestion">Suggested: {field.default}</span>
        ) : null}
      </div>
      {err ? <p id={`q-${field.key}-err`} className="conv-error" role="alert">{err}</p> : null}
    </div>
  )
}

function StrategyGroup({ title, strategies, onPick }: { title: string; strategies: CatalogStrategy[]; onPick: (s: CatalogStrategy) => void }) {
  if (strategies.length === 0) return null
  return (
    <section className="conv-strategy-group">
      <h3 className="conv-group-title">{title}</h3>
      {strategies.map((s) => {
        const usable = s.availability === 'READY' && s.paper_eligible
        return (
          <button
            key={s.strategy_key}
            type="button"
            className="conv-strategy-card"
            disabled={!usable}
            onClick={() => usable && onPick(s)}
          >
            <span className="conv-strategy-name">{s.name}{s.version ? ` · v${s.version}` : ''}</span>
            <span className="conv-strategy-desc">{s.description}</span>
            <span className="conv-strategy-meta">
              {usable ? `Paper ready${s.live_eligible ? ' · Live eligible' : ''}` : (s.disabled_reason ?? s.availability)}
            </span>
          </button>
        )
      })}
    </section>
  )
}

export function ConversationController({
  runtime, loading, error, onSelect, onSave, onStart,
  onModeSelect, liveAvailable = false, paperStartingBalance = 100000,
}: Props) {
  const reducedMotion = useAppReducedMotion()
  const conv = useConversation({ reducedMotion })
  const { state } = conv

  const catalog = runtime?.strategy_catalog
  const mode: EngineMode = state.mode ?? (catalog?.setup_progress.mode ?? 'paper') as EngineMode
  const strategies = useMemo(() => catalog?.strategies ?? [], [catalog])
  const selectedStrategy = useMemo(
    () => strategies.find((s) => s.strategy_key === state.strategyKey) ?? null,
    [strategies, state.strategyKey],
  )

  const [pending, setPending] = useState<'idle' | 'saving' | 'starting'>('idle')
  const [saveError, setSaveError] = useState('')
  const [saved, setSaved] = useState(false)
  const [paperBalance, setPaperBalance] = useState(paperStartingBalance)

  // Deterministic hydration: if the backend already established a mode (resume /
  // refresh), reflect it in the machine once. Otherwise the machine stays at mode
  // selection so the user picks it here (one controller owns mode + strategy).
  const backendMode = catalog?.setup_progress.mode ?? null
  const initedMode = useRef(false)
  useEffect(() => {
    if (!backendMode || state.mode || initedMode.current) return
    initedMode.current = true
    conv.selectMode(backendMode)
  }, [backendMode, state.mode, conv])

  // One-time deterministic initialization from the backend-selected strategy,
  // once a mode is known (mode determines which saved_setup applies).
  const initedFor = useRef<string | null>(null)
  useEffect(() => {
    if (!catalog || !state.mode) return
    const key = catalog.selected_strategy_key
    if (!key || initedFor.current === key) return
    const strat = strategies.find((s) => s.strategy_key === key)
    if (!strat) return
    initedFor.current = key
    conv.selectStrategy(strat.strategy_key, strat.setup_schema.fields, strat.saved_setup?.[state.mode] ?? {})
  }, [catalog, strategies, state.mode, conv])

  function pickMode(m: EngineMode) {
    if (m === 'live' && !liveAvailable) return // never advance when Live is blocked
    conv.selectMode(m)
    onModeSelect?.(m, paperBalance)
  }

  if (loading) {
    return <article className="setup-card catalog-state" role="status"><Loader2 className="strategy-card-spin" size={18} /> Loading strategy catalog…</article>
  }
  if (error) {
    return <article className="setup-card catalog-state" role="alert"><AlertTriangle size={18} /> <span>{error}</span></article>
  }

  const fields = selectedStrategy?.setup_schema.fields ?? state.fields

  async function pickStrategy(s: CatalogStrategy) {
    conv.selectStrategy(s.strategy_key, s.setup_schema.fields, s.saved_setup?.[mode] ?? {})
    initedFor.current = s.strategy_key
    try {
      await onSelect(s.strategy_key)
    } catch {
      /* selection error surfaces via the parent runtime/error props */
    }
  }

  function commit(value: string | number) {
    if (!state.activeQuestionKey) return
    conv.commitAnswer(state.activeQuestionKey, value)
  }

  async function saveSetup() {
    if (!selectedStrategy || pending !== 'idle') return
    setPending('saving'); setSaveError('')
    try {
      await onSave(selectedStrategy.strategy_key, toSaveValues(state.draft))
      setSaved(true)
    } catch (e) {
      setSaved(false)
      setSaveError(e instanceof Error ? e.message : 'Setup could not be saved. Your previous configuration is unchanged.')
    } finally {
      setPending('idle')
    }
  }

  async function startEngine() {
    if (!selectedStrategy?.strategy_instance_id || !saved || pending !== 'idle') return
    setPending('starting')
    try {
      await onStart(selectedStrategy.strategy_instance_id)
      conv.startEngine()
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Engine start failed.')
    } finally {
      setPending('idle')
    }
  }

  // --- Transcript projection (deterministic from machine state) -------------
  // Answered questions (before the active one) render as NOVA-question + user-answer pairs.
  const answered = applicableFields(fields).filter((f) => f.key in state.draft && f.key !== state.activeQuestionKey)

  return (
    <div className="conv-canvas" aria-live="polite">
      {!state.mode ? (
        <>
          <BotBubble>How should NOVA trade for you?</BotBubble>
          <div className="conv-mode-grid">
            <section className="conv-mode-card">
              <div className="conv-mode-head"><strong>Paper</strong><span className="conv-mode-badge">Recommended</span></div>
              <p>Real market data, simulated fills, virtual balance. No real orders.</p>
              <label className="conv-mode-balance">Virtual starting balance
                <input
                  type="number" min={10000} max={1000000} step={10000} value={paperBalance}
                  onChange={(e) => setPaperBalance(Math.min(1000000, Math.max(10000, Number(e.target.value) || 10000)))}
                />
              </label>
              <button type="button" className="conv-pill conv-pill--primary" onClick={() => pickMode('paper')}>Start in Paper</button>
            </section>
            <section className="conv-mode-card">
              <div className="conv-mode-head"><strong>Live</strong></div>
              <p>{liveAvailable ? 'Routes real orders to Dhan. Real money is at risk; static IP required.' : 'Live is unavailable — execution gates are not enabled on this environment.'}</p>
              <button type="button" className="conv-pill" disabled={!liveAvailable} aria-disabled={!liveAvailable} onClick={() => pickMode('live')}>
                {liveAvailable ? 'Configure Live' : 'Live unavailable'}
              </button>
            </section>
          </div>
        </>
      ) : null}

      {state.mode && (state.phase === 'STRATEGY_SELECTION' || !state.strategyKey) ? (
        <>
          <BotBubble>Which strategy should NOVA run?</BotBubble>
          <StrategyGroup title="NOVA Strategies" strategies={strategies.filter((s) => s.source_type === 'BUILT_IN')} onPick={pickStrategy} />
          <StrategyGroup title="My Strategies" strategies={strategies.filter((s) => s.source_type === 'IMPORTED')} onPick={pickStrategy} />
        </>
      ) : null}

      {state.phase === 'SAVED_SETUP_FOUND' ? (
        <>
          <BotBubble>I found your previous {mode === 'paper' ? 'Paper' : 'Live'} configuration.</BotBubble>
          <div className="conv-saved-summary" role="group" aria-label="Saved setup summary">
            {applicableFields(fields).filter((f) => f.key in state.saved).map((f) => (
              <div key={f.key} className="conv-summary-row"><span>{f.label}</span><strong>{String(state.saved[f.key])}</strong></div>
            ))}
          </div>
          <div className="conv-actions">
            <button type="button" className="conv-pill conv-pill--primary" onClick={conv.resume}>Resume</button>
            <button type="button" className="conv-pill" onClick={conv.review}>Review</button>
            <button type="button" className="conv-pill" onClick={conv.startNew}>Start New Setup</button>
          </div>
        </>
      ) : null}

      {answered.map((f) => (
        <div key={f.key}>
          <BotBubble>{f.label}?</BotBubble>
          <UserBubble text={`${f.label}: ${String(state.draft[f.key])}`} />
        </div>
      ))}

      {state.phase === 'ASSISTANT_TYPING' ? <TypingDots /> : null}

      {state.phase === 'QUESTION_ACTIVE' && state.activeQuestionKey ? (
        <>
          <BotBubble>{labelFor(fields, state.activeQuestionKey)}?</BotBubble>
          <ActiveQuestion
            key={`${state.activeQuestionKey}-${state.generation}`}
            field={fields.find((f) => f.key === state.activeQuestionKey)!}
            onCommit={commit}
          />
        </>
      ) : null}

      {state.phase === 'SETUP_REVIEW' || state.phase === 'ENGINE_READY' ? (
        <div className="conv-review" role="group" aria-label="Setup review">
          <BotBubble>Here is your {selectedStrategy?.name ?? 'strategy'} configuration. Review, then save and start.</BotBubble>
          <div className="conv-saved-summary">
            {applicableFields(fields).filter((f) => f.key in state.draft).map((f) => (
              <div key={f.key} className="conv-summary-row">
                <span>{f.label}</span>
                <strong>{String(state.draft[f.key])}</strong>
                <button type="button" className="conv-edit" aria-label={`Edit ${f.label}`} onClick={() => { setSaved(false); conv.editAnswer(f.key) }}>
                  <Pencil size={13} />
                </button>
              </div>
            ))}
          </div>
          {saveError ? <p className="conv-error" role="alert">{saveError}</p> : null}
          <div className="conv-actions">
            {!saved ? (
              <button type="button" className="conv-pill conv-pill--primary" disabled={pending !== 'idle'} onClick={saveSetup}>
                {pending === 'saving' ? 'Saving…' : 'Save setup'}
              </button>
            ) : (
              <button type="button" className="conv-pill conv-pill--primary" disabled={pending !== 'idle'} onClick={startEngine}>
                {pending === 'starting' ? 'Starting…' : 'Start engine'}
              </button>
            )}
            {saved ? <span className="conv-saved-badge"><Check size={14} /> Setup saved</span> : null}
          </div>
        </div>
      ) : null}
    </div>
  )
}
