import { BotBubble } from './BotBubble'
import { EodCard, ExitCard, OrderPlacedCard, RecentActivityCard, RejectCard, SignalCard } from './EventCards'
import { SetupInfoCard } from './SetupInfoCard'
import { UserBubble } from './UserBubble'
import type { ReactNode } from 'react'
import type { RenderableMessage } from '../../types'

export function MessageRow({ message, inlinePanel = null }: { message: RenderableMessage; inlinePanel?: ReactNode }) {
  if (message.type === 'system.event') {
    if (message.data.kind === 'history_replay') {
      return (
        <BotBubble label="Nova history">
          <RecentActivityCard />
        </BotBubble>
      )
    }
    return <div className="system-chip">{String(message.data.label ?? message.data.kind ?? 'System event')}</div>
  }

  if (message.type === 'client.summary') {
    return <div className="summary-chip">{String(message.data.text)}</div>
  }

  if (message.type === 'client.message') {
    return <UserBubble text={String(message.data.text ?? '')} />
  }

  if (message.type === 'setup.info') {
    return (
      <BotBubble label="Nova Dhan static IP">
        <SetupInfoCard />
      </BotBubble>
    )
  }

  if (message.type === 'signal.received') {
    return (
      <BotBubble label={`Nova signal ${message.data.action}`}>
        <SignalCard message={message} />
      </BotBubble>
    )
  }

  if (message.type === 'order.placed') {
    return (
      <BotBubble label="Nova order placed">
        <OrderPlacedCard message={message} />
      </BotBubble>
    )
  }

  if (message.type === 'order.filled') {
    return null
  }

  if (message.type === 'order.rejected') {
    return (
      <BotBubble label="Nova order rejected" tone="error">
        <RejectCard message={message} />
      </BotBubble>
    )
  }

  if (message.type === 'session.error') {
    return (
      <BotBubble label="Nova error" tone="error">
        {message.data.normalizedError ? <RejectCard message={message} /> : <p>{String(message.data.message)}</p>}
      </BotBubble>
    )
  }

  if (message.type === 'trade.exit') {
    return (
      <BotBubble label="Nova trade exit">
        <ExitCard message={message} />
      </BotBubble>
    )
  }

  if (message.type === 'session.eod') {
    return (
      <BotBubble label="Nova session ended">
        <EodCard message={message} />
      </BotBubble>
    )
  }

  return (
    <BotBubble>
      <p>{String(message.data.text ?? message.type)}</p>
      {inlinePanel}
    </BotBubble>
  )
}
