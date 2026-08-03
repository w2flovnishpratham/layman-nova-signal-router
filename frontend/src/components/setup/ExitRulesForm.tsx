import { Input } from "@/components/ui/input"
import { Button } from '@/components/ui/button'
import { useState } from 'react'
import type { FormEvent } from 'react'
import type { ClientCommand, ExitRules } from '../../types'

interface Props {
  initial: ExitRules
  onSend: (command: ClientCommand) => void
}

export function ExitRulesForm({ initial, onSend }: Props) {
  const [exits, setExits] = useState<ExitRules>(initial)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSend({ type: 'setup.exits', data: exits })
  }

  return (
    <form className="setup-card form-card" onSubmit={submit}>
      <span className="eyebrow">Exits</span>
      <div className="chip-row exit-row">
        {[
          ['flip_only', 'Reversal flip only'],
          ['flip_tp', 'Flip + TP'],
          ['custom', 'Custom'],
        ].map(([mode, label]) => (
          <Button variant="unstyled"
            key={mode}
            className={exits.mode === mode ? 'selected' : ''}
            type="button"
            onClick={() => setExits({ mode: mode as ExitRules['mode'], targetProfit: mode === 'flip_tp' ? 3000 : null })}
          >
            {label}
          </Button>
        ))}
      </div>
      {exits.mode === 'flip_tp' ? (
        <label>
          Target profit
          <Input variant="unstyled"
            type="number"
            value={exits.targetProfit ?? 3000}
            min={100}
            onChange={(event) => setExits((current) => ({ ...current, targetProfit: Number(event.target.value) }))}
          />
        </label>
      ) : null}
      <Button variant="unstyled" type="submit">Lock exits</Button>
    </form>
  )
}
