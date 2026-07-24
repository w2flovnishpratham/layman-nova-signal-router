import { Check } from 'lucide-react'
import type { SetupStep } from './setupFields'
import { setupProgress } from './setupFields'

/** Numbered rail down the left of the setup screen. Pure projection of steps. */
export function SetupStepRail({ steps }: { steps: SetupStep[] }) {
  return (
    <ol className="nova-setup-rail" aria-label="Setup steps">
      {steps.map((step, i) => (
        <li key={step.id} className={`nova-setup-rail-step is-${step.status}`}>
          <span className="nova-setup-rail-dot" aria-hidden="true">
            {step.status === 'done' ? <Check size={14} /> : i + 1}
          </span>
          <span className="sr-only">{`Step ${i + 1}: ${step.label} — ${step.status}`}</span>
        </li>
      ))}
    </ol>
  )
}

/** Live configuration panel. Every card states its real value or why it has none. */
export function SetupConfigPanel({ steps, canArm, armLabel, onArm }: {
  steps: SetupStep[]
  canArm: boolean
  armLabel: string
  onArm?: () => void
}) {
  const pct = setupProgress(steps)
  const remaining = steps.filter((s) => s.status !== 'done').map((s) => s.label.toLowerCase())

  return (
    <aside className="nova-setup-config" aria-label="Configuration">
      <div className="nova-setup-config-head">
        <h2>Configuration</h2>
        <span className="nova-setup-live">building live</span>
      </div>

      <ul className="nova-setup-cards">
        {steps.map((step) => (
          <li key={step.id} className={`nova-setup-card is-${step.status}`}>
            <div>
              <span className="nova-setup-card-label">{step.label}</span>
              <strong className="nova-setup-card-value">{step.summary}</strong>
            </div>
            <span className="nova-setup-card-mark" aria-hidden="true">
              {step.status === 'done' ? <Check size={14} /> : '○'}
            </span>
          </li>
        ))}
      </ul>

      <div className="nova-setup-progress">
        <div className="nova-setup-progress-head">
          <span>Setup progress</span>
          <strong>{pct}%</strong>
        </div>
        <div
          className="nova-setup-progress-track"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Setup progress"
        >
          <div className="nova-setup-progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <p className="nova-setup-remaining">
          {remaining.length === 0 ? 'All steps answered.' : `Remaining: ${remaining.join(', ')}.`}
        </p>
      </div>

      <button type="button" className="nova-setup-arm" disabled={!canArm} onClick={onArm}>
        {armLabel}
      </button>
      <p className="nova-setup-note">
        You can edit any answer later from Risk &amp; Settings. Nothing routes until you arm the engine.
      </p>
    </aside>
  )
}
