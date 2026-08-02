import { Input } from "@/components/ui/input"
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from '@/components/ui/popover'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Check, Loader2, Pencil } from 'lucide-react'
import { createRazorpaySubscription, getPaymentEntitlementStatus } from '../../api'
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
import { getRiskConfiguration, type RiskConfiguration } from '../../risk/riskApi'
import { projectTranscript } from '../../state/conversationTranscript'
import { useConversationScroll } from '../../state/useConversationScroll'

interface Props {
  runtime: RuntimeStatus | null
  loading: boolean
  error: string
  onManage: (instanceId: string) => void
  onSelect: (strategyKey: string) => Promise<void>
  onSave: (
    strategyKey: string,
    values: Record<string, string | number>,
    risk: Record<string, string | number>,
  ) => Promise<void>
  /** Reports machine state so the setup rail and configuration panel can
      project from the same source rather than tracking their own copy. */
  onStateChange?: (snapshot: {
    state: ConversationState
    strategyName: string | null
    strategyVersion: string | null
    savedComplete: boolean
  }) => void
  onStart: (instanceId: string, liveAcknowledged: boolean) => Promise<void>
  onUserReply: (text: string) => void
  /** Sync the backend when the machine picks a mode (setup.mode command + draft). */
  onModeSelect?: (mode: EngineMode, paperStartingBalance: number) => void
  /** Re-fetch the catalog/runtime after a load failure. */
  onRetry?: () => void
  liveAvailable?: boolean
  paperStartingBalance?: number
  strategyPromptPresent?: boolean
}

/** Persists across a refresh (sessionStorage, not the backend) so a mid-flow
    reload after "Start New Setup" re-enters the fresh flow instead of the old
    saved setup — see the restore effect in ConversationController. Scoped to
    the owner_user_id at the moment it was set: sessionStorage survives a
    same-tab logout/login (App unmounts/remounts this subtree without a page
    reload), so an unscoped flag would leak owner A's "start new" intent into
    owner B's first render. */
const FRESH_START_KEY = 'nova.setup.freshStart'

function setFreshStartFlag(ownerId: string | undefined): void {
  if (!ownerId) return
  sessionStorage.setItem(FRESH_START_KEY, JSON.stringify({ ownerId }))
}

function clearFreshStartFlag(): void {
  sessionStorage.removeItem(FRESH_START_KEY)
}

function isFreshStartFlagged(ownerId: string | undefined): boolean {
  if (!ownerId) return false
  try {
    const raw = sessionStorage.getItem(FRESH_START_KEY)
    if (!raw) return false
    const parsed = JSON.parse(raw) as { ownerId?: string }
    return parsed.ownerId === ownerId
  } catch {
    return false
  }
}

function labelFor(fields: StrategySetupField[], key: string): string {
  return fields.find((f) => f.key === key)?.label ?? key
}

const QUESTION_COPY: Record<string, string> = {
  direction: 'Which signals should NOVA trade?',
  lots: 'How many lots should be used?',
  max_daily_loss: "Now the safety net. What's your max daily loss? The engine hard-stops and squares off if it's hit.",
  max_trades_per_day: 'How many trades should NOVA take at most each day?',
}

function questionFor(fields: StrategySetupField[], key: string): string {
  const copy = QUESTION_COPY[key] ?? labelFor(fields, key)
  return /[?.!]$/.test(copy) ? copy : `${copy}?`
}

