import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Slider } from '@/components/ui/slider'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { toast } from '@/components/ui/toast'
import { Check, CircleAlert, Loader2, ShieldX } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { RuntimeStatus } from '../api'
import { PageSkeleton } from '../components/PageSkeleton'
import {
  getRiskPageData,
  paise,
  rupees,
  saveRiskConfiguration,
  triggerRiskKillSwitch,
  type RiskConfiguration,
  type RiskExitMode,
  type RiskMode,
  type RiskPageOverview,
  type RiskPreset,
  type RiskPresetKey,
  type RiskUtilisation,
  type RiskValues,
} from './riskApi'

const SOURCE_LABELS: Record<string, string> = {
  SETUP: 'Setup',
  RISK_PAGE: 'Risk Page',
  TRADING_TERMINAL: 'Trading Terminal',
  ADMIN: 'Admin',
  SYSTEM_MIGRATION: 'System migration',
}

const SAFETY_RULES = [
  'Margin validation before every entry — orders are rejected if insufficient.',
  'Entries are blocked outside 09:15–15:30 IST market hours.',
  'Forced square-off runs at 15:12 IST for INTRADAY products.',
  'Duplicate signals are dropped inside the protected replay window.',
  'Live mode requires verified Dhan credentials and Nova Static IP.',
]

function sameValues(left: RiskValues, right: RiskValues): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

function lastSaved(value: string | null): string {
  if (!value) return 'Suggested default — not saved yet'
  return new Intl.DateTimeFormat('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Asia/Kolkata',
  }).format(new Date(value))
}

function lotsLabel(values: RiskValues): string {
  return values.lots_per_trade_min === values.lots_per_trade_max
    ? String(values.lots_per_trade_min)
    : `${values.lots_per_trade_min}–${values.lots_per_trade_max}`
}

function numberField(
  draft: RiskValues,
  key: keyof RiskValues,
  setDraft: React.Dispatch<React.SetStateAction<RiskValues | null>>,
) {
  return {
    value: draft[key] ?? '',
    onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
      const value = event.target.value === '' ? null : Number(event.target.value)
      setDraft((current) => current ? { ...current, [key]: value } : current)
    },
  }
}

function PresetCard({
  preset,
  selected,
  active,
  onSelect,
}: {
  preset: RiskPreset
  selected: boolean
  active: boolean
  onSelect: () => void
}) {
  return (
    <Button
      variant="unstyled"
      type="button"
      className={`nova-risk-preset is-${preset.key.toLowerCase()}${selected ? ' is-selected' : ''}`}
      aria-pressed={selected}
      onClick={onSelect}
    >
      <span className="nova-risk-preset-head">
        <strong>{preset.name}</strong>
        {active ? <Badge variant="unstyled">ACTIVE</Badge> : null}
      </span>
      <small>{preset.description}</small>
      <dl>
        <div><dt>Daily loss cap</dt><dd>{rupees(preset.values.daily_loss_cap)}</dd></div>
        <div><dt>Max trades / day</dt><dd>{preset.values.max_trades_per_day}</dd></div>
        <div><dt>Lots per trade</dt><dd>{lotsLabel(preset.values)}</dd></div>
        <div><dt>Cooldown after loss</dt><dd>{preset.values.cooldown_minutes}m</dd></div>
      </dl>
    </Button>
  )
}

function UsageMeter({
  label,
  utilisation,
  money = false,
  pressure = false,
}: {
  label: string
  utilisation: RiskUtilisation
  money?: boolean
  pressure?: boolean
}) {
  const used = money ? paise(utilisation.used) : utilisation.used.toLocaleString('en-IN')
  const limit = utilisation.unlimited
    ? 'Not configured'
    : money ? paise(utilisation.limit) : utilisation.limit.toLocaleString('en-IN')
  const pct = utilisation.pct ?? 0
  const clampedPct = Math.min(100, Math.max(0, pct))
  const pressureHue = Number((clampedPct <= 50
    ? 44 - clampedPct * 0.32
    : 28 - (clampedPct - 50) * 0.56).toFixed(3))
  const style = pressure
    ? ({ '--usage-color': `hsl(${pressureHue} 92% 62%)` } as React.CSSProperties)
    : undefined
  return (
    <div className="nova-risk-usage-row" data-pressure={pressure || undefined} style={style}>
      <div>
        <span>{label}</span>
        <strong>
          {used} / {limit}
          {utilisation.pct === null ? null : <span> · {utilisation.pct}%</span>}
        </strong>
      </div>
      <progress max={100} value={pct} aria-label={`${label}: ${used} of ${limit}`} />
    </div>
  )
}

