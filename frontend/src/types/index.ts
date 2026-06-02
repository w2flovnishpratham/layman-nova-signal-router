export interface AppState {
  state: string
  last_signal_id: string | null
  last_alert_at: string | null
  last_message: string
  engine_started?: boolean
  webhook_trading_enabled?: boolean
  live_orders_enabled: boolean
  dhan_mode: 'MOCK' | 'REAL' | string
  emergency_stop: boolean
  global_kill_switch: boolean
}

export interface ExternalPositionItem {
  source?: string
  detected_at?: string
  trading_symbol?: string | null
  security_id?: string | null
  net_qty?: number | string | null
  position_type?: string | null
  product_type?: string | null
  exchange_segment?: string | null
}

export interface ExternalOrderItem {
  source?: string
  detected_at?: string
  order_id?: string | null
  order_status?: string | null
  trading_symbol?: string | null
  security_id?: string | null
  remaining_quantity?: number | string | null
}

export interface ExternalPositionsSnapshot {
  status: string
  message: string
  last_checked_at: string | null
  stale?: boolean
  external_count: number
  positions: ExternalPositionItem[]
  open_orders: ExternalOrderItem[]
  broker_active_count?: number
  broker_open_order_count?: number
  local_position_present?: boolean
  local_position_matched?: boolean
  manual_exit_detected?: boolean
  sl_tp_drift?: {
    status: string
    drift_detected: boolean
    message: string
    checked_at: string | null
    expected?: {
      stop_loss_price?: number | null
      target_price?: number | null
    }
    actual?: {
      stop_loss_price?: number | null
      target_price?: number | null
    }
    items?: Array<{
      leg?: string
      expected_price?: number | null
      actual_price?: number | null
      drift?: boolean
      order_id?: string | null
      leg_name?: string | null
    }>
  }
  failures?: string[]
}

export interface OpenPosition {
  has_open_position: boolean
  strategy_code: string | null
  security_id: string | null
  trading_symbol: string | null
  qty: number
  entry_order_id: string | null
  entry_price: number | null
  opened_at: string | null
  live_pnl?: {
    source?: string
    status?: string
    entry_price?: number | null
    ltp?: number | null
    qty?: number | null
    sl_price?: number | null
    tp_price?: number | null
    unrealized_pnl?: number | null
    pnl_percent?: number | null
    exit_reason?: string | null
    quote_age_seconds?: number | null
    message?: string
    error?: string | null
    last_checked_at?: string | null
    ws_status?: Record<string, unknown> | null
  } | null
  broker_sync?: {
    source?: string
    status?: string
    message?: string
    checked_at?: string
    cleared?: boolean
    active_positions_count?: number
    open_orders_count?: number
    failures?: string[]
  }
  external_positions?: ExternalPositionsSnapshot
}

export interface RuntimeSettings {
  allow_entry: boolean
  allow_exit: boolean
  max_qty_per_order: number
  option_disable_sl: boolean
  server_side_exit_enabled?: boolean
  marketfeed_ws_enabled?: boolean
  option_ltp_source?: 'WEBSOCKET' | 'REST' | 'AUTO' | string
  option_exit_mode?: 'DHAN_SUPER' | 'SERVER' | string
  option_ws_stale_seconds?: number
  option_rest_fallback_enabled?: boolean
  option_rest_fallback_cooldown_seconds?: number
  option_sl_percent?: number
  option_tp_percent?: number
  option_ltp_poll_seconds?: number
  emergency_stop: boolean
  global_kill_switch: boolean
  /** H8 — Monotonic version bumped on every settings write. Send back as
   * `expected_version` on the next save to detect concurrent edits. */
  _version?: number
}

export interface WalletSnapshot {
  success: boolean
  message: string
  client_id: string | null
  available_balance: number | null
  withdrawable_balance: number | null
  utilized_amount: number | null
  sod_limit: number | null
  collateral_amount: number | null
  blocked_payout_amount: number | null
  session_start_balance: number | null
  session_pnl: number | null
  last_checked_at: string | null
  raw_response?: Record<string, unknown> | null
}

export interface DashboardSummary {
  dhan_connected?: boolean
  dhan_client_id_masked?: string | null
  webhook_secret_set?: boolean
  engine_started?: boolean
  app_state: AppState
  open_position: OpenPosition
  external_positions?: ExternalPositionsSnapshot
  wallet: WalletSnapshot
  settings: RuntimeSettings
  webhook_url?: string
  mode: {
    dhan_mode: string
    live_orders_enabled: boolean
    dhan_token_configured: boolean
    webhook_trading_enabled?: boolean
  }
  last_logs: Record<string, unknown>
}

export interface RiskSetupPayload {
  max_qty_per_order: number
  option_disable_sl?: boolean
  server_side_exit_enabled?: boolean
  marketfeed_ws_enabled?: boolean
  option_ltp_source?: 'WEBSOCKET' | 'REST' | 'AUTO' | string
  option_exit_mode?: 'DHAN_SUPER' | 'SERVER' | string
  option_ws_stale_seconds?: number
  option_rest_fallback_enabled?: boolean
  option_rest_fallback_cooldown_seconds?: number
  option_sl_percent?: number
  option_tp_percent?: number
  option_ltp_poll_seconds?: number
  allow_entry: boolean
  allow_exit: boolean
  /** H8 — Optional optimistic-locking version. Send the `_version` value
   * read from the latest RuntimeSettings; backend returns 409 if stale. */
  expected_version?: number
}