function answerFor(key: string | undefined, value: unknown): string {
  if (key === 'max_daily_loss') return `₹${Number(value).toLocaleString('en-IN')}`
  if (key === 'max_trades_per_day') return `${value} trades a day`
  if (key === 'lots') return `${value} lot${Number(value) === 1 ? '' : 's'}`
  if (key === 'entry_cutoff_ist' && value !== 'No cutoff') return `${value} IST`
  if (key?.endsWith('_percent')) return `${value}%`
  return String(value)
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
            <Button variant="unstyled" key={opt} type="button" className={`conv-pill${prefill === opt ? ' conv-pill--current' : ''}`} aria-pressed={prefill === opt} onClick={() => onCommit(opt)}>
              {opt}
            </Button>
          ))}
        </div>
      </div>
    )
  }

  if (field.key === 'max_daily_loss') {
    return (
      <div className="conv-question" role="group" aria-label={field.label}>
        <div className="conv-choices">
          {[25000, 10000, 50000].map((amount, index) => (
            <Button
              variant="unstyled"
              key={amount}
              type="button"
              className={`conv-pill${index === 0 ? ' conv-pill--primary' : ''}`}
              onClick={() => onCommit(amount)}
            >
              ₹{amount.toLocaleString('en-IN')}
            </Button>
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
        <Input variant="unstyled"
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
        <Button variant="unstyled" type="button" className="conv-pill conv-pill--primary" onClick={commitNumber}>Confirm</Button>
        {field.default !== undefined ? (
          <span className="conv-suggestion">Suggested: {field.default}</span>
        ) : null}
      </div>
      {err ? <p id={`q-${field.key}-err`} className="conv-error" role="alert">{err}</p> : null}
    </div>
  )
}

function InlineReviewEdit({ field, value, onCommit }: {
  field: StrategySetupField
  value: unknown
  onCommit: (value: string | number) => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={<Button variant="unstyled" type="button" className="conv-edit" aria-label={`Edit ${field.label}`} />}
      >
        <Pencil size={13} />
      </PopoverTrigger>
      <PopoverContent className="conv-edit-popover" align="end" side="left" sideOffset={10}>
        <PopoverTitle>Edit {field.label}</PopoverTitle>
        <ActiveQuestion
          key={`${field.key}-${String(value)}-${open}`}
          field={field}
          currentValue={value}
          onCommit={(next) => { onCommit(next); setOpen(false) }}
        />
      </PopoverContent>
    </Popover>
  )
}

function StrategyGroup({ title, strategies, mode, onPick }: {
  title: string
  strategies: CatalogStrategy[]
  mode: EngineMode
  onPick: (s: CatalogStrategy) => void
}) {
  if (strategies.length === 0) return null
  return (
    <section className="conv-strategy-group">
      <h3 className="conv-group-title">{title}</h3>
      <div className="conv-strategy-row">
        {strategies.map((s) => {
          const usable = s.availability === 'READY'
            && (mode === 'live' ? s.live_eligible : s.paper_eligible)
          return (
            <Button variant="unstyled"
              key={s.strategy_key}
              type="button"
              className="conv-strategy-card"
              disabled={!usable}
              onClick={() => usable && onPick(s)}
            >
              <span className="conv-strategy-name">{s.name}{s.version ? ` · v${s.version}` : ''}</span>
              <span className="sr-only">{s.description}. </span>
              <span className="sr-only">
                {usable ? `Paper ready${s.live_eligible ? ' · Live eligible' : ''}` : (s.disabled_reason ?? s.availability)}
              </span>
            </Button>
          )
        })}
      </div>
    </section>
  )
}

export function ConversationController({
  runtime, loading, error, onSelect, onSave, onStart, onStateChange,
  onModeSelect, onRetry, liveAvailable = false,
}: Props) {
  const reducedMotion = useAppReducedMotion()
  const conv = useConversation({ reducedMotion })
  const { state } = conv

  const catalog = runtime?.strategy_catalog
  const mode = state.mode
  const strategies = useMemo(() => catalog?.strategies ?? [], [catalog])
  const selectedStrategy = useMemo(
    () => strategies.find((s) => s.strategy_key === state.strategyKey) ?? null,
    [strategies, state.strategyKey],
  )

  const [pending, setPending] = useState<'idle' | 'saving' | 'starting'>('idle')
  const [saveError, setSaveError] = useState('')
  const [saved, setSaved] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [liveAcknowledged, setLiveAcknowledged] = useState(false)
  const [sharedRisk, setSharedRisk] = useState<RiskConfiguration | null>(null)
  const [paperEntitled, setPaperEntitled] = useState(true)
  const [paywallOpen, setPaywallOpen] = useState(false)
  const [checkoutPending, setCheckoutPending] = useState(false)
  const [checkoutError, setCheckoutError] = useState('')
  const [checkoutStarted, setCheckoutStarted] = useState(false)
  const restoredRevisionRef = useRef<string | null>(null)
  // Once the user has explicitly interacted this mount (picked a mode,
  // picked a strategy, clicked Start New/Resume/Review), the restore effect
  // below must never fire again for the rest of this mount. Without this,
  // picking a mode that happens to match the backend's last-persisted mode
  // makes the effect re-run (it depends on state.mode) and auto-select the
  // last-saved strategy+config, landing back on the saved-setup decision
  // card the user never asked for -- the exact Start-New/Resume loop.
  const userInteractedRef = useRef(false)
  const setupSaved = saved || Boolean(
    !dirty
    && runtime?.selected_configuration
    && state.strategyKey
    && state.mode === runtime?.selected_configuration?.mode,
  )

  // Nothing in the draft persists server-side until an explicit Save, so a
  // refresh or tab close mid-setup would otherwise silently discard answers
  // with no warning. beforeunload is the one reliable native hook for this;
  // it doesn't cover in-app navigation (Dashboard/Strategies), which is a
  // known gap rather than something this warns about.
  useEffect(() => {
    if (!dirty || setupSaved) return
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty, setupSaved])

  useEffect(() => {
    if (!mode) {
      setSharedRisk(null)
      return
    }
    let current = true
    getRiskConfiguration(mode)
      .then((configuration) => { if (current) setSharedRisk(configuration) })
      .catch(() => { if (current) setSharedRisk(null) })
    return () => { current = false }
  }, [mode])

  const riskDefaults = useMemo(() => sharedRisk ? {
    max_daily_loss: sharedRisk.values.daily_loss_cap,
    max_trades_per_day: sharedRisk.values.max_trades_per_day,
  } : {}, [sharedRisk])

  const refreshPaperEntitlement = useCallback(async () => {
    try {
      const status = await getPaymentEntitlementStatus()
      // One-time purchase read directly off the entitlement row, deliberately
      // not gated by status.valid (unlike the monthly Premium flags) -- see
      // backend has_paper_entitlement().
      setPaperEntitled(Boolean(status.paper_trading_enabled))
    } catch {
      // Unknown status must not block a genuinely entitled user from
      // starting; /runtime/start-selected is still the real gate.
      setPaperEntitled(true)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refreshPaperEntitlement()
  }, [refreshPaperEntitlement])

  useEffect(() => {
    if (!checkoutStarted || paperEntitled) return
    const interval = window.setInterval(() => void refreshPaperEntitlement(), 2500)
    return () => window.clearInterval(interval)
  }, [checkoutStarted, paperEntitled, refreshPaperEntitlement])

  async function startPaperCheckout() {
    setCheckoutPending(true)
    setCheckoutError('')
    try {
      const checkout = await createRazorpaySubscription('paper_premium')
      const checkoutUrl = checkout.checkout_url || checkout.short_url
      if (!checkoutUrl) throw new Error('Checkout link was not returned.')
      const opened = window.open(checkoutUrl, '_blank', 'noopener,noreferrer')
      if (!opened) throw new Error('Allow pop-ups to open Razorpay checkout.')
      setCheckoutStarted(true)
    } catch (reason) {
      setCheckoutError(reason instanceof Error ? reason.message : 'Could not start Razorpay checkout.')
    } finally {
      setCheckoutPending(false)
    }
  }

  useEffect(() => {
    onStateChange?.({
      state,
      strategyName: selectedStrategy?.name ?? null,
      strategyVersion: selectedStrategy?.version ?? null,
      savedComplete: setupSaved,
    })
  }, [state, selectedStrategy, setupSaved, onStateChange])

  useEffect(() => {
    if (userInteractedRef.current) return
    const selectedConfiguration = runtime?.selected_configuration
    const bootstrapMode = (runtime as RuntimeStatus & { mode?: EngineMode | null } | null)?.mode
    const authoritativeMode = bootstrapMode
      ?? selectedConfiguration?.mode
      ?? runtime?.engine?.mode
    if (!authoritativeMode || !selectedConfiguration) return
    if (selectedConfiguration.mode !== authoritativeMode) return
    // The user explicitly asked to start a new setup before this remount (e.g.
    // a mid-flow refresh) — that intent survives the remount via sessionStorage
    // because nothing else here does. Suppress auto-restore entirely (mode
    // included) so a refresh mid "Start New Setup" lands back on Step 1
    // (Choose Mode), not silently re-selects the old mode/strategy first.
    if (isFreshStartFlagged(runtime?.owner_user_id)) return
    if (!state.mode) {
      conv.selectMode(authoritativeMode)
      return
    }
    if (state.mode !== authoritativeMode || state.strategyKey) return
    const revisionKey = `${selectedConfiguration.id}:${selectedConfiguration.revision}`
    if (restoredRevisionRef.current === revisionKey) return
    const strategyKey = catalog?.selected_strategy_key
    const strategy = strategies.find((item) => item.strategy_key === strategyKey)
    if (!strategy) return
    restoredRevisionRef.current = revisionKey
    conv.selectStrategy(
      strategy.strategy_key,
      withRiskFields(strategy.setup_schema.fields, riskDefaults),
      {
        ...(strategy.saved_setup?.[authoritativeMode] ?? {}),
        ...selectedConfiguration.configuration,
        ...selectedConfiguration.risk,
        ...riskDefaults,
      },
    )
  }, [catalog?.selected_strategy_key, conv.selectMode, conv.selectStrategy, riskDefaults, runtime, sharedRisk, state.mode, state.strategyKey, strategies])
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

  function pickMode(m: EngineMode) {
    if (m === 'live' && !liveAvailable) return // never advance when Live is blocked
    userInteractedRef.current = true
    restoredRevisionRef.current = null
    setDirty(true)
    setSaved(false)
    setLiveAcknowledged(false)
    // Deliberately NOT clearing the fresh-start flag here: "Start New Setup"
    // must survive picking a mode, or the very next step (picking a strategy
    // that has old saved answers) immediately re-surfaces the saved-setup
    // card the user just tried to get away from. The flag only clears once
    // the fresh flow actually completes (save) or the user explicitly opts
    // back into their old data (Resume/Review).
    conv.selectMode(m)
    onModeSelect?.(m, 1_000_000)
  }

  useEffect(() => {
    if (!paywallOpen || !paperEntitled) return
    // Payment confirmed while the paywall was open -- retry the start the
    // user already asked for instead of leaving a stale modal up.
    setPaywallOpen(false)
    setCheckoutStarted(false)
    void startEngine()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paywallOpen, paperEntitled])

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
        {onRetry ? <Button variant="unstyled" type="button" className="conv-pill" onClick={onRetry}>Retry</Button> : null}
      </article>
    )
  }

  const fields = selectedStrategy ? withRiskFields(selectedStrategy.setup_schema.fields, riskDefaults) : state.fields
  // A selected strategy that lost readiness, or that exposes no setup schema,
  // must halt progression — no review, save or start — with a truthful reason.
  const strategyUnavailable = !!selectedStrategy && !(selectedStrategy.availability === 'READY' && selectedStrategy.paper_eligible)
  const schemaMissing = !!state.strategyKey && applicableFields(fields).length === 0
  const setupBlocked = !!state.strategyKey && (strategyUnavailable || schemaMissing)

  async function pickStrategy(s: CatalogStrategy) {
    if (!mode) return
    userInteractedRef.current = true
    restoredRevisionRef.current = null
    setDirty(true)
    setSaved(false)
    setLiveAcknowledged(false)
    // Mid an explicit "Start New Setup" attempt, never resurface this
    // strategy's old saved answers -- that's the whole point of Start New.
    // Without this, picking a strategy that has a real saved config always
    // re-triggers the saved-setup decision card, no matter how many times
    // the user backs out via Start New Setup: an inescapable loop.
    const currentRiskDefaults = sharedRisk ? {
      max_daily_loss: sharedRisk.values.daily_loss_cap,
      max_trades_per_day: sharedRisk.values.max_trades_per_day,
    } : {}
    const savedValues = isFreshStartFlagged(runtime?.owner_user_id)
      ? {}
      : { ...(s.saved_setup?.[mode] ?? {}), ...currentRiskDefaults }
    conv.selectStrategy(s.strategy_key, withRiskFields(s.setup_schema.fields, {
      ...currentRiskDefaults,
    }), savedValues)
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
      // Both halves go in one revision-bound request. Two separate saves could
      // leave new limits applied to old sizing, or the reverse.
      await onSave(selectedStrategy.strategy_key, toSaveValues(strategy), toSaveValues(risk))
      if (isStale(gen, strategyKey)) return // conversation moved on — ignore result
      clearFreshStartFlag()
      setDirty(false)
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
    if (!selectedStrategy?.strategy_instance_id || !setupSaved || inFlightRef.current) return
    if (mode === 'live' && !liveAcknowledged) return
    if (mode === 'paper' && !paperEntitled) { setPaywallOpen(true); return }
    const gen = state.generation
    const strategyKey = state.strategyKey
    inFlightRef.current = true
    setPending('starting')
    try {
      await onStart(selectedStrategy.strategy_instance_id, liveAcknowledged)
      if (isStale(gen, strategyKey)) return // stale start cannot fabricate running state
      conv.startEngine()
    } catch (e) {
      if (isStale(gen, strategyKey)) return
      setSaveError(e instanceof Error ? e.message : 'Engine start failed.')
    } finally {
      inFlightRef.current = false
      setPending('idle')
      if (mode === 'live') setLiveAcknowledged(false)
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
        ? `NOVA asks: ${questionFor(fields, state.activeQuestionKey)}`
        : state.phase === 'SETUP_REVIEW' || state.phase === 'ENGINE_READY'
          ? 'NOVA setup is ready to review, save, and start.'
          : ''

  return (
    <div ref={scrollRef} className="conv-canvas">
      <div className="sr-only" role="status" aria-live="polite">{announce}</div>
      {!state.mode ? (
        <div className="conv-turn">
          <BotBubble showAvatar>
            <strong>How should NOVA trade for you?</strong> I recommend Paper first — it uses real market data with simulated fills, and no order reaches Dhan.
          </BotBubble>
          <div className="conv-choices conv-mode-choices" aria-label="Choose trading mode">
            <Button variant="unstyled" type="button" className="conv-pill conv-pill--primary" onClick={() => pickMode('paper')}>Start in Paper</Button>
            <Button variant="unstyled" type="button" className="conv-pill" disabled={!liveAvailable} aria-disabled={!liveAvailable} onClick={() => pickMode('live')}>
              {liveAvailable ? 'Configure Live' : 'Live unavailable'}
            </Button>
          </div>
        </div>
      ) : null}

      {state.mode && (state.phase === 'STRATEGY_SELECTION' || !state.strategyKey) ? (
        <div className="conv-turn">
          <BotBubble showAvatar>Which strategy should NOVA run?</BotBubble>
          <StrategyGroup title="NOVA Strategies" mode={state.mode} strategies={strategies.filter((s) => s.source_type === 'BUILT_IN')} onPick={pickStrategy} />
          <StrategyGroup title="My Strategies" mode={state.mode} strategies={strategies.filter((s) => s.source_type === 'IMPORTED')} onPick={pickStrategy} />
        </div>
      ) : null}

      {setupBlocked ? (
        <div role="alert">
          <BotBubble tone="normal" showAvatar>
            {schemaMissing
              ? `${selectedStrategy?.name ?? 'This strategy'} can't be configured right now — it has no setup questions.`
              : (selectedStrategy?.disabled_reason ?? 'This strategy is currently unavailable, so setup is paused.')}
          </BotBubble>
          <div className="conv-actions">
            <Button variant="unstyled" type="button" className="conv-pill" onClick={() => { userInteractedRef.current = true; if (state.mode) conv.selectMode(state.mode) }}>Choose another strategy</Button>
          </div>
        </div>
      ) : null}

      {!setupBlocked && state.phase === 'SAVED_SETUP_FOUND' ? (
        <>
          <BotBubble showAvatar>I found your previous {mode === 'paper' ? 'Paper' : 'Live'} configuration.</BotBubble>
          <div className="conv-saved-summary" role="group" aria-label="Saved setup summary">
            {applicableFields(fields).filter((f) => f.key in state.saved).map((f) => (
              <div key={f.key} className="conv-summary-row"><span>{f.label}</span><strong>{String(state.saved[f.key])}</strong></div>
            ))}
          </div>
          <div className="conv-actions">
            <Button variant="unstyled" type="button" className="conv-pill conv-pill--primary" onClick={() => { userInteractedRef.current = true; clearFreshStartFlag(); conv.resume() }}>Resume</Button>
            <Button variant="unstyled" type="button" className="conv-pill" onClick={() => { userInteractedRef.current = true; clearFreshStartFlag(); conv.review() }}>Review</Button>
            <Button variant="unstyled"
              type="button"
              className="conv-pill"
              onClick={() => {
                userInteractedRef.current = true
                restoredRevisionRef.current = null
                setDirty(true)
                setSaved(false)
                setLiveAcknowledged(false)
                setFreshStartFlag(runtime?.owner_user_id)
                // A full reset, not conv.startNew() — the audit requirement is
                // explicit: Start New Setup begins at Step 1 (Choose Mode), not
                // a fresh run of questions for the same already-selected mode
                // and strategy. Nothing here mutates the backend saved config;
                // it stays intact for Resume/Review to find later.
                conv.reset()
              }}
            >
              Start New Setup
            </Button>
          </div>
        </>
      ) : null}

      {transcript.filter((e) => e.type === 'user_answer').map((e) => (
        <div className="conv-turn" key={e.id}>
          <BotBubble showAvatar>{questionFor(fields, e.fieldKey ?? '')}</BotBubble>
          <UserBubble text={answerFor(e.fieldKey, e.payload.value)} />
        </div>
      ))}

      {state.phase === 'ASSISTANT_TYPING' ? <TypingDots /> : null}

      {!setupBlocked && state.phase === 'QUESTION_ACTIVE' && state.activeQuestionKey ? (
        <div className="conv-turn">
          <BotBubble showAvatar>{questionFor(fields, state.activeQuestionKey)}</BotBubble>
          <ActiveQuestion
            key={`${state.activeQuestionKey}-${state.generation}`}
            field={fields.find((f) => f.key === state.activeQuestionKey)!}
            currentValue={state.draft[state.activeQuestionKey]}
            onCommit={commit}
          />
        </div>
      ) : null}

      {!setupBlocked && (state.phase === 'SETUP_REVIEW' || state.phase === 'ENGINE_READY') ? (
        <div className="conv-review" role="group" aria-label="Setup review">
          <BotBubble showAvatar>Here is your {selectedStrategy?.name ?? 'strategy'} configuration. Review, then save and start.</BotBubble>
          <div className="conv-saved-summary">
            {applicableFields(fields).filter((f) => f.key in state.draft).map((f) => (
              <div key={f.key} className="conv-summary-row">
                <span>{f.label}</span>
                <strong>{String(state.draft[f.key])}</strong>
                <InlineReviewEdit
                  field={f}
                  value={state.draft[f.key]}
                  onCommit={(next) => {
                    restoredRevisionRef.current = null
                    setDirty(true)
                    setSaved(false)
                    conv.commitAnswer(f.key, next)
                  }}
                />
              </div>
            ))}
          </div>
          {saveError ? <p className="conv-error" role="alert">{saveError}</p> : null}
          {setupSaved && mode === 'live' ? (
            <label className="conv-live-ack">
              <Input variant="unstyled"
                type="checkbox"
                checked={liveAcknowledged}
                onChange={(event) => setLiveAcknowledged(event.target.checked)}
              />
              <span>I understand this will place real orders through my Dhan account</span>
            </label>
          ) : null}
          <div className="conv-actions">
            {!setupSaved ? (
              <Button variant="unstyled" type="button" className="conv-pill conv-pill--primary" disabled={pending !== 'idle'} onClick={saveSetup}>
                {pending === 'saving' ? 'Saving…' : 'Save setup'}
              </Button>
            ) : (
              <Button variant="unstyled"
                type="button"
                className="conv-pill conv-pill--primary"
                disabled={pending !== 'idle' || (mode === 'live' && !liveAcknowledged)}
                onClick={startEngine}
              >
                {pending === 'starting' ? 'Starting…' : mode === 'live' ? 'Start Live' : 'Start Paper'}
              </Button>
            )}
            {setupSaved ? (
              <Button variant="unstyled"
                type="button"
                className="conv-pill"
                disabled={pending !== 'idle'}
                onClick={() => {
                  userInteractedRef.current = true
                  restoredRevisionRef.current = null
                  setDirty(true)
                  setSaved(false)
                  setLiveAcknowledged(false)
                  setFreshStartFlag(runtime?.owner_user_id)
                  // Same full reset as "Start New Setup" — back to Step 1
                  // (Choose Mode) so Paper/Live can be picked again. Nothing
                  // here mutates the backend saved config.
                  conv.reset()
                }}
              >
                Reconfigure
              </Button>
            ) : null}
            {setupSaved ? <span className="conv-saved-badge"><Check size={14} /> Setup saved</span> : null}
          </div>
          {paywallOpen ? (
            <div role="alertdialog" aria-label="Nova Paper Premium required">
              <BotBubble tone="normal" showAvatar>
                Paper trading requires Nova Paper Premium — a one-time ₹100 purchase, not a subscription. Unlocks Paper mode permanently.
              </BotBubble>
              {checkoutStarted ? (
                <BotBubble tone="normal">Complete checkout, then return here — NOVA checks payment confirmation automatically and starts the engine once it clears.</BotBubble>
              ) : null}
              {checkoutError ? <p className="conv-error" role="alert">{checkoutError}</p> : null}
              <div className="conv-actions">
                <Button variant="unstyled" type="button" className="conv-pill conv-pill--primary" disabled={checkoutPending} onClick={() => void startPaperCheckout()}>
                  {checkoutPending ? 'Creating Checkout…' : checkoutStarted ? 'Reopen Razorpay Checkout' : 'Pay ₹100 & Continue'}
                </Button>
                <Button variant="unstyled" type="button" className="conv-pill" onClick={() => { setPaywallOpen(false); setCheckoutStarted(false); setCheckoutError('') }}>Cancel</Button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {showJump ? (
        <Button variant="unstyled" type="button" className="conv-jump" onClick={jumpToLatest}>Jump to latest ↓</Button>
      ) : null}
    </div>
  )
}
