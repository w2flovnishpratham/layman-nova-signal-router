import { AlertTriangle, Check, Loader2, Pencil } from 'lucide-react'
import { useState } from 'react'
import type { CatalogStrategy, RuntimeStatus, StrategySetupField } from '../../api'
import { blockerText } from '../../strategies/strategyBlockers'
import { BotBubble } from '../messages/BotBubble'
import { UserBubble } from '../messages/UserBubble'
import { TradingStrategyCard } from './TradingStrategyCard'

interface Props {
  runtime: RuntimeStatus | null
  loading: boolean
  error: string
  onManage: (instanceId: string) => void
  onSelect: (strategyKey: string) => Promise<void>
  onSave: (strategyKey: string, values: Record<string, string | number>) => Promise<void>
  onStart: (instanceId: string) => Promise<void>
  onUserReply: (text: string) => void
  strategyPromptPresent?: boolean
}

type Phase = 'catalog' | 'questions' | 'review'

function initialValues(strategy: CatalogStrategy | null): Record<string, string | number> {
  if (!strategy) return {}
  const saved = strategy.saved_setup?.paper ?? {}
  const values: Record<string, string | number> = {}
  for (const field of strategy.setup_schema.fields) {
    const savedValue = saved[field.key]
    values[field.key] = typeof savedValue === 'string' || typeof savedValue === 'number'
      ? savedValue
      : field.default ?? ''
  }
  return values
}

function fieldError(field: StrategySetupField, value: string | number): string {
  if (field.type === 'choice') {
    return field.options.includes(String(value)) ? '' : `Choose ${field.options.join(', ')}.`
  }
  const number = Number(value)
  if (!Number.isFinite(number)) return 'Enter a valid number.'
  if (field.type === 'integer' && !Number.isInteger(number)) return 'Enter a whole number.'
  if (number < field.minimum || number > field.maximum) {
    return `Enter a value from ${field.minimum} to ${field.maximum}.`
  }
  return ''
}

function answerText(field: StrategySetupField, value: string | number): string {
  if (field.key === 'lots') return `${value} lot${Number(value) === 1 ? '' : 's'}`
  if (field.key.includes('percent')) return `${value}%`
  return String(value)
}

