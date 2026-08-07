import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { toast } from '@/components/ui/toast'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronDown, Info, Minus, Plus } from 'lucide-react'
import type { CSSProperties } from 'react'
import { useEffect, useState } from 'react'
import type { RuntimeStatus } from '../api'
import { softEase, useAppReducedMotion } from '../components/MotionPrimitives'
import type { SideFilter } from '../types'

export interface EngineConfigValues {
  lots: number
  stopLossPercent: number
  takeProfitPercent: number
  maxTradesPerDay: number
}

export function EngineConfigCard({ runtime, onStop, onSaveConfig, side, onSideChange, actionPending = false }: {
  runtime: RuntimeStatus | null
  onStop: () => void
  onSaveConfig: (values: EngineConfigValues) => Promise<void>
  side: SideFilter
  onSideChange: (side: SideFilter) => void
  actionPending?: boolean
}) {
  const active = runtime?.config.active ?? {}
  const values = configValues(active)
  const [draft, setDraft] = useState(values)
  const [saving, setSaving] = useState(false)
  const [collapsed, setCollapsed] = useState(true)
  const reduceMotion = useAppReducedMotion()

  useEffect(() => setDraft(values), [values.lots, values.maxTradesPerDay, values.stopLossPercent, values.takeProfitPercent])

  async function save() {
    setSaving(true)
    try {
      await onSaveConfig(draft)
      const positionOpen = Boolean(runtime?.position?.has_open_position)
      toast.add({
        title: positionOpen
          ? 'Engine configuration saved. Your open position keeps its original stop/target -- this only applies to entries from here on.'
          : 'Engine configuration saved.',
        type: 'success',
      })
    } catch (error) {
      toast.add({ title: error instanceof Error ? error.message : 'Configuration could not be saved.', type: 'error' })
    } finally {
      setSaving(false)
    }
  }
  return (
    <section className="sidebar-card terminal-engine-card">
      <div className="sidebar-title">
        <span>Engine</span>
        <span className="terminal-card-actions">
          <strong>{runtime?.engine.state ?? 'LOADING'}</strong>
          <Button
            variant="unstyled"
            type="button"
            className="terminal-card-collapse-toggle"
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Expand engine configuration' : 'Collapse engine configuration'}
            onClick={() => setCollapsed((current) => !current)}
          >
            <motion.span animate={{ rotate: collapsed ? 0 : 180 }} transition={reduceMotion ? { duration: 0 } : { duration: 0.18, ease: softEase }}>
              <ChevronDown size={14} />
            </motion.span>
          </Button>
        </span>
      </div>
      <AnimatePresence initial={false}>
        {!collapsed ? (
          <motion.div
            className="terminal-engine-collapsible"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={reduceMotion ? { duration: 0 } : { duration: 0.22, ease: softEase }}
          >
            <label className="terminal-engine-strategy">
              <span>Selected strategy</span>
              <strong>{runtime?.selected_strategy?.display_name ?? 'No strategy selected'}</strong>
            </label>
            <div>
              <span className="terminal-engine-label">Allowed sides</span>
              <div className="terminal-side-options" role="group" aria-label="Allowed automated option sides">
                {(['CE', 'PE', 'BOTH'] as SideFilter[]).map((option) => (
                  <Button variant="unstyled" key={option} type="button" aria-pressed={side === option} onClick={() => onSideChange(option)}>{option}</Button>
                ))}
              </div>
            </div>
            <div className="terminal-engine-values">
              <label>
                <span>Lots</span>
                <div className="terminal-lot-stepper" role="group" aria-label="Lots">
                  <Button variant="unstyled" type="button" aria-label="Decrease lots" disabled={draft.lots <= 1} onClick={() => setDraft({ ...draft, lots: Math.max(1, draft.lots - 1) })}>
                    <Minus size={13} />
                  </Button>
                  <output aria-live="polite">{draft.lots}</output>
                  <Button variant="unstyled" type="button" aria-label="Increase lots" disabled={draft.lots >= 20} onClick={() => setDraft({ ...draft, lots: Math.min(20, draft.lots + 1) })}>
                    <Plus size={13} />
                  </Button>
                </div>
              </label>
              <ConfigInput
                label="Default SL (%)"
                value={draft.stopLossPercent}
                min={0.1}
                max={69.9}
                step={0.1}
                onChange={(stopLossPercent) => setDraft({ ...draft, stopLossPercent })}
                hint="The actual stop on a trade can end up tighter than this: your Risk Controls' Max Loss Per Trade cap (set separately, on the Risk page) overrides this percentage whenever it would trigger a smaller loss."
              />
              <ConfigInput label="Default TP (%)" value={draft.takeProfitPercent} min={0.1} max={1000} step={0.1} onChange={(takeProfitPercent) => setDraft({ ...draft, takeProfitPercent })} />
              <ConfigInput label="Max trades / day" value={draft.maxTradesPerDay} min={0} max={50} onChange={(maxTradesPerDay) => setDraft({ ...draft, maxTradesPerDay })} />
            </div>
            <Button variant="unstyled" type="button" className="terminal-save-config" disabled={saving || runtime?.engine.state !== 'STOPPED'} onClick={() => void save()}>
              {saving ? 'Saving…' : runtime?.engine.state === 'STOPPED' ? 'Save configuration' : 'Stop engine to save'}
            </Button>
            <Button variant="unstyled" type="button" className="terminal-stop-engine" loading={actionPending} onClick={onStop}>Stop Engine</Button>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  )
}

function ConfigInput({ label, value, min, max, step = 1, onChange, hint }: {
  label: string
  value: number
  min: number
  max: number
  step?: number
  onChange: (value: number) => void
  hint?: string
}) {
  return (
    <label>
      <span>
        {label}
        {hint ? (
          <TooltipProvider delay={120}>
            <Tooltip>
              <TooltipTrigger render={<Info className="terminal-hint-icon" size={12} tabIndex={0} aria-label={`${label} note`} />} />
              <TooltipContent side="top" sideOffset={8} className="max-w-64">{hint}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : null}
      </span>
      <Input type="number" value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  )
}

export function DailyDrawdownCard({ runtime }: { runtime: RuntimeStatus | null }) {
  const active = runtime?.config.active ?? {}
  const limit = numberValue(active.max_daily_loss, 0)
  const pnl = runtime?.pnl.session ?? 0
  const used = limit > 0 && pnl < 0 ? Math.min(100, Math.abs(pnl) / limit * 100) : 0
  return (
    <section className="sidebar-card daily-drawdown-card">
      <div className="daily-drawdown-ring" style={{ '--drawdown': `${used * 3.6}deg` } as CSSProperties}><strong>{used.toFixed(1)}%</strong></div>
      <div className="daily-drawdown-details">
        <div className="sidebar-title"><span>Daily Drawdown</span></div>
        <dl>
          <div><dt>Current</dt><dd className={pnl < 0 ? 'negative' : 'positive'}>{signedMoney(pnl)}</dd></div>
          <div><dt>Limit</dt><dd>{limit > 0 ? `₹${limit.toLocaleString('en-IN')}` : 'No cap'}</dd></div>
          <div><dt>Remaining</dt><dd>{limit > 0 ? `₹${Math.max(0, limit - Math.abs(Math.min(0, pnl))).toLocaleString('en-IN')}` : '—'}</dd></div>
        </dl>
      </div>
    </section>
  )
}

function numberValue(value: unknown, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function configValues(active: Record<string, unknown>): EngineConfigValues {
  return {
    lots: numberValue(active.configured_lots, 1),
    stopLossPercent: numberValue(active.option_sl_percent, 10),
    takeProfitPercent: numberValue(active.option_tp_percent, 20),
    maxTradesPerDay: numberValue(active.max_trades_per_day, 0),
  }
}

function signedMoney(value: number): string {
  return `${value >= 0 ? '+' : '-'}₹${Math.abs(value).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
