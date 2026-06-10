import { Activity } from 'lucide-react'
import type { ActiveTrade, EngineMode, SideFilter } from '../types'

interface Props {
  paused: boolean
  activeTrade: ActiveTrade | null
  side: SideFilter
  engineMode: EngineMode | null
}

export function EngineListening({ paused, activeTrade, side, engineMode }: Props) {
  const status = listeningStatus(paused, activeTrade, side)

  return (
    <div className={`engine-listening ${paused ? 'paused' : ''}`}>
      <div className="engine-orb">
        <span className="engine-pulse"><Activity size={28} /></span>
      </div>
      <div>
        <strong>{status.title}</strong>
        <p>{status.detail}</p>
        <p className="engine-mode-detail">{engineMode === 'paper' ? 'Paper simulator listening for TradingView signals.' : 'Live router listening for TradingView signals.'}</p>
      </div>
    </div>
  )
}

function listeningStatus(paused: boolean, activeTrade: ActiveTrade | null, side: SideFilter): { title: string; detail: string } {
  if (paused) {
    return {
      title: 'Entry requests blocked',
      detail: activeTrade
        ? `Backend will reject new entries; exits remain enabled for active ${activeTrade.optType}.`
        : 'Backend will reject ENTRY webhooks until entry requests are allowed.',
    }
  }

  if (activeTrade?.optType === 'CE') {
    return {
      title: 'Waiting for sell / flip signal',
      detail: side === 'CE'
        ? 'A SELL signal will close the active CE without opening a PE position.'
        : 'A SELL Supertrend signal will close the active CE and route a PE entry.',
    }
  }

  if (activeTrade?.optType === 'PE') {
    return {
      title: 'Waiting for buy / flip signal',
      detail: side === 'PE'
        ? 'A BUY signal will close the active PE without opening a CE position.'
        : 'A BUY Supertrend signal will close the active PE and route a CE entry.',
    }
  }

  if (side === 'CE') {
    return {
      title: 'Waiting for buy signal',
      detail: 'The next BUY Supertrend signal will route a CE entry.',
    }
  }

  if (side === 'PE') {
    return {
      title: 'Waiting for sell signal',
      detail: 'The next SELL Supertrend signal will route a PE entry.',
    }
  }

  return {
    title: 'Waiting for buy or sell signal',
    detail: 'BUY routes CE. SELL routes PE.',
  }
}
