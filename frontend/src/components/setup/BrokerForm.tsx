import { useState } from 'react'
import type { FormEvent } from 'react'
import type { ClientCommand } from '../../types'

export function BrokerForm({ onSend }: { onSend: (command: ClientCommand) => void }) {
  const [clientId, setClientId] = useState('')
  const [accessToken, setAccessToken] = useState('')

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSend({ type: 'setup.broker_creds', data: { clientId, accessToken } })
  }

  return (
    <form className="setup-card form-card" onSubmit={submit}>
      <span className="eyebrow">Broker</span>
      <label>
        Client ID
        <input value={clientId} onChange={(event) => setClientId(event.target.value)} required minLength={3} autoComplete="off" />
      </label>
      <label>
        Access Token
        <input
          value={accessToken}
          onChange={(event) => setAccessToken(event.target.value)}
          required
          minLength={12}
          type="password"
          autoComplete="off"
        />
      </label>
      <button type="submit">Securely connect</button>
    </form>
  )
}