export interface RiskSettingsPatchPayload {
  max_qty_per_order?: number
  option_disable_sl?: boolean
  server_side_exit_enabled?: boolean
  marketfeed_ws_enabled?: boolean
  option_ltp_source?: 'WEBSOCKET' | 'REST' | 'AUTO' | string
  option_exit_mode?: 'DHAN_SUPER' | 'SERVER' | string
  option_ws_stale_seconds?: number
  option_rest_fallback_enabled?: boolean
  option_rest_fallback_cooldown_seconds?: number
  option_sl_percent?: number
  option_tp_percent?: number
  option_ltp_poll_seconds?: number
  allow_entry?: boolean
  allow_exit?: boolean
  /** H8 — See RiskSetupPayload.expected_version. */
  expected_version?: number
}

export interface DhanConnectPayload {
  client_id?: string
  access_token?: string
}

export interface TokenAge {
  token_saved_at: string | null
  token_age_minutes: number | null
  token_estimated_expiry_at: string | null
  token_expired: boolean | null
  token_warn: boolean | null
  token_max_age_hours: number | null
  token_warn_age_hours: number | null
}

export interface SetupStatus {
  dhan_connected: boolean
  dhan_client_id_masked: string | null
  access_token_present: boolean
  access_token_masked: string | null
  webhook_secret_set: boolean
  webhook_secret_masked: string | null
  risk_configured: boolean
  engine_started: boolean
  wallet: WalletSnapshot
  backend_public_base_url: string
  webhook_url: string
  outgoing_ip: string | null
  outgoing_ip_check?: Record<string, unknown>
  static_ip_note: string
  token_age?: TokenAge
  mode: {
    dhan_mode: string
    live_orders_enabled: boolean
  }
  settings: RuntimeSettings
  app_state: AppState
  readiness: {
    ready: boolean
    issues: string[]
    warnings: string[]
    risk_configured: boolean
  }
  debug_enabled: boolean
  vault: {
    ready: boolean
    local_mock_allowed?: boolean
    file_exists: boolean
    path: string
    error: string | null
  }
}

export interface EngineStatus {
  engine_started: boolean
  webhook_trading_enabled: boolean
  app_state: AppState
  setup: SetupStatus
}

export interface LiveFlowStep {
  step: string
  status: 'pending' | 'active' | 'done' | 'blocked' | 'error'
  timestamp: string | null
  message: string | null
  order_id: string | null
  reason: string | null
}

export interface OrderEvent {
  id: number
  created_at: string | null
  phase: string | null
  signal_id: string | null
  payload_format: string | null
  action: string | null
  side: string | null
  normalized_action: string | null
  normalized_side: string | null
  normalized_qty: number | null
  normalized_symbol: string | null
  normalized_strike: number | null
  normalized_expiry: string | null
  normalized_option_side: string | null
  dhan_mode: string | null
  live_orders_enabled: boolean | null
  order_id: string | null
  status: string | null
  success: boolean | null
  blocked: boolean | null
  reason: string | null
  security_id: string | null
  trading_symbol: string | null
  qty: number | null
  order_type: string | null
  product_type: string | null
  avg_price: number | null
  request?: Record<string, unknown> | null
  response?: Record<string, unknown> | null
}

export interface LogBundle {
  webhook_events: Record<string, unknown>[]
  order_events: Record<string, unknown>[]
  audit_events: Record<string, unknown>[]
  error_events: Record<string, unknown>[]
}

export interface WebhookUrlInfo {
  public_base_url: string
  tradingview_webhook_url: string
  webhook_trading_enabled: boolean
  dhan_mode: string
  live_orders_enabled: boolean
}

export interface ScripMasterFileInfo {
  exists: boolean
  path: string
  size_bytes?: number
}

export interface ScripMasterRefreshJob {
  job_id: string | null
  status: 'IDLE' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | string
  started_at: string | null
  finished_at: string | null
  success: boolean | null
  message: string | null
  error: string | null
  path: string | null
  size_bytes: number | null
  results: Array<Record<string, unknown>>
}

export interface ScripMasterStatus {
  configured_path: ScripMasterFileInfo
  fallback_path: ScripMasterFileInfo
  auto_resolve_security_id: boolean
  allow_default_security_id: boolean
  last_download: Record<string, unknown>
  refresh_job: ScripMasterRefreshJob
  download_urls: string[]
}

export interface ScripMasterRefreshResponse {
  success: boolean
  accepted: boolean
  job_id: string
  status: string
  refresh_job: ScripMasterRefreshJob
  message: string
}

export interface DhanDebugConfig {
  ok: boolean
  outgoing_ip: string | null
  dhan: {
    mode: string
    live_orders_enabled: boolean
    client_id_present: boolean
    client_id_masked?: string | null
    access_token_present: boolean
    access_token_masked: string | null
    public_webhook_url: string
    market_closed_debug: boolean
    force_allow_order_when_market_closed: boolean
    allow_default_security_id: boolean
    default_security_id_present: boolean
    dhan_scrip_master_path: string
    auto_resolve_security_id: boolean
  }
  safety: {
    emergency_stop: boolean
    global_kill_switch: boolean
    require_market_hours: boolean
    market_is_open: boolean
    market_closed_debug: boolean
    force_allow_order_when_market_closed: boolean
  }
  last_dhan_status_code: number | null
  last_dhan_interpreted_error: {
    category: string
    message: string
    next_action: string
  } | null
  last_dhan_response?: Record<string, unknown> | null
  issues: string[]
  warnings: string[]
}
