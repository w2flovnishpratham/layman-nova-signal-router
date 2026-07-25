import type { ReactNode } from 'react'

interface Props {
  children: ReactNode
  tone?: 'normal' | 'error'
  label?: string
  showAvatar?: boolean
}

export function BotBubble({ children, tone = 'normal', label = 'Nova message', showAvatar = false }: Props) {
  return (
    <div className="bot-row" aria-label={label}>
      {showAvatar ? <div className="bot-avatar" aria-hidden="true">N</div> : null}
      <article className={`message bot-message ${tone === 'error' ? 'error-message' : ''}`}>
        {children}
      </article>
    </div>
  )
}
