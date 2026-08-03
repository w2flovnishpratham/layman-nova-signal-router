import { Input } from "@/components/ui/input"
import { Button } from '@/components/ui/button'
import { toast } from '@/components/ui/toast'
import { AlertTriangle, Check, Loader2, Lock } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { PageSkeleton } from '../components/PageSkeleton'
import {
  getAutomations,
  saveAutomations,
  type AutomationsOverview,
  type EditableRule,
} from './automationsApi'

function RuleEditor({
  rule,
  value,
  onChange,
}: {
  rule: EditableRule
  value: string
  onChange: (value: string) => void
}) {
  const isTime = rule.key === 'entry_cutoff_ist'

  return (
    <div className="nova-auto-rule">
      <div className="nova-auto-rule-head">
        <strong>{rule.label}</strong>
        <span className="nova-auto-effect">{rule.effect}</span>
      </div>
      <p className="nova-risk-note">{rule.basis}</p>
      <div className="nova-auto-edit">
        <label className="sr-only" htmlFor={`rule-${rule.key}`}>{rule.label}</label>
        <Input variant="unstyled"
          id={`rule-${rule.key}`}
          type={isTime ? 'time' : 'number'}
          value={value}
          min={rule.minimum ?? undefined}
          max={rule.maximum ?? undefined}
          onChange={(event) => onChange(event.target.value)}
        />
        <span className="nova-auto-unit">{rule.unit}</span>
      </div>
      <p className="nova-risk-note">
        {isTime
          ? `Empty means ${rule.zero_means.replace('empty means ', '')}.`
          : `0 means ${rule.zero_means}.`}{' '}
        {rule.affects_open_position
          ? 'Can act on the position that is open now.'
          : 'Does not touch the position that is open now.'}
        {rule.requires_restart ? ' Requires an engine restart.' : ''}
      </p>
    </div>
  )
}

export function AutomationsPage() {
  const [data, setData] = useState<AutomationsOverview | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [reviewing, setReviewing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const hydrate = useCallback((next: AutomationsOverview) => {
    setData(next)
    setDraft(Object.fromEntries(next.editable.map((rule) => [rule.key, String(rule.value)])))
    setReviewing(false)
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      hydrate(await getAutomations())
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : 'Could not load automations.')
    } finally {
      setLoading(false)
    }
  }, [hydrate])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  const changed = useMemo(() => {
    if (!data) return {}
    return Object.fromEntries(data.editable.flatMap((rule) => {
      const value = draft[rule.key] ?? String(rule.value)
      if (value === String(rule.value)) return []
      return [[rule.key, rule.key === 'entry_cutoff_ist' ? value : Number(value)]]
    }))
  }, [data, draft])
  const changedKeys = Object.keys(changed)

  async function confirmUpdate() {
    if (!data?.configuration_id || data.configuration_revision == null || changedKeys.length === 0) return
    setSaving(true)
    try {
      hydrate(await saveAutomations(data.configuration_id, data.configuration_revision, changed))
      toast.add({ title: 'Automation settings saved.', type: 'success' })
    } catch (exception) {
      toast.add({
        title: exception instanceof Error ? exception.message : 'Could not save.',
        type: 'error',
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="nova-signals">
      <header className="nova-signals-head">
        <div>
          <h1>Automations</h1>
          <p>
            Confirmed changes create one revision and apply only to future automated entries.
            Protected safety policies remain enforced by the engine.
          </p>
        </div>
      </header>

      {loading ? (
        <PageSkeleton label="Loading automations" variant="list" />
      ) : error && !data ? (
        <p className="nova-signals-state" role="alert">
          <AlertTriangle size={16} /> {error}
          <Button variant="unstyled" type="button" className="conv-pill" onClick={() => void load()}>Retry</Button>
        </p>
      ) : !data ? null : (
        <>
          {error ? <p className="nova-signals-state" role="alert"><AlertTriangle size={16} /> {error}</p> : null}

          <section className="nova-hooks-card" aria-label="Editable rules">
            <div className="nova-hooks-card-head">
              <strong>Editable risk controls</strong>
              {data.configuration_revision != null ? <span>Revision {data.configuration_revision}</span> : null}
            </div>
            {data.editable.map((rule) => (
              <RuleEditor
                key={rule.key}
                rule={rule}
                value={draft[rule.key] ?? String(rule.value)}
                onChange={(value) => {
                  setDraft((current) => ({ ...current, [rule.key]: value }))
                  setReviewing(false)
                }}
              />
            ))}
            {!data.configuration_id ? (
              <p className="nova-signals-state" role="alert">
                Save a Trading setup before editing Automation Settings.
              </p>
            ) : null}
            <div className="nova-auto-actions">
              <Button variant="unstyled"
                type="button"
                className="conv-pill conv-pill--primary"
                disabled={!data.configuration_id || changedKeys.length === 0}
                onClick={() => setReviewing(true)}
              >
                Review Changes
              </Button>
            </div>

            {reviewing ? (
              <div className="nova-auto-review" role="region" aria-label="Review automation changes">
                <div className="nova-hooks-card-head">
                  <strong>Review Changes</strong>
                  <span>Revision {data.configuration_revision} → {Number(data.configuration_revision) + 1}</span>
                </div>
                <ul>
                  {changedKeys.map((key) => {
                    const rule = data.editable.find((candidate) => candidate.key === key)
                    return (
                      <li key={key}>
                        <span>{rule?.label ?? key}</span>
                        <strong>{String(rule?.value)} → {String(changed[key])}</strong>
                      </li>
                    )
                  })}
                </ul>
                <p className="nova-risk-note">
                  This revision applies only to future automated entries. It will not change the
                  current open position, confirmed SL/TP, protection state, orders, or trade history.
                </p>
                <div className="nova-auto-actions">
                  <Button variant="unstyled"
                    type="button"
                    className="conv-pill"
                    disabled={saving}
                    onClick={() => setReviewing(false)}
                  >
                    Cancel
                  </Button>
                  <Button variant="unstyled"
                    type="button"
                    className="conv-pill conv-pill--primary"
                    disabled={saving}
                    onClick={() => void confirmUpdate()}
                  >
                    {saving ? <Loader2 size={15} className="spin" /> : <Check size={15} />}
                    Confirm Update
                  </Button>
                </div>
              </div>
            ) : null}
          </section>

          <section className="nova-hooks-card" aria-label="Protected rules">
            <div className="nova-hooks-card-head">
              <Lock size={15} />
              <strong>Protected system rules</strong>
            </div>
            <p className="nova-risk-note">
              These cannot be disabled or edited. They exist because switching them off is
              how positions get lost, duplicated or exited at prices that never existed.
            </p>
            <ul className="nova-auto-protected">
              {data.protected.map((rule) => (
                <li key={rule.key}>
                  <div className="nova-auto-rule-head">
                    <strong>{rule.label}</strong>
                    <span className="nova-auto-effect is-protected">{rule.effect}</span>
                  </div>
                  <p>{rule.description}</p>
                  <small>{rule.why_protected}</small>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  )
}
