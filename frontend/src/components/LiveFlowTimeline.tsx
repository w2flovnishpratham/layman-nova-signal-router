import { LiveFlowStep } from '../types'
import { CheckCircle, Circle, XCircle, Clock, AlertCircle } from 'lucide-react'

const STEP_LABELS: Record<string, string> = {
  WAITING_ENTRY: 'Waiting for Entry Alert',
  ENTRY_SIGNAL_RECEIVED: 'Entry Alert Received',
  ENTRY_RISK_CHECK: 'Risk Check',
  ENTRY_ORDER_SENDING: 'Sending Entry Order',
  ENTERED: 'Entered',
  OPEN: 'Position Entered',
  WAITING_EXIT: 'Waiting for Exit Alert',
  EXIT_SIGNAL_RECEIVED: 'Exit Alert Received',
  EXIT_ORDER_SENDING: 'Sending Exit Order',
  EXITED: 'Exited',
  BLOCKED: 'Trade Blocked',
}

function StepIcon({ status }: { status: string }) {
  switch (status) {
    case 'done':    return <CheckCircle size={20} style={{ color: '#98e94d' }} />
    case 'active':  return <Clock size={20} style={{ color: '#98e94d' }} className="animate-pulse" />
    case 'blocked': return <XCircle size={20} style={{ color: '#ef4444' }} />
    case 'error':   return <AlertCircle size={20} style={{ color: '#f59e0b' }} />
    default:        return <Circle size={20} style={{ color: '#3a3933' }} />
  }
}

function stepColor(status: string): string {
  if (status === 'active')                        return '#98e94d'
  if (status === 'done')                          return '#d8d3c8'
  if (status === 'blocked' || status === 'error') return '#f87171'
  return '#77736c'
}

export default function LiveFlowTimeline({ steps }: { steps: LiveFlowStep[] }) {
  if (steps.length === 0) {
    return <p className="text-sm" style={{ color: '#77736c' }}>No flow data yet.</p>
  }
  return (
    <div className="relative pl-6">
      {steps.map((step, i) => (
        <div key={i} className="relative flex items-start gap-4 mb-6 last:mb-0">
          {i < steps.length - 1 && (
            <div className="absolute left-[9px] top-6 bottom-0 w-px" style={{ background: '#24231f' }} />
          )}
          <div className="relative z-10 mt-0.5 shrink-0"><StepIcon status={step.status} /></div>
          <div className="flex-1 min-w-0">
            <p className="font-medium text-sm" style={{ color: stepColor(step.status) }}>
              {STEP_LABELS[step.step] || step.step}
            </p>
            {step.message && <p className="text-xs mt-0.5" style={{ color: '#9a968f' }}>{step.message}</p>}
            {step.order_id && <p className="text-xs mt-0.5 font-mono" style={{ color: '#77736c' }}>Order: {step.order_id}</p>}
            {step.reason && (
              <p className="text-xs mt-0.5 rounded px-2 py-1" style={{ color: '#f87171', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.15)' }}>
                Blocked: {step.reason}
              </p>
            )}
            {step.timestamp && <p className="text-xs mt-1" style={{ color: '#3a3933' }}>{new Date(step.timestamp).toLocaleTimeString()}</p>}
          </div>
        </div>
      ))}
    </div>
  )
}
