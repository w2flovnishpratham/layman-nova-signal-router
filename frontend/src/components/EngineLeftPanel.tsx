import { ManualOrderPanel } from './ManualOrderPanel'
import { MarketCard } from './MarketCard'
import type { ReactNode } from 'react'
import type { RuntimeStatus } from '../api'
import type { ActiveTrade, EngineMode, MarketSnapshot } from '../types'
import { DailyDrawdownCard, EngineConfigCard } from '../trading/EngineConfigCard'

interface Props {
  marketSnapshot: MarketSnapshot | null
  engineMode: EngineMode | null
  activeTrade: ActiveTrade | null
  runtimePositionOpen?: boolean
  collapseControl?: ReactNode
  runtime: RuntimeStatus | null
  onStop: () => void
}

export function EngineLeftPanel({ marketSnapshot, engineMode, activeTrade, runtimePositionOpen = false, collapseControl, runtime, onStop }: Props) {
  return (
    <div className="engine-left flex flex-col gap-4" aria-label="Market and manual order">
      <EngineConfigCard runtime={runtime} onStop={onStop} />
      <MarketCard snapshot={marketSnapshot} collapseControl={collapseControl} />
      <ManualOrderPanel engineMode={engineMode} activeTrade={activeTrade} runtimePositionOpen={runtimePositionOpen} />
      <DailyDrawdownCard runtime={runtime} />
    </div>
  )
}
