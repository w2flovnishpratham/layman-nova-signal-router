import { Button } from '@/components/ui/button'
import type { ClientCommand, RiskConfig, SetupState, SideFilter } from '../types'

interface Props {
  state: SetupState
  risk: RiskConfig | undefined
  onSend: (command: ClientCommand) => void
}

export function QuickActions({ state, risk, onSend }: Props) {
  if (state !== 'LIVE' && state !== 'PAUSED') return null

  return (
    <div className="quick-actions">
      <Button variant="unstyled" type="button" onClick={() => onSend({ type: state === 'LIVE' ? 'session.pause' : 'session.resume', data: {} })}>
        {state === 'LIVE' ? 'Pause after this trade' : 'Resume'}
      </Button>
      {(['CE', 'PE', 'BOTH'] as SideFilter[]).map((side) => (
        <Button variant="unstyled"
          key={side}
          className={risk?.side === side ? 'selected' : ''}
          type="button"
          onClick={() => onSend({ type: 'session.patch_risk', data: { side } })}
        >
          {side === 'BOTH' ? 'Both' : `${side} only`}
        </Button>
      ))}
      <Button variant="unstyled" type="button">EOD report</Button>
    </div>
  )
}
