import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { ConversationState } from '../state/conversationMachine'
import { getBrokerStatus } from './brokerApi'
import { deriveSteps } from './setupFields'
import { SetupConfigPanel, SetupStepRail } from './SetupProgressPanel'

export interface SetupSnapshot {
  state: ConversationState
  strategyName: string | null
  strategyVersion: string | null
}

/** The /app/setup screen: step rail, the setup conversation, and a live
    configuration panel. All three read the same conversation state. */
export function SetupPage({ conversation, snapshot }: { conversation: ReactNode; snapshot: SetupSnapshot | null }) {
  const [broker, setBroker] = useState<{ connected: boolean; masked: string | null }>({ connected: false, masked: null })

  const loadBroker = useCallback(async () => {
    try {
      const status = await getBrokerStatus()
      setBroker({ connected: status.has_dhan_client_id, masked: status.dhan_client_id_masked })
    } catch {
      // A broker read failure must not claim "connected"; the card stays truthful.
      setBroker({ connected: false, masked: null })
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadBroker()
  }, [loadBroker])

  const steps = useMemo(() => deriveSteps({
    brokerConnected: broker.connected,
    brokerMasked: broker.masked,
    strategyName: snapshot?.strategyName ?? null,
    strategyVersion: snapshot?.strategyVersion ?? null,
    mode: snapshot?.state.mode ?? null,
    fields: snapshot?.state.fields ?? [],
    draft: snapshot?.state.draft ?? {},
    activeKey: snapshot?.state.activeQuestionKey ?? null,
    reviewing: snapshot?.state.phase === 'SETUP_REVIEW' || snapshot?.state.phase === 'ENGINE_READY',
  }), [broker, snapshot])

  const mode = snapshot?.state.mode ?? null
  const allAnswered = steps.every((s) => s.status === 'done' || s.id === 'review')

  return (
    <div className="nova-setup">
      <SetupStepRail steps={steps} />
      <section className="nova-setup-main" aria-label="Setup conversation">{conversation}</section>
      <SetupConfigPanel
        steps={steps}
        // Arming happens in the review step inside the conversation; this panel
        // never bypasses it, so the button only ever reports readiness.
        canArm={false}
        armLabel={
          allAnswered
            ? `Arm Engine (${mode === 'live' ? 'Live' : 'Paper'}) — confirm in the review step`
            : `Arm Engine (${mode === 'live' ? 'Live' : 'Paper'}) — finish setup first`
        }
      />
    </div>
  )
}
