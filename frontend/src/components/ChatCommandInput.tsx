import { Send } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import type { ClientCommand, SetupState } from '../types'

interface Props {
  state: SetupState
  onUserReply: (text: string) => void
  onSend: (command: ClientCommand) => void
}

export function ChatCommandInput({ state, onUserReply, onSend }: Props) {
  const [value, setValue] = useState('')

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = value.trim()
    if (!text) return
    setValue('')
    onUserReply(text)

    const normalized = text.toLowerCase()
    if (normalized === 'pause') {
      onSend({ type: 'session.pause', data: {} })
      return
    }
    if (normalized === 'resume entries' || normalized === 'resume') {
      onSend({ type: 'session.resume', data: {} })
    }
  }

  return (
    <form className="chat-command-input" onSubmit={submit}>
      <input
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={state === 'PAUSED' ? 'Type "resume entries"' : 'Type "pause" or "resume entries"'}
      />
      <button type="submit" aria-label="Send command">
        <Send size={15} />
      </button>
    </form>
  )
}
