import { useEffect, useState } from 'react'
import { getUserCredentialStatus, saveUserCredentials, type UserCredentialStatus } from '../api'

export function BrokerCredentialsCard() {
  const [status, setStatus] = useState<UserCredentialStatus | null>(null)
  const [clientIdInput, setClientIdInput] = useState('')
  const [accessTokenInput, setAccessTokenInput] = useState('')
  const [savingField, setSavingField] = useState<'client_id' | 'access_token' | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => {
    let cancelled = false
    getUserCredentialStatus().then((result) => {
      if (!cancelled) setStatus(result)
    })
    return () => {
      cancelled = true
    }
  }, [])

  async function saveClientId() {
    if (!clientIdInput.trim()) return
    setSavingField('client_id')
    setError('')
    setMessage('')
    try {
      const next = await saveUserCredentials({ dhan_client_id: clientIdInput.trim() })
      setStatus(next)
      setClientIdInput('')
      setMessage('Dhan client ID saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save client ID.')
    } finally {
      setSavingField(null)
    }
  }

  async function saveAccessToken() {
    if (!accessTokenInput.trim()) return
    setSavingField('access_token')
    setError('')
    setMessage('')
    try {
      const next = await saveUserCredentials({ dhan_access_token: accessTokenInput.trim() })
      setStatus(next)
      setAccessTokenInput('')
      setMessage('Dhan access token saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save access token.')
    } finally {
      setSavingField(null)
    }
  }

  return (
    <section className="sidebar-card credentials-card">
      <div className="sidebar-title">
        <span>Dhan Broker Credentials</span>
      </div>

      <div className="credential-field">
        <label htmlFor="dhan-client-id">
          Client ID
          {status?.has_dhan_client_id ? <span className="credential-saved-hint">Saved: {status.dhan_client_id_masked}</span> : null}
        </label>
        <div className="credential-field-row">
          <input
            id="dhan-client-id"
            type="text"
            autoComplete="off"
            placeholder={status?.has_dhan_client_id ? 'Enter a new client ID to update' : 'Enter your Dhan client ID'}
            value={clientIdInput}
            onChange={(event) => setClientIdInput(event.target.value)}
          />
          <button
            type="button"
            className="secondary-button"
            disabled={!clientIdInput.trim() || savingField === 'client_id'}
            onClick={saveClientId}
          >
            {savingField === 'client_id' ? 'Saving…' : status?.has_dhan_client_id ? 'Update' : 'Save'}
          </button>
        </div>
      </div>

      <div className="credential-field">
        <label htmlFor="dhan-access-token">
          Access Token
          {status?.has_dhan_access_token ? <span className="credential-saved-hint">Saved</span> : null}
        </label>
        <div className="credential-field-row">
          <input
            id="dhan-access-token"
            type="password"
            autoComplete="off"
            placeholder={status?.has_dhan_access_token ? 'Enter a new access token to update' : 'Enter your Dhan access token'}
            value={accessTokenInput}
            onChange={(event) => setAccessTokenInput(event.target.value)}
          />
          <button
            type="button"
            className="secondary-button"
            disabled={!accessTokenInput.trim() || savingField === 'access_token'}
            onClick={saveAccessToken}
          >
            {savingField === 'access_token' ? 'Saving…' : status?.has_dhan_access_token ? 'Update' : 'Save'}
          </button>
        </div>
      </div>

      {message ? <p className="credential-status-message success">{message}</p> : null}
      {error ? <p className="credential-status-message error">{error}</p> : null}
    </section>
  )
}
