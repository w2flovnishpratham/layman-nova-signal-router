import { AlertTriangle, Check, ChevronRight, Loader2 } from 'lucide-react'
import { useState } from 'react'
import type { CatalogStrategy, RuntimeStatus, StrategySetupField } from '../../api'
import { blockerText } from '../../strategies/strategyBlockers'
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

export function UnifiedStrategySetup({
  runtime,
  loading,
  error,
  onManage,
  onSelect,
  onSave,
  onStart,
  onUserReply,
}: Props) {
  const catalog = runtime?.strategy_catalog
  const initialPhase: Phase = catalog?.setup_progress.complete ? 'review' : 'catalog'
  const [phase, setPhase] = useState<Phase>(initialPhase)
  const [strategyKey, setStrategyKey] = useState(catalog?.selected_strategy_key ?? '')
  const [questionIndex, setQuestionIndex] = useState(0)
  const [values, setValues] = useState<Record<string, string | number>>({})
  const [busy, setBusy] = useState('')
  const [actionError, setActionError] = useState('')
  const [validationError, setValidationError] = useState('')

  const strategies = catalog?.strategies ?? []
  const activeStrategy = strategies.find((strategy) => strategy.strategy_key === strategyKey)
    ?? strategies.find((strategy) => strategy.selected)
    ?? null

  async function choose(strategy: CatalogStrategy) {
    if (!strategy.paper_eligible || strategy.availability !== 'READY') return
    setBusy(`select-${strategy.strategy_key}`)
    setActionError('')
    try {
      await onSelect(strategy.strategy_key)
      setStrategyKey(strategy.strategy_key)
      setValues(initialValues(strategy))
      setQuestionIndex(0)
      setPhase('questions')
      onUserReply(`Use ${strategy.name} in Paper mode`)
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'Could not select strategy.')
    } finally {
      setBusy('')
    }
  }

  async function nextQuestion() {
    if (!activeStrategy) return
    const fields = activeStrategy.setup_schema.fields
    const field = fields[questionIndex]
    const invalid = fieldError(field, values[field.key])
    if (invalid) {
      setValidationError(invalid)
      return
    }
    setValidationError('')
    onUserReply(`${field.label} ${values[field.key]}`)
    if (questionIndex < fields.length - 1) {
      setQuestionIndex((index) => index + 1)
      return
    }
    setBusy('save')
    try {
      await onSave(activeStrategy.strategy_key, values)
      setPhase('review')
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'Could not save strategy setup.')
    } finally {
      setBusy('')
    }
  }

  function configureAgain() {
    if (!activeStrategy) return
    setValues(initialValues(activeStrategy))
    setQuestionIndex(0)
    setValidationError('')
    setActionError('')
    setPhase('questions')
  }

  const switchingBlocked = Boolean(
    runtime && (runtime.engine.state !== 'STOPPED' || runtime.position.has_open_position),
  )

  if (loading && !runtime) {
    return <article className="setup-card catalog-state" role="status"><Loader2 className="strategy-card-spin" size={18} /> Loading strategy catalog…</article>
  }
  if (error && !runtime) {
    return <article className="setup-card catalog-state" role="alert"><AlertTriangle size={18} /> <span>{error}</span></article>
  }

  if (phase === 'review') {
    return (
      <TradingStrategyCard
        runtime={runtime}
        loading={loading}
        error={error || actionError}
        onManage={onManage}
        onConfigure={async () => configureAgain()}
        onSelect={async () => undefined}
        onStart={onStart}
        onConfigureRequested={configureAgain}
        onChangeStrategy={() => {
          if (switchingBlocked) return
          setActionError('')
          setPhase('catalog')
        }}
      />
    )
  }

  if (phase === 'questions' && activeStrategy) {
    const fields = activeStrategy.setup_schema.fields
    const field = fields[questionIndex]
    if (!field) {
      return <article className="setup-card catalog-state" role="alert">This strategy has no supported setup questions.</article>
    }
    return (
      <article className="setup-card conversational-strategy-setup">
        <header>
          <span>Setting up {activeStrategy.name}</span>
          <small>Question {questionIndex + 1} of {fields.length}</small>
        </header>
        <h3>{field.label}</h3>
        {questionIndex === 0 && activeStrategy.pine_exit_behavior ? (
          <p className="pine-exit-note">{activeStrategy.pine_exit_behavior.message}</p>
        ) : null}
        {field.type === 'choice' ? (
          <div className="setup-choice-row" role="group" aria-label={field.label}>
            {field.options.map((option) => (
              <button
                key={option}
                type="button"
                className={values[field.key] === option ? 'selected' : ''}
                onClick={() => setValues({ ...values, [field.key]: option })}
              >
                {values[field.key] === option ? <Check size={14} /> : null}{option}
              </button>
            ))}
          </div>
        ) : (
          <label className="setup-number-answer">
            <span>{field.type === 'integer' ? 'Lots' : 'Percent'}</span>
            <input
              aria-label={field.label}
              type="number"
              min={field.minimum}
              max={field.maximum}
              step={field.type === 'integer' ? 1 : 0.1}
              value={values[field.key] ?? ''}
              onChange={(event) => setValues({ ...values, [field.key]: event.target.value })}
            />
          </label>
        )}
        {validationError || actionError ? <p className="strategy-setup-error" role="alert">{validationError || actionError}</p> : null}
        <div className="conversation-actions">
          <button type="button" onClick={() => setPhase('catalog')} disabled={busy === 'save'}>Back to catalog</button>
          <button type="button" className="primary-button" onClick={() => void nextQuestion()} disabled={busy === 'save'}>
            {busy === 'save' ? 'Saving…' : questionIndex === fields.length - 1 ? 'Save and review' : <>Continue <ChevronRight size={14} /></>}
          </button>
        </div>
      </article>
    )
  }

  const nova = strategies.filter((strategy) => strategy.source_type === 'BUILT_IN')
  const mine = strategies.filter((strategy) => strategy.source_type === 'IMPORTED')
  return (
    <article className="setup-card unified-strategy-catalog">
      <header className="catalog-heading">
        <div><h3>Which strategy should we run today?</h3><p>Choose a strategy, then NOVA will ask only its supported setup questions.</p></div>
        <span>Paper mode</span>
      </header>
      {switchingBlocked ? (
        <div className="trading-strategy-warning" role="status">
          <AlertTriangle size={15} />
          <span>Stop the engine and confirm the tracked position is flat before changing strategy.</span>
        </div>
      ) : null}
      {actionError ? <p className="strategy-setup-error" role="alert">{actionError}</p> : null}
      <CatalogGroup title="NOVA Strategies" strategies={nova} busy={busy} blocked={switchingBlocked} onChoose={choose} />
      <CatalogGroup title="My Strategies" strategies={mine} busy={busy} blocked={switchingBlocked} onChoose={choose} />
      {!mine.some((strategy) => strategy.paper_eligible)
        ? <p className="catalog-empty">No Paper-ready imported strategies are available yet.</p>
        : null}
      <div className="live-unavailable" role="status"><AlertTriangle size={14} /> Live unavailable on this environment. No Live engine can be started.</div>
    </article>
  )
}

