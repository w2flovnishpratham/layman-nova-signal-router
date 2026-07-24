import { AlertTriangle, Loader2, Lock } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getAutomations, saveAutomations, type AutomationsOverview, type EditableRule } from './automationsApi'

function RuleEditor({ rule, onSave }: { rule: EditableRule; onSave: (value: number | string) => void }) {
  const [draft, setDraft] = useState(String(rule.value))
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
        <input
          id={`rule-${rule.key}`}
          type={isTime ? 'time' : 'number'}
          value={draft}
          min={rule.minimum ?? undefined}
          max={rule.maximum ?? undefined}
          onChange={(e) => setDraft(e.target.value)}
        />
        <span className="nova-auto-unit">{rule.unit}</span>
        <button
          type="button"
          className="conv-pill conv-pill--primary"
          disabled={draft === String(rule.value)}
          onClick={() => onSave(isTime ? draft : Number(draft))}
        >
          Save
        </button>
      </div>
      <p className="nova-risk-note">
        {isTime
          ? `Empty means ${rule.zero_means.replace('empty means ', '')}.`
          : `0 means ${rule.zero_means}.`}
        {' '}
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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await getAutomations())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load automations.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  async function save(key: string, value: number | string) {
    setError('')
    try {
      setData(await saveAutomations({ [key]: value }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save.')
    }
  }

  return (
    <div className="nova-signals">
      <header className="nova-signals-head">
        <div>
          <h1>Automations</h1>
          <p>
            The risk controls you can change, and the safety policies you cannot.
            These are the engine&apos;s own settings — there is no separate rules engine.
          </p>
        </div>
      </header>

      {loading ? (
        <p className="nova-signals-state" role="status"><Loader2 size={16} /> Loading automations…</p>
      ) : error && !data ? (
        <p className="nova-signals-state" role="alert">
          <AlertTriangle size={16} /> {error}
          <button type="button" className="conv-pill" onClick={() => void load()}>Retry</button>
        </p>
      ) : !data ? null : (
        <>
          {error ? <p className="nova-signals-state" role="alert"><AlertTriangle size={16} /> {error}</p> : null}

          <section className="nova-hooks-card" aria-label="Editable rules">
            <div className="nova-hooks-card-head"><strong>Editable risk controls</strong></div>
            {data.editable.map((rule) => (
              <RuleEditor key={rule.key} rule={rule} onSave={(value) => void save(rule.key, value)} />
            ))}
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
