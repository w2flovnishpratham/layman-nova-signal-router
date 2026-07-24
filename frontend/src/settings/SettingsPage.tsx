import { AlertTriangle, Check, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import {
  CHART_TIMEFRAMES,
  TABLE_DENSITIES,
  getPreferences,
  resetPaperSession,
  savePreferences,
  type Preferences,
} from './settingsApi'

const TIMEZONES = ['Asia/Kolkata', 'UTC', 'America/New_York', 'Europe/London', 'Asia/Singapore']

export function SettingsPage() {
  const [prefs, setPrefs] = useState<Preferences | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)
  const [resetMessage, setResetMessage] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setPrefs(await getPreferences())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load settings.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  async function update(patch: Partial<Preferences>) {
    setSaved(false)
    setError('')
    try {
      setPrefs(await savePreferences(patch))
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save settings.')
    }
  }

  async function runReset() {
    setConfirmReset(false)
    setResetMessage('')
    try {
      await resetPaperSession()
      setResetMessage('Paper session reset. A final snapshot was recorded first.')
    } catch (e) {
      setResetMessage(e instanceof Error ? e.message : 'Paper reset failed.')
    }
  }

  return (
    <div className="nova-signals">
      <header className="nova-signals-head">
        <div>
          <h1>Settings</h1>
          <p>Display preferences and Paper account controls.</p>
        </div>
        {saved ? <span className="nova-sig-ok"><Check size={14} /> Saved</span> : null}
      </header>

      {loading ? (
        <p className="nova-signals-state" role="status"><Loader2 size={16} /> Loading settings…</p>
      ) : error && !prefs ? (
        <p className="nova-signals-state" role="alert">
          <AlertTriangle size={16} /> {error}
          <button type="button" className="conv-pill" onClick={() => void load()}>Retry</button>
        </p>
      ) : !prefs ? null : (
        <>
          {error ? <p className="nova-signals-state" role="alert"><AlertTriangle size={16} /> {error}</p> : null}

          <section className="nova-hooks-card" aria-label="Display">
            <div className="nova-hooks-card-head"><strong>Display</strong></div>
            <div className="nova-set-grid">
              <label htmlFor="pref-tz">Timezone
                <select id="pref-tz" value={prefs.timezone} onChange={(e) => void update({ timezone: e.target.value })}>
                  {TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
                </select>
              </label>
              <label htmlFor="pref-density">Table density
                <select id="pref-density" value={prefs.table_density} onChange={(e) => void update({ table_density: e.target.value })}>
                  {TABLE_DENSITIES.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </label>
              <label htmlFor="pref-timeframe">Default chart timeframe
                <select id="pref-timeframe" value={prefs.default_chart_timeframe} onChange={(e) => void update({ default_chart_timeframe: e.target.value })}>
                  {CHART_TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              <label htmlFor="pref-motion" className="nova-set-toggle">
                <input
                  id="pref-motion"
                  type="checkbox"
                  checked={prefs.reduced_motion}
                  onChange={(e) => void update({ reduced_motion: e.target.checked })}
                />
                Reduced motion
              </label>
            </div>
          </section>

          <section className="nova-hooks-card" aria-label="Notifications">
            <div className="nova-hooks-card-head"><strong>Notifications</strong></div>
            {prefs.channels.map((channel) => (
              <label key={channel.key} className="nova-set-toggle">
                <input
                  type="checkbox"
                  checked={Boolean(prefs.notification_preferences[channel.key])}
                  onChange={(e) => void update({
                    notification_preferences: { ...prefs.notification_preferences, [channel.key]: e.target.checked },
                  })}
                />
                {channel.label}
                {/* Stated plainly: the preference is stored, nothing is delivered. */}
                {!channel.available ? <small>{channel.reason}</small> : null}
              </label>
            ))}
          </section>

          <section className="nova-hooks-card" aria-label="Paper account">
            <div className="nova-hooks-card-head"><strong>Paper account</strong></div>
            <p>
              Resets the simulated balance and clears the Paper session after recording a
              final snapshot. The server refuses while the engine is running, a Paper
              position is open, or an exit is pending. Live credentials and broker data
              are never touched.
            </p>
            <div className="nova-cred-actions">
              <button type="button" className="conv-pill nova-cred-danger" onClick={() => setConfirmReset(true)}>
                Reset Paper account
              </button>
            </div>
            {confirmReset ? (
              <div className="nova-cred-confirm" role="alertdialog" aria-label="Confirm Paper reset">
                <p>This clears your Paper trades and balance. It cannot be undone.</p>
                <div className="nova-cred-actions">
                  <button type="button" className="conv-pill nova-cred-danger" onClick={() => void runReset()}>
                    Yes, reset Paper
                  </button>
                  <button type="button" className="conv-pill" onClick={() => setConfirmReset(false)}>Cancel</button>
                </div>
              </div>
            ) : null}
            {resetMessage ? <p role="status" className="nova-risk-note">{resetMessage}</p> : null}
          </section>
        </>
      )}
    </div>
  )
}