export function UnifiedStrategySetup({
  runtime,
  loading,
  error,
  onManage,
  onSelect,
  onSave,
  onStart,
  strategyPromptPresent = false,
}: Props) {
  const catalog = runtime?.strategy_catalog
  const initialPhase: Phase = catalog?.setup_progress.complete ? 'review' : 'catalog'
  const [phase, setPhase] = useState<Phase>(initialPhase)
  const [strategyKey, setStrategyKey] = useState(catalog?.selected_strategy_key ?? '')
  const selectedFromCatalog = catalog?.strategies.find((strategy) => (
    strategy.strategy_key === (catalog.selected_strategy_key ?? strategyKey)
  )) ?? null
  const [values, setValues] = useState<Record<string, string | number>>(
    initialValues(selectedFromCatalog),
  )
  const initialAnswered = catalog?.setup_progress.complete
    ? selectedFromCatalog?.setup_schema.fields.length ?? 0
    : 0
  const [answeredCount, setAnsweredCount] = useState(initialAnswered)
  const [busy, setBusy] = useState('')
  const [actionError, setActionError] = useState('')
  const [validationError, setValidationError] = useState('')
  const [startedMessage, setStartedMessage] = useState('')

  const strategies = catalog?.strategies ?? []
  const activeStrategy = strategies.find((strategy) => strategy.strategy_key === strategyKey)
    ?? strategies.find((strategy) => strategy.selected)
    ?? null
  const fields = activeStrategy?.setup_schema.fields ?? []
  const switchingBlocked = Boolean(
    runtime && (runtime.engine.state !== 'STOPPED' || runtime.position.has_open_position),
  )
  const completedFields = fields.slice(0, Math.min(answeredCount, fields.length))

  async function choose(strategy: CatalogStrategy) {
    if (!strategy.paper_eligible || strategy.availability !== 'READY') return
    setBusy(`select-${strategy.strategy_key}`)
    setActionError('')
    try {
      await onSelect(strategy.strategy_key)
      setStrategyKey(strategy.strategy_key)
      setValues(initialValues(strategy))
      setAnsweredCount(0)
      setValidationError('')
      setPhase('questions')
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'Could not select strategy.')
    } finally {
      setBusy('')
    }
  }

  async function submitCurrentAnswer() {
    if (!activeStrategy) return
    const field = fields[answeredCount]
    if (!field) return
    const invalid = fieldError(field, values[field.key])
    if (invalid) {
      setValidationError(invalid)
      return
    }
    setValidationError('')
    if (answeredCount < fields.length - 1) {
      setAnsweredCount((count) => count + 1)
      return
    }
    setBusy('save')
    try {
      await onSave(activeStrategy.strategy_key, values)
      setAnsweredCount(fields.length)
      setPhase('review')
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'Could not save strategy setup.')
    } finally {
      setBusy('')
    }
  }

  function editAnswer(index: number) {
    if (!activeStrategy) return
    const reset = initialValues(activeStrategy)
    for (let current = 0; current <= index; current += 1) {
      const field = fields[current]
      if (field) reset[field.key] = values[field.key]
    }
    setValues(reset)
    setAnsweredCount(index)
    setValidationError('')
    setActionError('')
    setPhase('questions')
  }

  function configureAgain() {
    editAnswer(0)
  }

  async function startSelected(instanceId: string) {
    setBusy('start')
    setActionError('')
    try {
      await onStart(instanceId)
      setStartedMessage(`${activeStrategy?.name ?? 'The selected strategy'} is now running in Paper mode.`)
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'Could not start the Paper engine.')
      throw reason
    } finally {
      setBusy('')
    }
  }

  if (loading && !runtime) {
    return <article className="setup-card catalog-state" role="status"><Loader2 className="strategy-card-spin" size={18} /> Loading strategy catalog…</article>
  }
  if (error && !runtime) {
    return <article className="setup-card catalog-state" role="alert"><AlertTriangle size={18} /> <span>{error}</span></article>
  }

  if (phase === 'catalog') {
    const nova = strategies.filter((strategy) => strategy.source_type === 'BUILT_IN')
    const mine = strategies.filter((strategy) => strategy.source_type === 'IMPORTED')
    return (
      <div className="unified-conversation">
        {!strategyPromptPresent ? (
          <BotBubble><p>Which strategy should we run today?</p></BotBubble>
        ) : null}
        {switchingBlocked ? (
          <BotBubble tone="error">
            <p>Stop the engine and confirm the tracked position is flat before changing strategy.</p>
          </BotBubble>
        ) : null}
        {actionError ? <p className="strategy-setup-error" role="alert">{actionError}</p> : null}
        <ConversationStrategyPicker
          nova={nova}
          mine={mine}
          busy={busy}
          blocked={switchingBlocked}
          onChoose={choose}
        />
        <BotBubble label="Nova Live availability">
          <p className="live-unavailable-message">
            Live unavailable on this environment. Complete Live eligibility and broker verification before starting real-money routing.
          </p>
        </BotBubble>
      </div>
    )
  }

  if (!activeStrategy || fields.length === 0) {
    return <article className="setup-card catalog-state" role="alert">This strategy has no supported setup questions.</article>
  }

  return (
    <div className="unified-conversation">
      <UserBubble text={`Use ${activeStrategy.name}`} />
      {completedFields.map((field, index) => (
        <div className="conversation-turn" key={field.key}>
          <BotBubble><p>{field.label}</p></BotBubble>
          <div className="conversation-answer-row">
            <UserBubble text={answerText(field, values[field.key])} />
            {phase !== 'review' ? (
              <button type="button" className="conversation-edit" onClick={() => editAnswer(index)}>
                <Pencil size={12} /> Edit
              </button>
            ) : null}
          </div>
        </div>
      ))}

      {phase === 'questions' && answeredCount < fields.length ? (
        <ConversationQuestion
          field={fields[answeredCount]}
          value={values[fields[answeredCount].key]}
          pineMessage={answeredCount === 0 ? activeStrategy.pine_exit_behavior?.message : null}
          error={validationError || actionError}
          busy={busy === 'save'}
          finalQuestion={answeredCount === fields.length - 1}
          onChange={(value) => setValues({ ...values, [fields[answeredCount].key]: value })}
          onContinue={() => void submitCurrentAnswer()}
          onChangeStrategy={() => setPhase('catalog')}
        />
      ) : null}

      {phase === 'review' ? (
        <>
          <BotBubble label="Nova setup review">
            <p>Your Paper setup is ready. Please review it before starting.</p>
          </BotBubble>
          <div className="conversation-review">
            <TradingStrategyCard
              runtime={runtime}
              loading={loading}
              error={error || actionError}
              onManage={onManage}
              onConfigure={async () => configureAgain()}
              onSelect={async () => undefined}
              onStart={startSelected}
              onConfigureRequested={configureAgain}
              onChangeStrategy={() => {
                if (switchingBlocked) return
                setActionError('')
                setPhase('catalog')
              }}
            />
          </div>
          {startedMessage || runtime?.engine.running ? (
            <BotBubble label="Nova engine status">
              <p>{startedMessage || `${activeStrategy.name} is now running in Paper mode.`}</p>
            </BotBubble>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

function ConversationQuestion({
  field,
  value,
  pineMessage,
  error,
  busy,
  finalQuestion,
  onChange,
  onContinue,
  onChangeStrategy,
}: {
  field: StrategySetupField
  value: string | number
  pineMessage?: string | null
  error: string
  busy: boolean
  finalQuestion: boolean
  onChange: (value: string | number) => void
  onContinue: () => void
  onChangeStrategy: () => void
}) {
  return (
    <BotBubble label={`Nova setup question: ${field.label}`}>
      <div className="conversation-question">
        <p>{field.label}</p>
        {pineMessage ? <small className="pine-exit-note">{pineMessage}</small> : null}
        {field.type === 'choice' ? (
          <div className="conversation-choice-row" role="group" aria-label={field.label}>
            {field.options.map((option) => (
              <button
                key={option}
                type="button"
                className={value === option ? 'selected' : ''}
                onClick={() => onChange(option)}
              >
                {value === option ? <Check size={13} /> : null}{option}
              </button>
            ))}
          </div>
        ) : (
          <label className="conversation-number-answer">
            <span>{field.type === 'integer' ? 'Lots' : 'Percent'}</span>
            <input
              aria-label={field.label}
              type="number"
              min={field.minimum}
              max={field.maximum}
              step={field.type === 'integer' ? 1 : 0.1}
              value={value ?? ''}
              onChange={(event) => onChange(event.target.value)}
            />
          </label>
        )}
        {error ? <p className="strategy-setup-error" role="alert">{error}</p> : null}
        <div className="conversation-controls">
          <button type="button" className="conversation-link" onClick={onChangeStrategy}>Change strategy</button>
          <button type="button" className="conversation-continue" onClick={onContinue} disabled={busy}>
            {busy ? 'Saving…' : finalQuestion ? 'Save and review' : 'Continue'}
          </button>
        </div>
      </div>
    </BotBubble>
  )
}

function ConversationStrategyPicker({
  nova,
  mine,
  busy,
  blocked,
  onChoose,
}: {
  nova: CatalogStrategy[]
  mine: CatalogStrategy[]
  busy: string
  blocked: boolean
  onChoose: (strategy: CatalogStrategy) => Promise<void>
}) {
  return (
    <div className="conversation-strategy-picker">
      <StrategyChoiceGroup title="NOVA Strategies" strategies={nova} busy={busy} blocked={blocked} onChoose={onChoose} />
      <StrategyChoiceGroup title="My Strategies" strategies={mine} busy={busy} blocked={blocked} onChoose={onChoose} />
      {!mine.some((strategy) => strategy.paper_eligible)
        ? <p className="catalog-empty">No Paper-ready imported strategies are available yet.</p>
        : null}
    </div>
  )
}

function StrategyChoiceGroup({
  title,
  strategies,
  busy,
  blocked,
  onChoose,
}: {
  title: string
  strategies: CatalogStrategy[]
  busy: string
  blocked: boolean
  onChoose: (strategy: CatalogStrategy) => Promise<void>
}) {
  const id = title.replaceAll(' ', '-').toLowerCase()
  return (
    <section className="conversation-strategy-group" aria-labelledby={id}>
      <h4 id={id}>{title}</h4>
      <div className="conversation-strategy-choices">
        {strategies.map((strategy) => {
          const ready = strategy.availability === 'READY' && strategy.paper_eligible
          const reason = strategy.disabled_reason
            ? blockerText(strategy.disabled_reason)
            : strategy.availability.replaceAll('_', ' ').toLowerCase()
          return (
            <div className="conversation-strategy-option" key={strategy.strategy_key}>
              <button
                type="button"
                className={strategy.selected ? 'selected' : ''}
                disabled={!ready || blocked || busy === `select-${strategy.strategy_key}`}
                title={ready ? `${strategy.name} is ready for Paper` : reason}
                onClick={() => void onChoose(strategy)}
              >
                {strategy.name}
                {strategy.selected ? <Check size={11} /> : null}
              </button>
              <small className={ready ? 'ready' : 'disabled'}>
                {ready ? `${strategy.source_type === 'BUILT_IN' ? 'NOVA' : 'Imported'} · Paper ready` : reason}
              </small>
            </div>
          )
        })}
      </div>
    </section>
  )
}
