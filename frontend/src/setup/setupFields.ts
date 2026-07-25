import type { StrategySetupField } from '../api'
import type { SetupValues } from '../state/conversationMachine'

// The setup wizard asks two kinds of question: the strategy's own schema fields
// (served by the backend) and these engine safety settings, which live in the
// runtime settings rather than in a strategy's setup_schema. They are expressed
// as ordinary schema fields so the frozen conversation machine and the generic
// question renderer handle them with no special-casing.
export const RISK_FIELDS: StrategySetupField[] = [
  {
    key: 'max_daily_loss',
    type: 'decimal',
    label: "What's your maximum loss for one day? The engine hard-stops and squares off if it's hit.",
    minimum: 0,
    maximum: 10_000_000,
    required: true,
    default: 25000,
  },
  {
    key: 'max_trades_per_day',
    type: 'integer',
    label: 'How many trades per day at most? Enter 0 for no cap.',
    minimum: 0,
    maximum: 50,
    required: true,
    default: 6,
  },
  {
    key: 'entry_cutoff_ist',
    type: 'choice',
    label: 'After what time should NOVA stop accepting new entries?',
    options: ['14:30', '15:00', '15:15', 'No cutoff'],
    required: true,
    default: '15:15',
  },
]

export const RISK_KEYS: readonly string[] = RISK_FIELDS.map((f) => f.key)

/** Strategy schema fields followed by the engine safety fields, in ask order. */
export function withRiskFields(fields: StrategySetupField[]): StrategySetupField[] {
  // A strategy with no schema is unusable; don't dress it up with safety
  // questions it can never act on.
  if (fields.length === 0) return fields
  return [...fields, ...RISK_FIELDS]
}

/** Split a draft into the strategy setup values and the runtime risk settings,
    which are saved through two different endpoints. */
export function splitDraft(draft: SetupValues): { strategy: SetupValues; risk: SetupValues } {
  const strategy: SetupValues = {}
  const risk: SetupValues = {}
  for (const [key, value] of Object.entries(draft)) {
    if (RISK_KEYS.includes(key)) risk[key] = key === 'entry_cutoff_ist' && value === 'No cutoff' ? '' : value
    else strategy[key] = value
  }
  return { strategy, risk }
}

export type StepStatus = 'done' | 'active' | 'pending'

export interface SetupStep {
  id: string
  label: string
  /** Schema keys this step covers; empty for steps that aren't questions. */
  keys: string[]
  status: StepStatus
  /** What to show on the configuration card: the answer, or why there isn't one. */
  summary: string
}

const GROUPS: { id: string; label: string; keys: string[] }[] = [
  { id: 'sizing', label: 'Sizing & allowed sides', keys: ['direction', 'lots'] },
  { id: 'exits', label: 'Exit levels', keys: ['stop_loss_percent', 'take_profit_percent'] },
  { id: 'safety', label: 'Risk limits', keys: ['max_daily_loss', 'max_trades_per_day', 'entry_cutoff_ist'] },
]

function rupees(value: unknown): string {
  const num = Number(value)
  return Number.isFinite(num) ? `₹${num.toLocaleString('en-IN')}` : String(value)
}

function summarise(key: string, value: unknown): string {
  switch (key) {
    case 'direction': return value === 'BOTH' ? 'CE + PE' : String(value)
    case 'lots': return `${value} lot${Number(value) === 1 ? '' : 's'}`
    case 'stop_loss_percent': return `SL ${value}%`
    case 'take_profit_percent': return `TP ${value}%`
    case 'max_daily_loss': return rupees(value)
    case 'max_trades_per_day': return Number(value) === 0 ? 'no trade cap' : `max ${value} trades`
    case 'entry_cutoff_ist': return value === 'No cutoff' ? 'no entry cutoff' : `${value} IST`
    default: return String(value)
  }
}

/** Derive the step rail and configuration cards from machine state. Everything
    here is a projection — it never introduces a second source of truth. */
export function deriveSteps(args: {
  brokerConnected: boolean
  brokerMasked: string | null
  strategyName: string | null
  strategyVersion: string | null
  mode: string | null
  fields: StrategySetupField[]
  draft: SetupValues
  activeKey: string | null
  reviewing: boolean
}): SetupStep[] {
  const { draft, activeKey } = args
  const answered = (key: string) => draft[key] !== undefined && draft[key] !== null && draft[key] !== ''

  const steps: SetupStep[] = [
    {
      id: 'mode',
      label: 'Mode',
      keys: [],
      status: args.mode ? 'done' : 'active',
      summary: args.mode === 'paper' ? 'Paper — simulated' : args.mode === 'live' ? 'Live — real orders' : 'Not chosen yet',
    },
    {
      id: 'broker',
      label: 'Broker readiness',
      keys: [],
      status: args.mode === 'paper' || args.brokerConnected ? 'done' : 'pending',
      summary: args.mode === 'paper'
        ? 'Not required for Paper'
        : args.brokerConnected
          ? `Dhan ${args.brokerMasked ?? 'connected'}`
          : 'Not connected — connect Dhan in Credentials',
    },
    {
      id: 'strategy',
      label: 'Strategy',
      keys: [],
      status: args.strategyName ? 'done' : args.mode ? 'active' : 'pending',
      // Version comes from the catalog; never invent one.
      summary: args.strategyName
        ? `${args.strategyName}${args.strategyVersion ? ` v${args.strategyVersion}` : ''}`
        : 'Not chosen yet',
    },
  ]

  const present = new Set(args.fields.map((f) => f.key))
  for (const group of GROUPS) {
    const keys = group.keys.filter((k) => present.has(k))
    if (keys.length === 0) continue
    const isActive = activeKey !== null && keys.includes(activeKey)
    const allAnswered = keys.every(answered)
    steps.push({
      id: group.id,
      label: group.label,
      keys,
      status: allAnswered ? 'done' : isActive ? 'active' : 'pending',
      summary: keys.filter(answered).map((k) => summarise(k, draft[k])).join(' · ')
        || (isActive ? 'Awaiting your answer…' : 'Not answered yet'),
    })
  }

  // Any schema field the groups don't know about still gets its own step, so a
  // new backend field can never go unasked or unshown.
  const grouped = new Set(GROUPS.flatMap((g) => g.keys))
  for (const field of args.fields) {
    if (grouped.has(field.key)) continue
    const isActive = activeKey === field.key
    steps.push({
      id: field.key,
      label: field.label,
      keys: [field.key],
      status: answered(field.key) ? 'done' : isActive ? 'active' : 'pending',
      summary: answered(field.key)
        ? summarise(field.key, draft[field.key])
        : isActive ? 'Awaiting your answer…' : 'Not answered yet',
    })
  }

  steps.push({
    id: 'review',
    label: 'Review & arm',
    keys: [],
    status: args.reviewing ? 'active' : 'pending',
    summary: args.reviewing ? 'Ready to review' : 'Not started',
  })
  return steps
}

/** Completed steps over total, as a whole percentage. */
export function setupProgress(steps: SetupStep[]): number {
  if (steps.length === 0) return 0
  const done = steps.filter((s) => s.status === 'done').length
  return Math.round((done / steps.length) * 100)
}
