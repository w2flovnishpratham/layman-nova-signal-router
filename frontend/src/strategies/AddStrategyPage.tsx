import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Button } from '@/components/ui/button'
import { toast } from '@/components/ui/toast'
import { AlertTriangle, Check, FileUp, Loader2 } from 'lucide-react'
import { useRef, useState } from 'react'
import {
  ACCEPTED_EXTENSIONS,
  createPineStrategy,
  extensionIsSupported,
  submitForReview,
  validatePineVersion,
  type PineValidation,
} from './pineApi'
import './addStrategy.css'

type Stage = 'source' | 'validated' | 'submitted'

const STEPS: { key: Stage; label: string }[] = [
  { key: 'source', label: 'Source' },
  { key: 'validated', label: 'Validate' },
  { key: 'submitted', label: 'Review' },
]

// Terminology is deliberate: NOVA runs its own static and signal-contract
// checks. It never compiles anything on TradingView, so no stage may claim that.
const STAGE_LABELS: Record<PineValidation['status'], string> = {
  PASSED: 'Static validation passed',
  PASSED_WITH_WARNINGS: 'Static validation passed with warnings',
  FAILED: 'Static validation failed',
  VALIDATOR_ERROR: 'Static validation could not complete',
}

function Stepper({ stage }: { stage: Stage }) {
  const activeIndex = STEPS.findIndex((step) => step.key === stage)
  return (
    <ol className="add-strategy-stepper" aria-label="Import progress">
      {STEPS.map((step, index) => {
        const status = index < activeIndex ? 'done' : index === activeIndex ? 'active' : 'upcoming'
        return (
          <li key={step.key} className={`as-step as-step--${status}`}>
            <span className="as-step-dot">{status === 'done' ? <Check size={12} /> : index + 1}</span>
            <span className="as-step-label">{step.label}</span>
            {index < STEPS.length - 1 ? <span className="as-step-line" /> : null}
          </li>
        )
      })}
    </ol>
  )
}

export function AddStrategyPage() {
  const [name, setName] = useState('')
  const [source, setSource] = useState('')
  const [filename, setFilename] = useState<string | null>(null)
  const [stage, setStage] = useState<Stage>('source')
  const [validation, setValidation] = useState<PineValidation | null>(null)
  const [ids, setIds] = useState<{ strategyId: string; versionId: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  function onFile(file: File | undefined) {
    if (!file) return
    if (!extensionIsSupported(file.name)) {
      setError(`Only ${ACCEPTED_EXTENSIONS.join(' and ')} files are accepted.`)
      return
    }
    setError('')
    const reader = new FileReader()
    reader.onload = () => {
      setSource(String(reader.result ?? ''))
      setFilename(file.name)
      if (!name) setName(file.name.replace(/\.(pine|txt)$/i, ''))
    }
    reader.readAsText(file)
  }

  async function runValidation() {
    setBusy(true)
    setError('')
    try {
      const created = await createPineStrategy(name.trim(), source, filename ?? undefined)
      const next = { strategyId: created.strategy.id, versionId: created.version.id }
      setIds(next)
      const result = await validatePineVersion(next.strategyId, next.versionId)
      setValidation(result.validation)
      setStage('validated')
    } catch (e) {
      toast.add({
        title: e instanceof Error ? e.message : 'Validation could not run.',
        type: 'error',
      })
    } finally {
      setBusy(false)
    }
  }

  async function runSubmit() {
    if (!ids || !validation?.eligible_for_review) return
    setBusy(true)
    setError('')
    try {
      await submitForReview(ids.strategyId, ids.versionId, validation.contract_version)
      setStage('submitted')
      toast.add({ title: 'Strategy submitted for admin review.', type: 'success' })
    } catch (e) {
      toast.add({
        title: e instanceof Error ? e.message : 'Submission failed.',
        type: 'error',
      })
    } finally {
      setBusy(false)
    }
  }

  const canValidate = name.trim().length > 0 && source.trim().length > 0 && !busy

  return (
    <div className="nova-signals">
      <header className="nova-signals-head">
        <div>
          <h1>Add Strategy</h1>
          <p>
            Import a Pine script. NOVA validates it statically and against the signal
            contract, then an admin reviews it. Nothing routes until you select it,
            complete setup and start the engine yourself.
          </p>
        </div>
      </header>

      <Stepper stage={stage} />

      {error ? <p className="nova-signals-state" role="alert"><AlertTriangle size={16} /> {error}</p> : null}

      {stage === 'submitted' ? (
        <section className="nova-hooks-card" aria-label="Submitted">
          <div className="nova-hooks-card-head"><Check size={15} /><strong>Submitted for admin review</strong></div>
          <p>
            An admin must approve this strategy before it can be selected. Approval does
            not start anything: you still choose the strategy, complete setup, commit the
            configuration and press Start Engine.
          </p>
        </section>
      ) : (
        <section className="nova-hooks-card" aria-label="Pine source">
          <div className="nova-hooks-card-head"><strong>Pine source</strong></div>
          <label htmlFor="pine-name">Strategy name</label>
          <Input variant="unstyled"
            id="pine-name"
            className="nova-pine-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My Supertrend variant"
          />

          <label htmlFor="pine-source">Paste Pine code</label>
          <Textarea variant="unstyled"
            id="pine-source"
            className="nova-pine-source"
            value={source}
            rows={12}
            onChange={(e) => setSource(e.target.value)}
            placeholder="//@version=5&#10;strategy(...)"
          />

          <div className="nova-cred-actions">
            <Input variant="unstyled"
              ref={fileRef}
              type="file"
              accept=".pine,.txt"
              className="sr-only"
              aria-label="Upload a .pine or .txt file"
              onChange={(e) => onFile(e.target.files?.[0])}
            />
            <Button variant="unstyled" type="button" className="conv-pill" onClick={() => fileRef.current?.click()}>
              <FileUp size={13} /> Upload .pine or .txt
            </Button>
            {filename ? <span className="nova-risk-note">{filename}</span> : null}
            <Button variant="unstyled" type="button" className="conv-pill conv-pill--primary" disabled={!canValidate} onClick={() => void runValidation()}>
              {busy ? <><Loader2 size={13} /> Validating…</> : 'Validate'}
            </Button>
          </div>
        </section>
      )}

      {validation ? (
        <section className="nova-hooks-card" aria-label="Compatibility report">
          <div className="nova-hooks-card-head">
            <strong>{STAGE_LABELS[validation.status]}</strong>
            <span className="nova-hooks-method">{validation.validation_engine}</span>
          </div>
          <p className="nova-risk-note">
            {validation.error_count} errors · {validation.warning_count} warnings ·{' '}
            {validation.info_count} notes. TradingView compilation is pending — NOVA does
            not compile Pine, so it cannot claim this script compiled there.
          </p>
          {validation.findings.length > 0 ? (
            <ul className="nova-pine-findings">
              {validation.findings.map((finding, i) => (
                <li key={`${finding.code}-${i}`} className={`is-${finding.severity.toLowerCase()}`}>
                  <strong>{finding.title}</strong>
                  <span>{finding.explanation}</span>
                </li>
              ))}
            </ul>
          ) : null}

          {stage === 'validated' ? (
            validation.eligible_for_review ? (
              <Button variant="unstyled" type="button" className="conv-pill conv-pill--primary" disabled={busy} onClick={() => void runSubmit()}>
                Submit for admin review
              </Button>
            ) : (
              <p className="nova-sig-bad">
                Fix the errors above and validate again. A script that fails static
                validation cannot be submitted.
              </p>
            )
          ) : null}
        </section>
      ) : null}
    </div>
  )
}
