import type { ClientCommand, RiskConfig, SetupState, SideFilter } from '../types'

interface Props {
  state: SetupState
  risk: RiskConfig | undefined
  pendingActions: ReadonlySet<string>
  connected: boolean
  onSend: (command: ClientCommand) => boolean
}

export function QuickActions({ state, risk, pendingActions, connected, onSend }: Props) {
  if (state !== 'LIVE' && state !== 'PAUSED') return null

  return (
    <div className="quick-actions">
      <button type="button" disabled={!connected || pendingActions.has('session.pause') || pendingActions.has('session.resume')} onClick={() => onSend({ type: state === 'LIVE' ? 'session.pause' : 'session.resume', data: {} })}>
        {pendingActions.has('session.pause') || pendingActions.has('session.resume') ? 'Working...' : state === 'LIVE' ? 'Pause after this trade' : 'Resume'}
      </button>
      {(['CE', 'PE', 'BOTH'] as SideFilter[]).map((side) => (
        <button
          key={side}
          className={risk?.side === side ? 'selected' : ''}
          type="button"
          disabled={!connected || pendingActions.has('session.patch_risk')}
          onClick={() => onSend({ type: 'session.patch_risk', data: { side } })}
        >
          {side === 'BOTH' ? 'Both' : `${side} only`}
        </button>
      ))}
      <button type="button">EOD report</button>
    </div>
  )
}
