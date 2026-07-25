import { describe, expect, it } from 'vitest'
import type { StrategySetupField } from '../api'
import { RISK_FIELDS, deriveSteps, setupProgress, splitDraft, withRiskFields } from './setupFields'

const schema: StrategySetupField[] = [
  { key: 'direction', type: 'choice', label: 'Which signals?', options: ['CE', 'PE', 'BOTH'], required: true, default: 'BOTH' },
  { key: 'lots', type: 'integer', label: 'How many lots?', minimum: 1, maximum: 20, required: true, default: 1 },
  { key: 'stop_loss_percent', type: 'decimal', label: 'Stop loss %', minimum: 0, maximum: 100, required: true, default: 10 },
  { key: 'take_profit_percent', type: 'decimal', label: 'Take profit %', minimum: 0, maximum: 1000, required: true, default: 20 },
]

const base = {
  brokerConnected: true,
  brokerMasked: '••••1097',
  strategyName: 'Supertrend',
  strategyVersion: '1.0.0',
  mode: 'paper',
  fields: withRiskFields(schema),
  draft: {},
  activeKey: null,
  reviewing: false,
}

describe('setup fields', () => {
  it('keeps the exit-level questions the mockup dropped', () => {
    const keys = withRiskFields(schema).map((f) => f.key)
    expect(keys).toContain('stop_loss_percent')
    expect(keys).toContain('take_profit_percent')
  })

  it('asks the safety questions after the strategy schema', () => {
    const keys = withRiskFields(schema).map((f) => f.key)
    expect(keys.slice(-3)).toEqual(['max_daily_loss', 'max_trades_per_day', 'entry_cutoff_ist'])
  })

  it('does not bolt safety questions onto a strategy with no schema', () => {
    expect(withRiskFields([])).toEqual([])
  })

  it('offers deterministic entry-cutoff choices', () => {
    const cutoff = RISK_FIELDS.find((f) => f.key === 'entry_cutoff_ist')
    expect(cutoff && 'options' in cutoff ? cutoff.options : []).toEqual(['14:30', '15:00', '15:15', 'No cutoff'])
  })

  it('routes safety answers away from the strategy setup payload', () => {
    const { strategy, risk } = splitDraft({ lots: 2, direction: 'BOTH', max_daily_loss: 25000, entry_cutoff_ist: 'No cutoff' })
    expect(strategy).toEqual({ lots: 2, direction: 'BOTH' })
    expect(risk).toEqual({ max_daily_loss: 25000, entry_cutoff_ist: '' })
  })
})

describe('step derivation', () => {
  it('never invents a strategy version', () => {
    const steps = deriveSteps({ ...base, strategyVersion: null })
    expect(steps.find((s) => s.id === 'strategy')?.summary).toBe('Supertrend')
  })

  it('states plainly when the broker is not connected', () => {
    const steps = deriveSteps({ ...base, mode: 'live', brokerConnected: false, brokerMasked: null })
    const broker = steps.find((s) => s.id === 'broker')
    expect(broker?.status).toBe('pending')
    expect(broker?.summary).toMatch(/not connected/i)
  })

  it('marks the step holding the active question', () => {
    const steps = deriveSteps({ ...base, activeKey: 'entry_cutoff_ist' })
    const cutoff = steps.find((s) => s.id === 'safety')
    expect(cutoff?.status).toBe('active')
    expect(cutoff?.summary).toMatch(/awaiting/i)
  })

  it('summarises answers without rounding them away', () => {
    const steps = deriveSteps({ ...base, draft: { direction: 'BOTH', lots: 1, max_daily_loss: 25000, max_trades_per_day: 6 } })
    expect(steps.find((s) => s.id === 'sizing')?.summary).toBe('CE + PE · 1 lot')
    expect(steps.find((s) => s.id === 'safety')?.summary).toBe('₹25,000 · max 6 trades')
  })

  it('states when the entry cutoff is disabled', () => {
    const steps = deriveSteps({ ...base, draft: { entry_cutoff_ist: 'No cutoff' } })
    expect(steps.find((s) => s.id === 'safety')?.summary).toBe('no entry cutoff')
  })

  it('gives an unknown backend field its own step instead of hiding it', () => {
    const extra: StrategySetupField = { key: 'trail_percent', type: 'decimal', label: 'Trail %', minimum: 0, maximum: 100, required: true }
    const steps = deriveSteps({ ...base, fields: [...schema, extra] })
    expect(steps.some((s) => s.id === 'trail_percent')).toBe(true)
  })

  it('reports progress from answered steps only', () => {
    const empty = deriveSteps({ ...base, brokerConnected: false, strategyName: null, mode: null })
    expect(setupProgress(empty)).toBe(0)
    const partial = deriveSteps({ ...base, draft: { direction: 'BOTH', lots: 1 } })
    expect(setupProgress(partial)).toBeGreaterThan(0)
    expect(setupProgress(partial)).toBeLessThan(100)
  })
})
