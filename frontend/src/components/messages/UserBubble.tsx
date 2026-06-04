export function UserBubble({ text }: { text: string }) {
  return (
    <div className="user-row" aria-label="User reply">
      <article className="message user-message">
        <p>{text}</p>
      </article>
    </div>
  )
}
