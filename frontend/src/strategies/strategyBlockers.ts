// Human-readable text for the safe activation blocker codes the backend emits
// (strategy_instance_service._BLOCKER_CODES / list_engine_strategies).
const BLOCKER_TEXT: Record<string, string> = {
  C2_FEATURE_DISABLED: 'Feature disabled',
  C2_READINESS_UNAVAILABLE: 'Readiness temporarily unavailable',
  PAPER_MODE_REQUIRED: 'Switch to paper mode',
  INVALID_LOTS: 'Set a valid lot count',
  CREDENTIAL_INACTIVE: 'Waiting for an active private credential',
  HOLD_NOT_VERIFIED: 'Waiting for a genuine TradingView HOLD',
  PINE_NOT_APPROVED: 'Approved Pine version required',
  TRADINGVIEW_NOT_INSTALLED: 'TradingView installation pending',
  INSTALLATION_INACTIVE: 'TradingView installation inactive',
  INSTALLATION_OWNER_INVALID: 'Installation owner binding invalid',
  CREDENTIAL_BINDING_INVALID: 'Credential binding invalid',
  CANDIDATE_INTEGRITY_INVALID: 'Candidate integrity changed',
  SOURCE_INTEGRITY_INVALID: 'Source integrity changed',
  STRATEGY_LAYER_INTEGRITY_INVALID: 'Strategy layer integrity changed',
  PAPER_ENTRY_NOT_VERIFIED: 'Waiting for a confirmed paper entry',
  PAPER_EXIT_NOT_VERIFIED: 'Waiting for a confirmed paper exit',
  SETUP_BLOCKED: 'Setup is blocked; contact NOVA',
  STRATEGY_STOPPED: 'Strategy is stopped',
  VERIFICATION_IN_PROGRESS: 'Verification in progress',
  VERIFICATION_NOT_STARTED: 'Start verification to begin',
  PAPER_EXECUTION_DISABLED: 'Paper execution is unavailable',
  LIVE_EXECUTION_SAFETY_BLOCK: 'Blocked: live execution is enabled',
  ADMIN_ACTION_REQUIRED: 'A NOVA admin must act next',
  VERIFICATION_FAILED: 'Verification could not start',
}

export function blockerText(code?: string | null): string {
  if (!code) return 'Not ready yet'
  return BLOCKER_TEXT[code] ?? code.replaceAll('_', ' ').toLowerCase()
}