function KillSwitch({
  mode,
  enabled,
  onComplete,
}: {
  mode: RiskMode
  enabled: boolean
  onComplete: () => Promise<void>
}) {
  const timer = useRef<number | null>(null)
  const [holding, setHolding] = useState(false)
  const [pending, setPending] = useState(false)

  function cancel() {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
    setHolding(false)
  }

  function start() {
    if (pending || !enabled) return
    setHolding(true)
    timer.current = window.setTimeout(async () => {
      setHolding(false)
      setPending(true)
      try {
        await onComplete()
      } finally {
        setPending(false)
      }
    }, 800)
  }

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
  }, [])

  return (
    <section className="nova-risk-panel nova-risk-kill">
      <div className="nova-risk-kill-head">
        <span><ShieldX size={16} /></span>
        <div><h2>Kill Switch</h2><p>Stop {mode} routing and square off</p></div>
      </div>
      <p>Immediately blocks new entries and exits NOVA’s tracked open position at market. The engine remains stopped until you restart it.</p>
      <Button
        variant="unstyled"
        type="button"
        className={holding ? 'is-holding' : ''}
        disabled={pending || !enabled}
        onPointerDown={start}
        onPointerUp={cancel}
        onPointerLeave={cancel}
        onKeyDown={(event) => {
          if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) start()
        }}
        onKeyUp={cancel}
      >
        {pending ? <Loader2 size={14} /> : null}
        {pending ? 'Stopping…' : enabled ? 'Hold to Stop & Square Off' : 'Engine Stopped'}
      </Button>
      <small>{enabled ? 'Hold for 0.8s to confirm' : `No running ${mode} engine.`}</small>
    </section>
  )
}

