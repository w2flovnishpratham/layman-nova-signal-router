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
import type { ConversationState } from '../../state/conversationMachine'
import { splitDraft, withRiskFields } from '../../setup/setupFields'
import { projectTranscript } from '../../state/conversationTranscript'
import { useConversationScroll } from '../../state/useConversationScroll'

interface Props {
  runtime: RuntimeStatus | null
  loading: boolean
  error: string
  onManage: (instanceId: string) => void
  onSelect: (strategyKey: string) => Promise<void>
  onSave: (strategyKey: string, values: Record<string, string | number>) => Promise<void>
  /** Reports machine state so the setup rail and configuration panel can
      project from the same source rather than tracking their own copy. */
  onStateChange?: (snapshot: { state: ConversationState; strategyName: string | null; strategyVersion: string | null }) => void
  /** Saves the engine safety settings (daily loss cap, trade cap, cooldown). */
  onSaveRisk?: (values: Record<string, string | number>) => Promise<void>
  onStart: (instanceId: string) => Promise<void>
  onUserReply: (text: string) => void
  /** Sync the backend when the machine picks a mode (setup.mode command + draft). */
  onModeSelect?: (mode: EngineMode, paperStartingBalance: number) => void
  /** Re-fetch the catalog/runtime after a load failure. */
  onRetry?: () => void
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

/** One interactive question rendered generically from the schema field. When
    editing, the current draft value is prefilled (keyed remount re-initialises). */
function ActiveQuestion({ field, currentValue, onCommit }: { field: StrategySetupField; currentValue?: unknown; onCommit: (value: string | number) => void }) {
  const prefill = currentValue !== undefined && currentValue !== null && currentValue !== '' ? String(currentValue) : ''
  const [value, setValue] = useState<string>(prefill || (field.default !== undefined ? String(field.default) : ''))
  const [err, setErr] = useState('')

  if (field.type === 'choice') {
    return (
      <div className="conv-question" role="group" aria-label={field.label}>
        <div className="conv-choices">
          {field.options.map((opt) => (
            <button key={opt} type="button" className={`conv-pill${prefill === opt ? ' conv-pill--current' : ''}`} aria-pressed={prefill === opt} onClick={() => onCommit(opt)}>
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
  runtime, loading, error, onSelect, onSave, onSaveRisk, onStart, onStateChange,
  onModeSelect, onRetry, liveAvailable = false, paperStartingBalance = 100000,
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

  useEffect(() => {
    onStateChange?.({
      state,
      strategyName: selectedStrategy?.name ?? null,
      strategyVersion: selectedStrategy?.version ?? null,
    })
  }, [state, selectedStrategy, onStateChange])

  const [pending, setPending] = useState<'idle' | 'saving' | 'starting'>('idle')
  const [saveError, setSaveError] = useState('')
  const [saved, setSaved] = useState(false)
  const [paperBalance, setPaperBalance] = useState(paperStartingBalance)
  // Synchronous guard so two rapid clicks (before the disabled state re-renders)
  // cannot fire a second save/start network request.
  const inFlightRef = useRef(false)
  // Generation + strategy at render time; a save/start captures these and ignores
  // its own resolution if the conversation moved on (mode/strategy change, edit,
  // reset) — a stale response can never mark saved, expose start, or set running.
  const genRef = useRef(state.generation)
  const strategyRef = useRef(state.strategyKey)
  useEffect(() => {
    genRef.current = state.generation
    strategyRef.current = state.strategyKey
  }, [state.generation, state.strategyKey])

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
    conv.selectStrategy(strat.strategy_key, withRiskFields(strat.setup_schema.fields), strat.saved_setup?.[state.mode] ?? {})
  }, [catalog, strategies, state.mode, conv])

  function pickMode(m: EngineMode) {
    if (m === 'live' && !liveAvailable) return // never advance when Live is blocked
    conv.selectMode(m)
    onModeSelect?.(m, paperBalance)
  }

  // Hooks must run before any early return. The transcript is a pure projection
  // of machine state; its length is the new-content signal for the scroll hook.
  const transcript = projectTranscript(state)
  const { ref: scrollRef, showJump, jumpToLatest } = useConversationScroll({ itemCount: transcript.length, reducedMotion })

  if (loading) {
    return <article className="setup-card catalog-state" role="status"><Loader2 className="strategy-card-spin" size={18} /> Loading strategy catalog…</article>
  }
  if (error) {
    return (
      <article className="setup-card catalog-state" role="alert">
        <AlertTriangle size={18} /> <span>{error}</span>
        {onRetry ? <button type="button" className="conv-pill" onClick={onRetry}>Retry</button> : null}
      </article>
    )
  }

  const fields = selectedStrategy ? withRiskFields(selectedStrategy.setup_schema.fields) : state.fields
  // A selected strategy that lost readiness, or that exposes no setup schema,
  // must halt progression — no review, save or start — with a truthful reason.
  const strategyUnavailable = !!selectedStrategy && !(selectedStrategy.availability === 'READY' && selectedStrategy.paper_eligible)
  const schemaMissing = !!state.strategyKey && applicableFields(fields).length === 0
  const setupBlocked = !!state.strategyKey && (strategyUnavailable || schemaMissing)

  async function pickStrategy(s: CatalogStrategy) {
    conv.selectStrategy(s.strategy_key, withRiskFields(s.setup_schema.fields), s.saved_setup?.[mode] ?? {})
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

  function isStale(gen: number, strategyKey: string | null): boolean {
    return genRef.current !== gen || strategyRef.current !== strategyKey
  }

  async function saveSetup() {
    if (!selectedStrategy || inFlightRef.current) return
    const gen = state.generation
    const strategyKey = state.strategyKey
    inFlightRef.current = true
    setPending('saving'); setSaveError('')
    try {
      const { strategy, risk } = splitDraft(state.draft)
      // Engine safety settings are runtime settings, not strategy setup, so they
      // go to their own endpoint. Save them first: if the strategy save fails the
      // user is left with tighter limits, never looser ones.
      if (Object.keys(risk).length > 0) await onSaveRisk?.(toSaveValues(risk))
      await onSave(selectedStrategy.strategy_key, toSaveValues(strategy))
      if (isStale(gen, strategyKey)) return // conversation moved on — ignore result
      setSaved(true)
    } catch (e) {
      if (isStale(gen, strategyKey)) return
      setSaved(false)
      setSaveError(e instanceof Error ? e.message : 'Setup could not be saved. Your previous configuration is unchanged.')
    } finally {
      inFlightRef.current = false
      setPending('idle')
    }
  }

  async function startEngine() {
    if (!selectedStrategy?.strategy_instance_id || !saved || inFlightRef.current) return
    const gen = state.generation
    const strategyKey = state.strategyKey
    inFlightRef.current = true
    setPending('starting')
    try {
      await onStart(selectedStrategy.strategy_instance_id)
      if (isStale(gen, strategyKey)) return // stale start cannot fabricate running state
      conv.startEngine()
    } catch (e) {
      if (isStale(gen, strategyKey)) return
      setSaveError(e instanceof Error ? e.message : 'Engine start failed.')
    } finally {
      inFlightRef.current = false
      setPending('idle')
    }
  }

  // Restrained live announcement: only the latest NOVA prompt, never the whole
  // transcript, so screen readers hear new questions without re-reading history.
  // Announcements are phrased distinctly from the visible NOVA bubbles so they
  // read naturally to a screen reader without colliding with the on-screen text.
  const announce = !state.mode
    ? 'NOVA is asking which trading mode to use.'
    : state.phase === 'SAVED_SETUP_FOUND'
      ? `NOVA found your previous ${mode === 'paper' ? 'Paper' : 'Live'} configuration. Resume, review, or start new.`
      : state.phase === 'QUESTION_ACTIVE' && state.activeQuestionKey
        ? `NOVA asks for ${labelFor(fields, state.activeQuestionKey)}.`
        : state.phase === 'SETUP_REVIEW' || state.phase === 'ENGINE_READY'
          ? 'NOVA setup is ready to review, save, and start.'
          : ''

  return (
    <div ref={scrollRef} className="conv-canvas">
      <div className="sr-only" role="status" aria-live="polite">{announce}</div>
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

      {setupBlocked ? (
        <div role="alert">
          <BotBubble tone="normal">
            {schemaMissing
              ? `${selectedStrategy?.name ?? 'This strategy'} can't be configured right now — it has no setup questions.`
              : (selectedStrategy?.disabled_reason ?? 'This strategy is currently unavailable, so setup is paused.')}
          </BotBubble>
          <div className="conv-actions">
            <button type="button" className="conv-pill" onClick={() => conv.selectMode(mode)}>Choose another strategy</button>
          </div>
        </div>
      ) : null}

      {!setupBlocked && state.phase === 'SAVED_SETUP_FOUND' ? (
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

      {transcript.filter((e) => e.type === 'user_answer').map((e) => (
        <div key={e.id}>
          <BotBubble>{String(e.payload.label)}?</BotBubble>
          <UserBubble text={`${String(e.payload.label)}: ${String(e.payload.value)}`} />
        </div>
      ))}

      {state.phase === 'ASSISTANT_TYPING' ? <TypingDots /> : null}

      {!setupBlocked && state.phase === 'QUESTION_ACTIVE' && state.activeQuestionKey ? (
        <>
          <BotBubble>{labelFor(fields, state.activeQuestionKey)}?</BotBubble>
          <ActiveQuestion
            key={`${state.activeQuestionKey}-${state.generation}`}
            field={fields.find((f) => f.key === state.activeQuestionKey)!}
            currentValue={state.draft[state.activeQuestionKey]}
            onCommit={commit}
          />
        </>
      ) : null}

      {!setupBlocked && (state.phase === 'SETUP_REVIEW' || state.phase === 'ENGINE_READY') ? (
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

      {showJump ? (
        <button type="button" className="conv-jump" onClick={jumpToLatest}>Jump to latest ↓</button>
      ) : null}
    </div>
  )
}
