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
    case 'done': return <CheckCircle className="text-green-400" size={20} />
    case 'active': return <Clock className="text-brand-500 animate-pulse" size={20} />
    case 'blocked': return <XCircle className="text-red-400" size={20} />
    case 'error': return <AlertCircle className="text-orange-400" size={20} />
    default: return <Circle className="text-gray-600" size={20} />
  }
}

export default function LiveFlowTimeline({ steps }: { steps: LiveFlowStep[] }) {
  if (steps.length === 0) {
    return <p className="text-gray-500 text-sm">No flow data yet.</p>
  }

  return (
    <div className="relative pl-6">
      {steps.map((step, i) => (
        <div key={i} className="relative flex items-start gap-4 mb-6 last:mb-0">
          {/* Connector line */}
          {i < steps.length - 1 && (
            <div className="absolute left-[9px] top-6 bottom-0 w-px bg-gray-700" />
          )}
          <div className="relative z-10 mt-0.5 shrink-0">
            <StepIcon status={step.status} />
          </div>
          <div className="flex-1 min-w-0">
            <p className={`font-medium text-sm ${
              step.status === 'active' ? 'text-brand-500' :
              step.status === 'done' ? 'text-gray-200' :
              step.status === 'blocked' || step.status === 'error' ? 'text-red-400' :
              'text-gray-500'
            }`}>
              {STEP_LABELS[step.step] || step.step}
            </p>
            {step.message && (
              <p className="text-gray-400 text-xs mt-0.5">{step.message}</p>
            )}
            {step.order_id && (
              <p className="text-gray-500 text-xs mt-0.5 font-mono">Order: {step.order_id}</p>
            )}
            {step.reason && (
              <p className="text-red-400 text-xs mt-0.5 bg-red-900/20 rounded px-2 py-1">
                Blocked: {step.reason}
              </p>
            )}
            {step.timestamp && (
              <p className="text-gray-600 text-xs mt-1">
                {new Date(step.timestamp).toLocaleTimeString()}
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
