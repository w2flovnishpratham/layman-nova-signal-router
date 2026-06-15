import { ChevronDown } from 'lucide-react'
import { useState } from 'react'
import type { SetupInfo } from '../../types'

export function SetupInfoCard({ info }: { info: SetupInfo }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="setup-info-card">
      <button type="button" className="setup-info-toggle" onClick={() => setOpen((current) => !current)}>
        <span>TradingView setup info</span>
        <ChevronDown size={14} className={open ? 'rotated' : ''} />
      </button>
      {open ? (
        <dl>
          <div><dt>Session</dt><dd>{info.sessionId}</dd></div>
          <div><dt>Webhook URL</dt><dd><code>{info.webhookUrl}</code></dd></div>
          <div><dt>Secret</dt><dd><code>{info.webhookSecret}</code></dd></div>
        </dl>
      ) : null}
    </div>
  )
}
