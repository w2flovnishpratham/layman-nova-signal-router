import type { ReactNode } from 'react'
import { Message, MessageAvatar, MessageContent } from '@/components/ui/message'

interface Props {
  children: ReactNode
  tone?: 'normal' | 'error'
  label?: string
  showAvatar?: boolean
}

export function BotBubble({ children, tone = 'normal', label = 'Nova message', showAvatar = false }: Props) {
  return (
    <Message className="bot-row" aria-label={label}>
      {showAvatar ? <MessageAvatar className="bot-avatar" aria-hidden="true">N</MessageAvatar> : null}
      <MessageContent>
        <article className={`message bot-message ${tone === 'error' ? 'error-message' : ''}`}>
          {children}
        </article>
      </MessageContent>
    </Message>
  )
}
