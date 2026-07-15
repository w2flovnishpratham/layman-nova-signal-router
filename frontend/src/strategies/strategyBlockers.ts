// Human-readable text for the safe activation blocker codes the backend emits
// (strategy_instance_service._BLOCKER_CODES / list_engine_strategies).
const BLOCKER_TEXT: Record<string, string> = {
  PAPER_MODE_REQUIRED: 'Switch to paper mode',
  INVALID_LOTS: 'Set a valid lot count',
  CREDENTIAL_INACTIVE: 'Waiting for an active private credential',
  HOLD_NOT_VERIFIED: 'Waiting for a genuine TradingView HOLD',
  PINE_NOT_APPROVED: 'Approved Pine version required',
  TRADINGVIEW_NOT_INSTALLED: 'TradingView installation pending',
  PAPER_ENTRY_NOT_VERIFIED: 'Waiting for a confirmed paper entry',
  PAPER_EXIT_NOT_VERIFIED: 'Waiting for a confirmed paper exit',
  SETUP_BLOCKED: 'Setup is blocked; contact NOVA',
  STRATEGY_STOPPED: 'Strategy is stopped',
}

export function blockerText(code?: string | null): string {
  if (!code) return 'Not ready yet'
  return BLOCKER_TEXT[code] ?? code.replaceAll('_', ' ').toLowerCase()
}
