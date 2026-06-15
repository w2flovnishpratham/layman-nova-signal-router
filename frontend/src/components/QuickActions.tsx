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
      <button type="button" onClick={() => onSend({ type: state === 'LIVE' ? 'session.pause' : 'session.resume', data: {} })}>
        {state === 'LIVE' ? 'Pause after this trade' : 'Resume'}
      </button>
      {(['CE', 'PE', 'BOTH'] as SideFilter[]).map((side) => (
        <button
          key={side}
          className={risk?.side === side ? 'selected' : ''}
          type="button"
          onClick={() => onSend({ type: 'session.patch_risk', data: { side } })}
        >
          {side === 'BOTH' ? 'Both' : `${side} only`}
        </button>
      ))}
      <button type="button">EOD report</button>
    </div>
  )
}
