import { AlertTriangle, X } from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'
import type { EngineMode } from '../types'

interface Props {
  open: boolean
  title: string
  consequence: string
  confirmLabel: string
  confirmPhrase?: string
  mode: EngineMode | null
  affectsRealOrders: boolean
  pending: boolean
  error?: string
  details?: ReactNode
  onConfirm: () => void
  onClose: () => void
}

export function ConfirmationDialog({
  open,
  title,
  consequence,
  confirmLabel,
  confirmPhrase,
  mode,
  affectsRealOrders,
  pending,
  error,
  details,
  onConfirm,
  onClose,
}: Props) {
  const [confirmation, setConfirmation] = useState('')

  if (!open) return null
  const phraseMatches = !confirmPhrase || confirmation === confirmPhrase

  function close() {
    setConfirmation('')
    onClose()
  }

  return (
    <div className="modal-backdrop" role="presentation" onPointerDown={(event) => {
      if (!pending && event.currentTarget === event.target) close()
    }}>
      <section className="confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="confirmation-title">
        <button className="icon-button dialog-close" type="button" aria-label="Close confirmation" onClick={close} disabled={pending}>
          <X size={17} />
        </button>
        <div className="confirmation-heading">
          <AlertTriangle size={22} />
          <div>
            <span>{mode ? `${mode.toUpperCase()} MODE` : 'MODE UNKNOWN'}</span>
            <h2 id="confirmation-title">{title}</h2>
          </div>
        </div>
        <p>{consequence}</p>
        <div className={`real-order-impact ${affectsRealOrders ? 'danger' : 'safe'}`}>
          {affectsRealOrders
            ? 'This action can affect real broker orders or positions.'
            : 'This action does not place a real broker order in the current mode.'}
        </div>
        {details ? <div className="confirmation-details">{details}</div> : null}
        {confirmPhrase ? (
          <label className="confirmation-input">
            Type <strong>{confirmPhrase}</strong> to continue
            <input
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              disabled={pending}
            />
          </label>
        ) : null}
        {error ? <p className="form-error">{error}</p> : null}
        <div className="confirmation-actions">
          <button type="button" className="secondary-button" onClick={close} disabled={pending}>Cancel</button>
          <button
            type="button"
            className="danger-confirm"
            disabled={!phraseMatches || pending}
            onClick={onConfirm}
          >
            {pending ? <span className="button-spinner" aria-hidden="true" /> : null}
            {pending ? 'Working...' : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  )
}