function CatalogGroup({
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
  return (
    <section className="strategy-catalog-group" aria-labelledby={title.replaceAll(' ', '-').toLowerCase()}>
      <h4 id={title.replaceAll(' ', '-').toLowerCase()}>{title}</h4>
      <div className="strategy-catalog-grid">
        {strategies.map((strategy) => {
          const ready = strategy.availability === 'READY' && strategy.paper_eligible
          const reason = strategy.disabled_reason
            ? blockerText(strategy.disabled_reason)
            : strategy.availability.replaceAll('_', ' ').toLowerCase()
          return (
            <button
              key={strategy.strategy_key}
              type="button"
              className={`strategy-catalog-card ${strategy.selected ? 'selected' : ''}`}
              disabled={!ready || blocked || busy === `select-${strategy.strategy_key}`}
              onClick={() => void onChoose(strategy)}
            >
              <span className="catalog-card-title">
                <strong>{strategy.name}</strong>
                {strategy.selected ? <small><Check size={11} /> Selected</small> : null}
              </span>
              <span>{strategy.version ? `Version ${strategy.version} · ` : ''}{strategy.source_type === 'BUILT_IN' ? 'NOVA' : 'Imported'}</span>
              <span className={ready ? 'catalog-ready' : 'catalog-disabled'}>
                {ready ? 'Paper ready' : reason}
              </span>
              <span>Live unavailable</span>
            </button>
          )
        })}
      </div>
    </section>
  )
}
