import { Button } from '@/components/ui/button'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { formatCurrency } from '../../lib/format'
import type { ClientCommand, RiskConfig, SideFilter } from '../../types'
import { contractsForLots, DEFAULT_NIFTY_LOT_SIZE } from '../../lib/trading'

interface Props {
  initial: RiskConfig
  onSend: (command: ClientCommand) => void
}

export function RiskForm({ initial, onSend }: Props) {
  const [risk, setRisk] = useState<RiskConfig>(initial)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSend({ type: 'setup.risk', data: risk })
  }

  return (
    <form className="setup-card form-card" onSubmit={submit}>
      <span className="eyebrow">Risk</span>
      <ChipRow
        label="Max trades"
        options={[3, 5, 10, null]}
        value={risk.maxTrades}
        format={(value) => (value === null ? 'No limit' : String(value))}
        onChange={(maxTrades) => setRisk((current) => ({ ...current, maxTrades }))}
      />
      <ChipRow
        label="Max loss"
        options={[1000, 3000, 5000, null]}
        value={risk.maxLoss}
        format={(value) => (value === null ? 'No limit' : formatCurrency(value))}
        onChange={(maxLoss) => setRisk((current) => ({ ...current, maxLoss }))}
      />
      <div className="counter-row">
        <span>Lots</span>
        <Button variant="unstyled" type="button" onClick={() => setRisk((current) => ({ ...current, lots: Math.max(1, current.lots - 1) }))}>
          -
        </Button>
        <strong>{risk.lots}</strong>
        <Button variant="unstyled" type="button" onClick={() => setRisk((current) => ({ ...current, lots: current.lots + 1 }))}>
          +
        </Button>
        <small>{contractsForLots(risk.lots, DEFAULT_NIFTY_LOT_SIZE)} qty</small>
      </div>
      <div className="chip-row">
        <span>Side</span>
        {(['CE', 'PE', 'BOTH'] as SideFilter[]).map((side) => (
          <Button variant="unstyled"
            key={side}
            className={risk.side === side ? 'selected' : ''}
            type="button"
            onClick={() => setRisk((current) => ({ ...current, side }))}
          >
            {side === 'BOTH' ? 'Both' : `${side} only`}
          </Button>
        ))}
      </div>
      <Button variant="unstyled" type="submit">Lock risk</Button>
    </form>
  )
}

function ChipRow<TValue extends number | null>({
  label,
  options,
  value,
  format,
  onChange,
}: {
  label: string
  options: TValue[]
  value: TValue
  format: (value: TValue) => string
  onChange: (value: TValue) => void
}) {
  return (
    <div className="chip-row">
      <span>{label}</span>
      {options.map((option) => (
        <Button variant="unstyled"
          key={String(option)}
          className={value === option ? 'selected' : ''}
          type="button"
          onClick={() => onChange(option)}
        >
          {format(option)}
        </Button>
      ))}
    </div>
  )
}
