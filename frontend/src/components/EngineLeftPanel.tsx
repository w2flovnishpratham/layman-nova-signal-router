import { ManualOrderPanel } from './ManualOrderPanel'
import type { RuntimeStatus } from '../api'
import type { ActiveTrade, ClientCommand, EngineMode, SideFilter } from '../types'
import { DailyDrawdownCard, EngineConfigCard, type EngineConfigValues } from '../trading/EngineConfigCard'

interface Props {
  engineMode: EngineMode | null
  activeTrade: ActiveTrade | null
  runtimePositionOpen?: boolean
  runtime: RuntimeStatus | null
  onStop: () => void
  onSaveConfig: (values: EngineConfigValues) => Promise<void>
  side: SideFilter
  onSend: (command: ClientCommand) => void
  actionPending?: boolean
}

export function EngineLeftPanel({ engineMode, activeTrade, runtimePositionOpen = false, runtime, onStop, onSaveConfig, side, onSend, actionPending = false }: Props) {
  return (
    <div className="engine-left flex flex-col gap-4" aria-label="Market and manual order">
      <EngineConfigCard runtime={runtime} onStop={onStop} onSaveConfig={onSaveConfig} side={side} onSideChange={(next) => onSend({ type: 'session.patch_risk', data: { side: next } })} actionPending={actionPending} />
      <ManualOrderPanel engineMode={engineMode} activeTrade={activeTrade} runtimePositionOpen={runtimePositionOpen} />
      <DailyDrawdownCard runtime={runtime} />
    </div>
  )
}
