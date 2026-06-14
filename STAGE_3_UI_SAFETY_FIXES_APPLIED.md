You are working on the Nova Trading / Layman Signal Router project.

Read these files first:

1. `DEEP_REVIEW_AND_FIX_PLAN.md`
2. `STAGE_0_1_SECURITY_FIXES_APPLIED.md`
3. `STAGE_2_TRADING_SECURITY_FIXES_APPLIED.md`
4. Current frontend and backend code

Your task is to implement **Stage 3 only**.

Do not implement Stage 4 or Stage 5.

Stage 3 goal:

Make the UI/UX trustworthy and safe for a financial trading product by making Paper vs Live status impossible to miss, preventing accidental live actions, preventing double-click/double-submit risks, showing safety readiness clearly, and making users understand exactly what is safe and unsafe.

Keep `ENABLE_LIVE_ORDERS=false` by default.

---

# Stage 3 Scope

Implement only frontend/UI/UX safety improvements and small backend support endpoints if needed.

Focus on:

1. Persistent Paper/Live mode visibility
2. Typed confirmation before Live
3. Dangerous action confirmation
4. Pending/loading/disabled states
5. Broker credential trust display
6. Webhook signing status display
7. Safety checklist before launch
8. Better error/empty/loading states
9. Mobile-safe trading controls
10. Operator-visible production readiness status

---

# Important Context

Current safety status after Stage 2:

* Multi-user paper beta: conditional GO
* Single trusted operator live: NO-GO
* Public live launch: NO-GO
* 100-user production launch: NO-GO

Reason live is still blocked:

* webhook signing relay not deployed
* executor/per-user egress routing not verified
* runtime trading state still partly JSON-backed
* multi-worker trading workers still unsafe
* infra/monitoring/hardening not complete

The UI must reflect this honestly.

Do not make the UI pretend live trading is ready.

---

# Frontend Files To Inspect

Review and modify relevant files under:

* `frontend/src/api.ts`
* `frontend/src/lib/backend.ts`
* `frontend/src/ws.ts`
* `frontend/src/state/sessionStore.ts`
* `frontend/src/components/**`
* setup components:

  * `BrokerForm`
  * `RiskForm`
  * `StrategyPicker`
  * `ConfirmLaunchCard`
  * `SetupPanel`
  * `DeploymentSummary`
* dashboard/trading components:

  * active trade card
  * engine status card
  * quick action buttons
  * chat input
  * event/message cards
  * mode/status banners
  * broker connection cards
  * webhook connection cards

Backend files may be touched only if a small status endpoint is needed.

---

# 1. Persistent Paper/Live Safety Banner

Add a persistent top-level banner visible on every trading/setup screen.

It must show:

* Current mode:

  * PAPER
  * LIVE
  * UNKNOWN / DISCONNECTED
* Backend safety status:

  * live orders enabled/disabled
  * trading workers enabled/disabled
  * worker role
  * webhook HMAC required
  * replay protection active
  * executor/egress routing status
* User-facing warning:

  * Paper mode: “Paper mode — no real orders”
  * Live disabled: “Live trading is disabled by system policy”
  * Live mode: “LIVE MODE — real broker orders can be placed”

Design rules:

* Paper should be calm but visible.
* Live must be impossible to miss.
* Unknown/disconnected must warn the user not to trust the screen.
* The banner must not disappear while scrolling if the app has a trading dashboard.

---

# 2. Typed Live Confirmation

Before any Live launch/start action, require a typed confirmation.

User must type exactly:

```txt
LIVE
```

or better:

```txt
START LIVE WITH REAL MONEY
```

Backend already has live guards, but frontend must prevent accidental clicks.

Rules:

* No one-click Live.
* No double-click Live.
* Confirmation modal must show:

  * broker connected user/account
  * strategy selected
  * risk limits
  * quantity/max loss
  * webhook signing status
  * executor/egress status
  * statement that real orders may be placed
