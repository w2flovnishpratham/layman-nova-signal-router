import { useEffect, useState } from 'react'
import { Pencil, Save, X } from 'lucide-react'
import type { RuntimeStatus } from '../api'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { toast } from '../components/ui/toast'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../components/ui/tooltip'
import {
  getRiskConfiguration,
  getRiskOverview,
  paise,
  rupees,
  saveRiskConfiguration,
  type RiskConfiguration,
  type RiskMode,
  type RiskUtilisation,
} from '../risk/riskApi'

export function RiskAutomationCard({ runtime }: { runtime: RuntimeStatus | null }) {
  const mode = runtime?.engine.mode as RiskMode | undefined
  const [configuration, setConfiguration] = useState<RiskConfiguration | null>(null)
  const [usage, setUsage] = useState<Record<string, RiskUtilisation> | null>(null)
  const [editing, setEditing] = useState(false)
  const [dailyLoss, setDailyLoss] = useState('')
  const [maxTrades, setMaxTrades] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!mode) return
    let current = true
    Promise.all([getRiskConfiguration(mode), getRiskOverview(mode)])
      .then(([next, overview]) => {
        if (!current) return
        setConfiguration(next)
        setUsage(overview.today_usage ?? null)
        setDailyLoss(String(next.values.daily_loss_cap))
        setMaxTrades(String(next.values.max_trades_per_day))
      })
      .catch((reason) => { if (current) setError(reason instanceof Error ? reason.message : 'Risk settings unavailable.') })
    return () => { current = false }
  }, [mode])

  async function save() {
    if (!mode || !configuration) return
    setError('')
    try {
      const next = await saveRiskConfiguration({
        mode,
        basedOnPreset: configuration.basedOnPreset,
        values: {
          ...configuration.values,
          daily_loss_cap: Number(dailyLoss),
          max_trades_per_day: Number(maxTrades),
        },
        changeSource: 'TRADING_TERMINAL',
        expectedVersion: configuration.activeVersion,
      })
      setConfiguration(next)
      setEditing(false)
      toast.add({ title: 'Risk settings saved.', type: 'success' })
    } catch (reason) {
      toast.add({
        title: reason instanceof Error ? reason.message : 'Risk settings could not be saved.',
        type: 'error',
      })
    }
  }

  const values = configuration?.values
  const cutoff = String(runtime?.config.active?.entry_cutoff_ist || 'No cutoff')
  return (
    <section className="sidebar-card terminal-risk-card">
      <div className="sidebar-title">
        <span>Risk Controls</span>
        {editing ? (
          <Button variant="unstyled" type="button" aria-label="Cancel risk changes" onClick={() => setEditing(false)}><X size={13} /></Button>
        ) : (
          <Button variant="unstyled" type="button" aria-label="Edit shared risk settings" disabled={!configuration} onClick={() => setEditing(true)}><Pencil size={13} /></Button>
        )}
      </div>
      {editing ? (
        <dl>
          <div><dt>Daily loss cap</dt><dd><Input aria-label="Daily loss cap" type="number" min={1} value={dailyLoss} onChange={(event) => setDailyLoss(event.target.value)} /></dd></div>
          <div><dt>Max trades / day</dt><dd><Input aria-label="Maximum trades per day" type="number" min={1} max={50} value={maxTrades} onChange={(event) => setMaxTrades(event.target.value)} /></dd></div>
          <div><dt>Entry cutoff</dt><dd>{cutoff === 'No cutoff' ? cutoff : `${cutoff} IST`}</dd></div>
        </dl>
      ) : (
        <TooltipProvider delay={120}>
          <div className="terminal-risk-meters">
            <RiskMeter label="Max Daily Loss" value={usage?.daily_loss} fallbackLimit={values?.daily_loss_cap} money />
            <RiskMeter label="Max Trades / Day" value={usage?.trades} fallbackLimit={values?.max_trades_per_day} />
            <RiskMeter label="Max Open Positions" value={usage?.open_positions} fallbackLimit={values?.max_open_positions} />
            <RiskMeter label="Margin Exposure" value={usage?.margin_exposure} fallbackLimit={values?.margin_exposure_cap ?? undefined} money />
          </div>
        </TooltipProvider>
      )}
      {editing ? <Button variant="unstyled" className="terminal-risk-save" type="button" onClick={() => void save()}><Save size={13} /> Save shared limits</Button> : null}
      {error ? <p className="terminal-risk-error" role="alert">{error}</p> : null}
    </section>
  )
}

function RiskMeter({ label, value, fallbackLimit, money = false }: { label: string; value?: RiskUtilisation; fallbackLimit?: number; money?: boolean }) {
  const used = value?.used ?? 0
  const limit = value?.limit ?? fallbackLimit ?? 0
  const pct = Math.max(0, Math.min(100, value?.pct ?? (limit > 0 ? used / limit * 100 : 0)))
  const format = (amount: number) => money ? (value ? paise(amount) : rupees(amount)) : amount.toLocaleString('en-IN')
  return (
    <div>
      <div>
        <span>{label} <small>{limit > 0 ? format(limit) : 'No cap'}</small></span>
        <Tooltip>
          <TooltipTrigger render={<strong tabIndex={0}>{pct.toFixed(pct < 10 ? 1 : 0)}% used</strong>} />
          <TooltipContent side="top" sideOffset={8}>
            {format(used)}{limit > 0 ? ` / ${format(limit)}` : ''} used
          </TooltipContent>
        </Tooltip>
      </div>
      <div className="terminal-risk-track"><span style={{ width: `${pct}%` }} /></div>
    </div>
  )
}
