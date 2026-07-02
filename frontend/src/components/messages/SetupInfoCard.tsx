import { CheckCircle2, Copy, LoaderCircle, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getEgressOptions, selectEgressIp, verifyEgressIp, type EgressOptionsResponse } from '../../api'

interface Props {
  autoAssign?: boolean
  onReadyChange?: (ready: boolean) => void
  refreshKey?: number
}

export function SetupInfoCard({ autoAssign = true, onReadyChange, refreshKey = 0 }: Props = {}) {
  const [options, setOptions] = useState<EgressOptionsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [assigning, setAssigning] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadOptions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      let response = await getEgressOptions()
      if (autoAssign && !response.egress.public_ip) {
        const availableNode = response.nodes.find((node) => node.available)
        if (!availableNode) {
          setOptions(response)
          setError('No Nova Static IP is currently available.')
          return
        }
        setAssigning(true)
        await selectEgressIp(availableNode.public_ip)
        response = await getEgressOptions()
      }
      setOptions(response)
    } catch (loadError) {
      setOptions(null)
      setError(loadError instanceof Error ? loadError.message : 'Could not load static IPs.')
    } finally {
      setAssigning(false)
      setLoading(false)
    }
  }, [autoAssign])

  useEffect(() => {
    void loadOptions()
  }, [loadOptions, refreshKey])

  useEffect(() => {
    onReadyChange?.(Boolean(options?.egress.public_ip && options.egress.verified))
  }, [onReadyChange, options])

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

  const assignedIp = options?.egress.public_ip ?? null
  const assignedNode = assignedIp
    ? options?.nodes.find((node) => node.public_ip === assignedIp || node.selected)
    : null

  return (
    <div className="setup-info-card">
      <div className="static-ip-heading">
        <strong>Dhan static IP</strong>
        <span>NOVA assigns one dedicated IP. Whitelist this IP in your Dhan account.</span>
      </div>
      {loading ? <div className="static-ip-loading"><LoaderCircle size={15} /> {assigning ? 'Assigning Static IP...' : 'Checking Static IP access...'}</div> : null}
      <div className="static-ip-options">
        {assignedIp ? (
            <div className="static-ip-option selected" key={assignedIp}>
              <div>
                <code>{assignedIp}</code>
                <span>{assignedNode?.selected ? 'Assigned to this account' : 'Assigned Nova Static IP'}</span>
              </div>
              <button type="button" disabled>
                <CheckCircle2 size={13} />
                Assigned
              </button>
              <button type="button" className="static-ip-copy" aria-label={`Copy ${assignedIp}`} onClick={() => void navigator.clipboard?.writeText(assignedIp)}>
                <Copy size={13} />
              </button>
            </div>
        ) : !loading && options ? (
          <p className="static-ip-pending">Payment confirmation is pending. This panel refreshes after Razorpay webhook confirmation.</p>
        ) : null}
      </div>
      {options?.egress.public_ip ? (
        <div className="static-ip-verification-row">
          <p className={`static-ip-verification ${options.egress.verified ? 'verified' : ''}`}>
            {options.egress.verified ? 'Proxy verified. Add the selected IP to Dhan.' : options.egress.verification_error || 'Proxy verification is pending.'}
          </p>
          <button type="button" onClick={() => void verifyIp()} disabled={verifying}>
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
