import { Message, MessageContent } from '@/components/ui/message'

export function UserBubble({ text }: { text: string }) {
  return (
    <Message align="end" className="user-row" aria-label="User reply">
      <MessageContent>
        <article className="message user-message">
          <p>{text}</p>
        </article>
      </MessageContent>
    </Message>
  )
}