* If backend says live is not allowed, the Live button must be disabled and explain why.

Acceptance:

* Clicking Live without typed confirmation does nothing.
* Typed confirmation enables exactly one submit.
* Submit button disables while pending.
* If request fails, the modal shows safe error and allows retry.

---

# 3. Dangerous Action Confirmation

Add confirmation for dangerous controls:

* panic exit
* emergency stop
* global kill switch
* reset state
* fresh start
* disconnect broker
* delete/rotate webhook secret
* any action that can affect order placement or positions

Each confirmation must show:

* exact action
* consequence
* whether it affects real broker orders
* current mode
* typed confirmation for the most destructive actions

Suggested typed confirmations:

```txt
PANIC EXIT
RESET
DISCONNECT
ROTATE SECRET
```

Acceptance:

* Dangerous actions cannot fire from a single accidental click.
* Buttons are disabled while request is pending.
* Duplicate rapid clicks do not send duplicate API calls.

---

# 4. Pending / Loading / Disabled States

Audit every button that calls backend APIs.

Fix:

* disable while pending
* show spinner or “Working…”
* prevent duplicate submission
* show success/failure toast
* do not leave stale optimistic state if backend fails
* recover cleanly after reconnect

Apply especially to:

* connect broker
* generate/save webhook secret
* save risk
* pick strategy
* start engine
* pause/resume
* panic exit
* reset/fresh start
* mode switch
* copy webhook URL/secret

Acceptance:

* no backend action button can be double-submitted from UI
* frontend state matches backend response
* failed requests show understandable error

---

# 5. Broker Credential Trust UI

Improve broker connection display.

Show:

* broker connected/not connected
* masked client id
* token saved status without revealing token
* token age if backend exposes it
* last verified time
* Dhan profile/funds check status if available
* warning if token may be expired
* warning if broker is connected but live is disabled

Rules:

* Never display raw access token.
* Never log token.
* Copy buttons must not copy secrets accidentally.
* If a secret must be shown once, clearly label it as one-time display.

---

# 6. Webhook Signing Status UI

After Stage 2, production webhooks require timestamped HMAC and likely a signing relay.

Add UI to show:

* webhook URL
* whether HMAC is required
* whether timestamp replay protection is active
* whether signing relay is configured
* whether legacy unsigned mode is disabled
* last webhook received time
* last webhook rejected reason category, if safe
* user’s current webhook secret status, masked

Important:

TradingView cannot directly generate HMAC headers. The UI must say:

“Production live alerts require a signing relay. Direct unsigned TradingView alerts are not live-safe.”

Do not expose raw webhook secret except during creation/rotation, if current design already does so.

---

# 7. Safety Readiness Checklist

Create a visible “Launch Readiness” or “Safety Checklist” component.

It should show checklist items like:

For Paper:

* logged in
* broker connected or paper broker ready
* strategy selected
* risk limits saved
* webhook secret configured
* live orders disabled

For Live:

* logged in
* broker connected
* risk limits saved
* webhook HMAC active
* timestamp replay protection active
* signing relay configured
* executor/egress routing verified
* live orders enabled by operator
* trading worker policy safe
* market hours valid

Live launch must be blocked if any required live item is missing.

Acceptance:

* Paper beta can proceed with paper-safe checklist.
* Live checklist clearly shows blocked items.
* User knows why Live is unavailable.

---

# 8. Better Empty, Error, and Reconnect States

Improve user experience for:

* backend disconnected
* websocket disconnected
* unauthenticated session expired
* CSRF expired
* broker token expired
* no active trades
* no orders yet
* no positions yet
* webhook never received
* market closed
* live disabled by policy

Each state should be clear and non-technical where possible.

Examples:

* “Session expired. Please sign in again.”
* “Backend disconnected. Do not trust live status until reconnect.”
* “No active paper trades yet.”
* “Live trading is blocked because executor routing is not verified.”

