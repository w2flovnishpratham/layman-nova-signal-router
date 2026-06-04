import type { ReactNode } from 'react'

interface Props {
  children: ReactNode
  tone?: 'normal' | 'error'
  label?: string
}

export function BotBubble({ children, tone = 'normal', label = 'Nova message' }: Props) {
  return (
    <div className="bot-row" aria-label={label}>
      <article className={`message bot-message ${tone === 'error' ? 'error-message' : ''}`}>
        {children}
      </article>
    </div>
  )
}
