import { Send } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import type { ClientCommand, SetupState } from '../types'

interface Props {
  state: SetupState
  pending: boolean
  connected: boolean
  onUserReply: (text: string) => void
  onSend: (command: ClientCommand) => boolean
}

export function ChatCommandInput({ state, pending, connected, onUserReply, onSend }: Props) {
  const [value, setValue] = useState('')

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = value.trim()
    if (!text) return
    const normalized = text.toLowerCase()
    if (normalized === 'pause') {
      if (!onSend({ type: 'session.pause', data: {} })) return
      setValue('')
      onUserReply(text)
      return
    }
    if (normalized === 'resume entries' || normalized === 'resume') {
      if (!onSend({ type: 'session.resume', data: {} })) return
      setValue('')
      onUserReply(text)
    }
  }

  return (
    <form className="chat-command-input" onSubmit={submit}>
      <input
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={state === 'PAUSED' ? 'Type "resume entries"' : 'Type "pause" or "resume entries"'}
        disabled={!connected || pending}
      />
      <button type="submit" aria-label="Send command" disabled={!connected || pending}>
        <Send size={15} />
      </button>
    </form>
  )
}