---

# 9. Mobile Safety

Review mobile layout.

Fix:

* destructive buttons too close together
* tiny action buttons
* modals that overflow
* important warnings hidden below fold
* mode banner not visible
* tables unreadable
* horizontal scroll where needed

Acceptance:

* At 375px width, user can clearly see Paper/Live mode.
* Dangerous buttons still require confirmation.
* No accidental live action is easy on mobile.

---

# 10. Backend Support Endpoint If Needed

If the frontend does not already have enough safety state, add a small endpoint:

```txt
GET /api/safety/status
```

It should return safe non-secret values only:

```json
{
  "mode": "paper",
  "live_orders_enabled": false,
  "auth_required": true,
  "webhook_hmac_required": true,
  "webhook_replay_protection": true,
  "worker_role": "web",
  "trading_workers_enabled": false,
  "executor_routing_enabled": false,
  "unique_egress_required": true,
  "signing_relay_configured": false,
  "public_live_launch_allowed": false,
  "single_operator_live_allowed": false,
  "reasons_live_blocked": [
    "Signing relay not configured",
    "Executor egress routing not verified",
    "Trading workers disabled for authenticated multi-user mode"
  ]
}
```

Rules:

* Must require auth.
* Must not expose secrets.
* Must not expose internal file paths.
* Must not expose raw env values.
* Must be tested.

---

# 11. Tests

Add or update tests.

Frontend tests/security scripts should verify:

* all API calls use the shared wrapper or credentials include
* mutating requests include CSRF
* Live button disabled when safety status blocks live
* typed Live confirmation required
* dangerous action confirmation required
* pending state prevents double-submit
* raw token is not rendered after save
* Paper/Live banner renders correct status
* disconnected state is visible

Backend tests if endpoint added:

* `/api/safety/status` requires auth
* status returns no secrets
* status correctly blocks live when Stage 2/4/5 prerequisites are missing

Run:

```bash
cd backend
python -m pytest app/tests -q
python -m compileall app
python scripts/check_repo_hygiene.py
```

If frontend touched:

```bash
cd frontend
npm run test:security
npm run lint
npm run build
```

---

# Do Not Do

Do not:

* enable live trading
* claim live is ready
* remove backend safety guards
* weaken CSRF/auth
* expose raw broker access token
* expose webhook secret except existing one-time reveal behavior
* add fake safety status
* make frontend bypass backend live restrictions
* implement Redis/Postgres state migration
* implement executor node routing
* implement systemd/nginx hardening
* implement Stage 4/5 items

---

# Required Output

After implementation, create:

`STAGE_3_UI_SAFETY_FIXES_APPLIED.md`

It must include:

## 1. Summary

* What Stage 3 fixed
* What remains unsafe
* Whether live trading remains disabled

## 2. Files Changed

Table:

| File | Change | Reason |

## 3. Paper/Live Safety UX

Explain:

* persistent banner
* mode display
* disconnected/unknown mode behavior
* live warning behavior

## 4. Confirmation UX

Explain:

* typed Live confirmation
* dangerous action confirmations
* duplicate-submit prevention

## 5. Safety Checklist

Explain:

* paper checklist
* live checklist
* why live is blocked
* how user sees missing requirements

## 6. Broker/Webhook Trust UI

Explain:

* broker credential masking
* token status
* webhook HMAC/replay/signing relay status
* no secret exposure

## 7. Tests Added

List all new tests.

## 8. Verification Results

Include exact command results.

## 9. Remaining Risks

Mention clearly:

* public live launch is still not approved
* single trusted operator live is still blocked unless signing relay and executor/egress routing are verified
* Stage 4 deployment hardening still required
* Stage 5 100-user scaling still required

## 10. Go / No-Go Status

State:

* Multi-user paper beta:
* Single trusted operator live:
* Public live launch:
* 100-user production launch:

End with next recommended stage.
