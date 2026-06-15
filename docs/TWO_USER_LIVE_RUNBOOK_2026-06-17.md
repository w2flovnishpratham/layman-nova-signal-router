# Two-User Live Pilot - June 17, 2026

## Safety State

Keep the production deployment in `safe` mode until both users complete every
readiness step. Safe mode sets:

- `ENABLE_LIVE_ORDERS=false`
- `DHAN_READ_ONLY_REAL_DATA=true`
- `EXECUTION_NODE_ROUTING_ENABLED=false`

## Account Setup

Complete these steps independently on two devices:

1. Sign in with the intended Google account.
2. Choose **Live** mode.
3. Select one available Dhan static IP. The two accounts must select different IPs:
   - `165.232.184.177`
   - `167.71.232.232`
4. Add only the selected IP to that account's Dhan static-IP whitelist.
5. Generate a fresh Dhan access token on June 17 and submit the Client ID and token.
6. Confirm that Dhan account verification succeeds.
7. Select `Supertrend`, one lot, and the required daily loss/trade limits.
8. Prefer a broker-managed Dhan Super Order exit mode for the pilot:
   `Target Profit` or `Custom SL & TP`.
9. Stop at the final live confirmation until both accounts are ready.

The backend enforces one user per egress IP. Credentials, runtime files, open
positions, audit logs, queued alerts, Dhan calls, and monitor loops are isolated
by user ID.

## Arm Production

In GitHub:

1. Open **Actions**.
2. Select **Layman backend CI and deploy**.
3. Choose **Run workflow**.
4. Set `live_trading_mode` to `armed`.
5. Enter the exact confirmation: `ARM TWO USER LIVE`
6. Run the workflow and wait for both `test` and `deploy` to pass.

Confirm `/api/health` reports:

- `live_orders_enabled: true`
- `execution_node_routing_enabled: true`
- `multi_user_mode: true`
- `strategy_job_worker.running: true`

Only then should both users press the final **Trade Real Money - Confirm** button.

## First Alert

1. Use one controlled TradingView Supertrend alert.
2. Keep both Dhan order books open.
3. Verify one order appears in each account with that account's configured lot size.
4. Verify each account independently shows its position and broker-managed exit legs.
5. Do not send another alert until both accounts match the expected state.

## Emergency Stop

If either account differs from the expected state:

1. Stop new TradingView alerts.
2. Use Dhan directly to flatten any uncertain position.
3. Run the GitHub workflow again with `live_trading_mode: safe`.
4. Stop both NOVA sessions.

Running the workflow in `safe` mode immediately restores all server-side
real-order gates to blocked and restarts the backend.
