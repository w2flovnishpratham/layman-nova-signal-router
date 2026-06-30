import { CheckCircle2, Copy, LoaderCircle, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getEgressOptions, selectEgressIp, verifyEgressIp, type EgressOptionsResponse } from '../../api'

interface Props {
  onReadyChange?: (ready: boolean) => void
}

export function SetupInfoCard({ onReadyChange }: Props = {}) {
  const [options, setOptions] = useState<EgressOptionsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [selecting, setSelecting] = useState<string | null>(null)
  const [verifying, setVerifying] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadOptions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setOptions(await getEgressOptions())
    } catch (loadError) {
      setOptions(null)
      setError(loadError instanceof Error ? loadError.message : 'Could not load static IPs.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadOptions()
  }, [loadOptions])

  useEffect(() => {
    onReadyChange?.(Boolean(options?.egress.public_ip && options.egress.verified))
  }, [onReadyChange, options])

  async function selectIp(publicIp: string) {
    setSelecting(publicIp)
    setError(null)
    try {
      await selectEgressIp(publicIp)
      setOptions(await getEgressOptions())
    } catch (selectError) {
      setError(selectError instanceof Error ? selectError.message : 'Could not select static IP.')
    } finally {
      setSelecting(null)
    }
  }

  async function verifyIp() {
    setVerifying(true)
    setError(null)
    try {
      await verifyEgressIp()
      setOptions(await getEgressOptions())
    } catch (verifyError) {
      setError(verifyError instanceof Error ? verifyError.message : 'Could not verify static IP.')
      setOptions(await getEgressOptions().catch(() => options))
    } finally {
      setVerifying(false)
    }
  }

  return (
    <div className="setup-info-card">
      <div className="static-ip-heading">
        <strong>Dhan static IP</strong>
        <span>Select one IP and whitelist it in your Dhan account.</span>
      </div>
      {loading ? <div className="static-ip-loading"><LoaderCircle size={15} /> Loading IPs...</div> : null}
      <div className="static-ip-options">
        {options?.nodes.map((node) => {
          const disabled = selecting !== null || (!node.available && !node.selected)
          return (
            <div className={`static-ip-option ${node.selected ? 'selected' : ''}`} key={node.public_ip}>
              <div>
                <code>{node.public_ip}</code>
                <span>{node.selected ? 'Assigned to this account' : node.available ? 'Available' : 'Assigned to another account'}</span>
              </div>
              <button type="button" disabled={disabled || node.selected} onClick={() => void selectIp(node.public_ip)}>
                {selecting === node.public_ip ? <LoaderCircle size={13} /> : node.selected ? <CheckCircle2 size={13} /> : null}
                {node.selected ? 'Selected' : node.available ? 'Select' : 'Unavailable'}
              </button>
              <button type="button" className="static-ip-copy" aria-label={`Copy ${node.public_ip}`} onClick={() => void navigator.clipboard?.writeText(node.public_ip)}>
                <Copy size={13} />
              </button>
            </div>
          )
        })}
      </div>
      {options?.egress.public_ip ? (
        <div className="static-ip-verification-row">
          <p className={`static-ip-verification ${options.egress.verified ? 'verified' : ''}`}>
            {options.egress.verified ? 'Proxy verified. Add the selected IP to Dhan.' : options.egress.verification_error || 'Proxy verification is pending.'}
          </p>
          <button type="button" onClick={() => void verifyIp()} disabled={selecting !== null || verifying}>
            {verifying ? <LoaderCircle size={13} /> : <RefreshCw size={13} />}
            Verify
          </button>
        </div>
      ) : null}
      {error ? (
        <div className="static-ip-error-row">
          <p className="static-ip-error">{error}</p>
          <button type="button" onClick={() => void loadOptions()} disabled={loading}>
            <RefreshCw size={13} />
            Refresh
          </button>
        </div>
      ) : null}
    </div>
  )
}