export function RiskPage({ runtime }: { runtime: RuntimeStatus | null }) {
  const search = new URLSearchParams(window.location.search)
  const [mode, setMode] = useState<RiskMode>(search.get('mode') === 'live' ? 'live' : 'paper')
  const [presets, setPresets] = useState<RiskPreset[]>([])
  const [configuration, setConfiguration] = useState<RiskConfiguration | null>(null)
  const [overview, setOverview] = useState<RiskPageOverview | null>(null)
  const [draft, setDraft] = useState<RiskValues | null>(null)
  const [selectedPreset, setSelectedPreset] = useState<RiskPresetKey>('BALANCED')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await getRiskPageData(mode)
      setPresets(result.presets)
      setConfiguration(result.configuration)
      setOverview(result.overview)
      setDraft(result.configuration.values)
      setSelectedPreset(result.configuration.basedOnPreset)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Could not load risk settings.')
    } finally {
      setLoading(false)
    }
  }, [mode])

  useEffect(() => {
    const query = new URLSearchParams(window.location.search)
    query.set('mode', mode)
    window.history.replaceState(null, '', `${window.location.pathname}?${query}`)
    void load()
  }, [load, mode])

  const selectedTemplate = useMemo(
    () => presets.find((preset) => preset.key === selectedPreset) ?? null,
    [presets, selectedPreset],
  )
  const dirty = Boolean(configuration && draft && !sameValues(configuration.values, draft))
  const customDraft = Boolean(draft && selectedTemplate && !sameValues(draft, selectedTemplate.values))

  function selectPreset(preset: RiskPreset) {
    setSelectedPreset(preset.key)
    setDraft(structuredClone(preset.values))
  }

  async function save() {
    if (!configuration || !draft) return
    setSaving(true)
    try {
      await saveRiskConfiguration({
        mode,
        basedOnPreset: selectedPreset,
        values: draft,
        changeSource: 'RISK_PAGE',
        expectedVersion: configuration.activeVersion,
      })
      await load()
      toast.add({
        title: 'Risk settings saved.',
        description: 'New entries now use this version.',
        type: 'success',
      })
    } catch (saveError) {
      toast.add({
        title: saveError instanceof Error ? saveError.message : 'Could not save risk settings.',
        type: 'error',
      })
    } finally {
      setSaving(false)
    }
  }

  function revert() {
    if (!configuration) return
    setDraft(structuredClone(configuration.values))
    setSelectedPreset(configuration.basedOnPreset)
  }

  return (
    <div className="nova-signals nova-risk-page">
      <header className="nova-signals-head nova-risk-head">
        <div>
          <h1>Risk Management</h1>
          <p>Control account-level limits, position sizing and exit protection. Changes apply to future trades.</p>
        </div>
        <Tabs value={mode} onValueChange={(value) => setMode(value as RiskMode)} variant="unstyled">
          <TabsList variant="unstyled" className="nova-risk-mode" data-mode={mode}>
            <TabsTrigger variant="unstyled" value="paper">Paper</TabsTrigger>
            <TabsTrigger variant="unstyled" value="live">Live</TabsTrigger>
          </TabsList>
        </Tabs>
      </header>

      {loading ? (
        <PageSkeleton label="Loading risk settings" variant="split-form" />
      ) : error && !draft ? (
        <p className="nova-signals-state" role="alert"><CircleAlert size={16} /> {error}
          <Button variant="unstyled" className="conv-pill" onClick={() => void load()}>Retry</Button>
        </p>
      ) : !configuration || !draft || !overview ? null : (
        <>
          <section aria-labelledby="risk-profiles-title">
            <div className="nova-risk-section-head">
              <div><h2 id="risk-profiles-title">Risk Profiles</h2><p>Fixed system templates. Selecting one loads it into the editor without saving.</p></div>
            </div>
            <div className="nova-risk-presets">
              {presets.map((preset) => (
                <PresetCard
                  key={preset.key}
                  preset={preset}
                  selected={selectedPreset === preset.key}
                  active={!dirty && configuration.profileType === preset.key}
                  onSelect={() => selectPreset(preset)}
                />
              ))}
            </div>
          </section>

          <div className="nova-risk-layout">
            <div className="nova-risk-primary">
              <section className="nova-risk-panel nova-risk-editor">
                <div className="nova-risk-editor-head">
                  <div>
                    <h2>Active Risk Settings</h2>
                    <p>
                      <strong>{customDraft ? 'Custom' : selectedTemplate?.name}</strong>
                      {' · based on '}{selectedTemplate?.name}
                      {dirty ? ' · Unsaved changes' : ''}
                    </p>
                  </div>
                  <Badge variant="unstyled">Version {configuration.activeVersion || 'suggested'}</Badge>
                </div>
                <p className="nova-risk-meta">
                  {lastSaved(configuration.updatedAt)}
                  {configuration.changeSource ? ` · Updated from ${SOURCE_LABELS[configuration.changeSource] ?? configuration.changeSource}` : ''}
                </p>

                <fieldset>
                  <legend>Account protection</legend>
                  <div className="nova-risk-fields">
                    <label>Maximum daily loss (₹)<Input type="number" min={1} step={500} {...numberField(draft, 'daily_loss_cap', setDraft)} /></label>
                    <label>Maximum trades per day<Input type="number" min={1} max={50} {...numberField(draft, 'max_trades_per_day', setDraft)} /></label>
                    <label>Maximum open positions<Input type="number" min={1} max={20} {...numberField(draft, 'max_open_positions', setDraft)} /></label>
                    <label>Cooldown after loss (minutes)<Input type="number" min={0} max={1440} {...numberField(draft, 'cooldown_minutes', setDraft)} /></label>
                  </div>
                </fieldset>

                <fieldset>
                  <legend>Position sizing</legend>
                  <div className="nova-risk-fields">
                    <label>Minimum lots per trade<Input type="number" min={1} max={20} {...numberField(draft, 'lots_per_trade_min', setDraft)} /></label>
                    <label>Maximum lots per trade<Input type="number" min={1} max={20} {...numberField(draft, 'lots_per_trade_max', setDraft)} /></label>
                    <label>Maximum loss per trade (₹)<Input type="number" min={1} step={100} {...numberField(draft, 'max_loss_per_trade', setDraft)} /></label>
                    <label>Margin exposure cap (₹, optional)<Input type="number" min={1} step={1000} placeholder="Broker/account limit" {...numberField(draft, 'margin_exposure_cap', setDraft)} /></label>
                  </div>
                </fieldset>

                <fieldset>
                  <legend>Exit protection</legend>
                  <div className="nova-risk-exit-modes" role="radiogroup" aria-label="Exit protection mode">
                    {([
                      ['FLIPS_ONLY', 'Flips Only', 'Exit only on an opposite signal'],
                      ['TARGET_PROFIT', 'Target Profit', 'Take profit without a stop loss'],
                      ['CUSTOM_SL_TP', 'Custom SL & TP', 'Server-managed entry snapshot'],
                    ] as Array<[RiskExitMode, string, string]>).map(([value, label, description]) => (
                      <Button
                        variant="unstyled"
                        type="button"
                        role="radio"
                        aria-checked={draft.exit_mode === value}
                        key={value}
                        onClick={() => setDraft((current) => current ? { ...current, exit_mode: value } : current)}
                      >
                        <strong>{label}</strong><small>{description}</small>
                      </Button>
                    ))}
                  </div>
                  {draft.exit_mode !== 'FLIPS_ONLY' ? (
                    <div className="nova-risk-sliders">
                      {draft.exit_mode === 'CUSTOM_SL_TP' ? (
                        <label>
                          <span>Stop loss ({draft.stop_loss_basis.toLowerCase()}) <strong>{draft.stop_loss_value}</strong></span>
                          <Slider className="nova-risk-slider is-stop" aria-label="Stop loss points" min={1} max={200} value={draft.stop_loss_value ?? 1} onValueChange={(value) => setDraft({ ...draft, stop_loss_value: value })} />
                        </label>
                      ) : null}
                      <label>
                        <span>Take profit ({draft.take_profit_basis.toLowerCase()}) <strong>{draft.take_profit_value}</strong></span>
                        <Slider className="nova-risk-slider is-target" aria-label="Take profit points" min={1} max={400} value={draft.take_profit_value ?? 1} onValueChange={(value) => setDraft({ ...draft, take_profit_value: value })} />
                      </label>
                    </div>
                  ) : null}
                </fieldset>

                <div className="nova-risk-actions">
                  <Button variant="unstyled" type="button" className="nova-risk-save" disabled={!dirty || saving} onClick={() => void save()}>
                    {saving ? <Loader2 size={14} /> : null}{saving ? 'Saving…' : 'Save Risk Settings'}
                  </Button>
                  <Button variant="unstyled" type="button" className="conv-pill" disabled={!dirty || saving} onClick={revert}>Revert Unsaved Changes</Button>
                  <Button variant="unstyled" type="button" className="conv-pill" disabled={!selectedTemplate || saving} onClick={() => selectedTemplate && selectPreset(selectedTemplate)}>
                    Reset to {selectedTemplate?.name} Preset
                  </Button>
                </div>
              </section>

              <section className="nova-risk-panel nova-risk-usage-panel">
                <h2>Today’s Usage</h2>
                <div className="nova-risk-usage">
                  <UsageMeter label="Daily loss" utilisation={overview.today_usage.daily_loss} money pressure />
                  <UsageMeter label="Trades taken" utilisation={overview.today_usage.trades} pressure />
                  <UsageMeter label="Open positions" utilisation={overview.today_usage.open_positions} />
                  <UsageMeter label="Margin exposure" utilisation={overview.today_usage.margin_exposure} money />
                </div>
              </section>
            </div>

            <aside className="nova-risk-aside">
              <KillSwitch
                key={`${mode}-${Boolean(runtime?.engine.running && runtime.engine.mode === mode)}`}
                mode={mode}
                enabled={Boolean(runtime?.engine.running && runtime.engine.mode === mode)}
                onComplete={async () => {
                  try {
                    const result = await triggerRiskKillSwitch(mode)
                    toast.add({ title: result.outcome, type: 'success' })
                    await load()
                  } catch (killError) {
                    toast.add({
                      title: killError instanceof Error ? killError.message : 'Kill switch failed.',
                      type: 'error',
                    })
                  }
                }}
              />

              <section className="nova-risk-panel nova-risk-safety">
                <h2>Safety Rules <span>(always on)</span></h2>
                <ul>{SAFETY_RULES.map((rule) => <li key={rule}><Check size={14} />{rule}</li>)}</ul>
              </section>

              <section className="nova-risk-panel nova-risk-history">
                <h2>Breaker History</h2>
                {overview.breaker_history.length ? (
                  <div>{overview.breaker_history.map((event) => (
                    <article key={`${event.timestamp}-${event.trigger_type}`}>
                      <time>{new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: 'short', timeZone: 'Asia/Kolkata' }).format(new Date(event.timestamp))}</time>
                      <p>{event.event}</p>
                      <Badge variant="unstyled">{event.outcome}</Badge>
                    </article>
                  ))}</div>
                ) : <p className="nova-risk-empty">No breaker events recorded for this account.</p>}
              </section>
            </aside>
          </div>
        </>
      )}
    </div>
  )
}
