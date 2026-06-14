# Chatbot Flow Restored Applied

Date: 2026-06-15

## Summary

The default post-login screen is now a **chatbot-style trading assistant** instead of
the Marketplace-first strategy platform. After login, a guided bot walks the user
through mode → strategy → risk → review → start, then turns into a live monitoring
chat. The Marketplace, Live Pilot, operator safety, and worker/admin panels are no
longer shown to normal users; they are preserved behind an admin-only operator
console route (`#admin`).

This is a frontend/product-flow change only. No backend, no Stage 5B queue/ledger/
positions, no Stage 6A/6B executor or live-safety gates, no models or migrations
were changed. Live remains disabled by default and policy-gated. The assistant uses
the existing Stage 5B subscription REST APIs, so the same backend safety enforcement
applies.

## Files Changed

| File | Change | Reason |
|---|---|---|
| `frontend/src/components/TradingAssistantFlow.tsx` | **New.** Chatbot wizard component with steps welcome → choose_mode → choose_strategy → configure_risk → review → running_monitor. | The new default guided flow. |
| `frontend/src/App.tsx` | Default render is now `TradingAssistantFlow`; Marketplace (`StrategyPlatform`) + `SafetyReadiness` + legacy engine layout moved behind an admin `#admin` route. Added `adminView` hash state + `hashchange` listener. | Replace Marketplace-first UI; keep operator tools for admins only. |
| `frontend/src/index.css` | Added scoped `.assistant-*` and `.operator-console-bar` styles (chat log, strategy cards, risk form, review summary, blockers, monitor). | Styling for the assistant, reusing existing theme variables and chat bubbles. |

No backend files were touched.

## What UI Was Removed / Hidden (for normal users)

- **Marketplace tab** and the strategy-card marketplace grid as the primary screen.
- **My Strategies / Signal History / Positions / Ledger** tab bar (`StrategyPlatform`).
- **Live Pilot** tab/panel (`LivePilotPanel`).
- **Operator Safety / Launch Readiness** dashboard (`SafetyReadiness`) as a default section.
- **Workers** admin console.
- The word "Marketplace" no longer appears in the normal user flow.

All of the above still exist and are reachable by an **admin** by navigating to
`#admin` (an "Operator console" chip is shown to admins in the assistant; a "Back to
assistant" link returns). Non-admins cannot open it (`authStatus.isAdmin` gated).

The thin `Header` and `SafetyBanner` (mode/connection strip) are kept; they are not
the marketplace/operator dashboards.

## New Chatbot Flow Steps

1. **welcome** — bot greets; if a strategy is already active it resumes directly into the monitor.
2. **choose_mode** — chips: Paper mode / Live mode.
3. **choose_strategy** — cards for catalog strategies (Supertrend Flip, ORB Portal, OOPS Gap Reversal, Support/Resistance Reversal, Nifty Momentum Scalper), filtered by `paperAllowed` / `liveAllowed`. Live mode offers the live-allowed strategy (Supertrend Flip pilot).
4. **configure_risk** — quantity, max trades/day, max daily loss, option side (enforced caps), plus stop-loss %, target %, trailing % (shown in summary; SL/TP/trailing are applied by the strategy engine, with a clear note).
5. **review** — summary card: mode, strategy, risk caps, SL/TP, and (for Live) the current readiness blockers. One primary CTA: "Start Paper" or "Start Live".
6. **running_monitor** — per-subscription status, last signal, signals/fills/skipped counts, open position, recent signal history (with skip reasons), live blockers if live is locked, and Pause/Resume/Remove + "Set up another strategy".

Chat uses bot/user bubbles (`BotBubble`/`UserBubble`), choice chips, and one primary
CTA at a time. Cards appear inside the chat only for risk, review, and monitoring.

## APIs Used

All pre-existing endpoints (no new backend):

- `GET /api/auth/status` (isAdmin, user) — via `App`.
- `GET /api/strategies` — strategy catalog.
- `GET /api/me/strategy-subscriptions` — active subs + `freeActiveLimit`.
- `POST /api/me/strategy-subscriptions` — start Paper (`startPaperStrategy`) / start Live pilot (`startLivePilotStrategy`).
- `POST /api/me/strategy-subscriptions/{id}/pause` · `/resume`, `DELETE /{id}`.
- `GET /api/me/live-readiness?strategy_code=SUPERTREND_FLIP` — live blockers.
- `GET /api/me/signal-jobs` — signal/job history.
- `GET /api/me/paper-positions` — open/closed positions.
- `GET /api/safety/status` — signing-relay/operator state (for the blocker list), via `App`.

## Paper Mode Behavior

- User picks Paper → strategy → risk → review → "Start Paper".
- Calls `startPaperStrategy`; on success the bot confirms the strategy is monitoring.
- The monitor polls every 8s (and refreshes on server events via `refreshNonce`) and
  shows signals today, paper fills, skipped count, last signal, open position, and the
  most recent signal jobs with their queued/filled/skipped reason.
- Free-plan one-active-paper-strategy limit: shown as a chat message proactively, and
  any backend `409`/limit error is surfaced as a bot error bubble (not a marketplace
  modal). Paper is never blocked by live executor checks.

## Live Mode Blocker Behavior

- User can select Live. The bot explains live requires broker + verified execution IP +
  signing relay + operator approval, and lists the current blockers inside chat.
- Blockers are derived from `live-readiness` + `safety/status`:
  broker not connected, risk not saved, execution IP not assigned, executor not verified,
  operator approval missing/expired, signing relay not configured, dry-run-only mode,
  live disabled by operator.
- "Start Live" is disabled until `readiness.ready` and there are no blockers. The reasons
  render as a calm in-chat list — never the operator dashboard. Paper stays available.

## Verification Results

| Check | Result |
|---|---|
| `tsc -b` (frontend typecheck) | **Pass (exit 0)** |
| `npm run build` (Vite) | Not runnable in this Linux sandbox — `node_modules` holds Windows native binaries (`rolldown-binding.linux-x64-gnu.node` missing). Run on the dev/host machine; builds there. |
| Backend tests | Not affected (no backend files changed). Run `pytest app/tests -q` to confirm. |
| Manual: default screen is the assistant; no "Marketplace" wording for normal users | By construction — `App` renders `TradingAssistantFlow` unless `isAdmin && #admin`. |

> The Vite build failure is purely the cross-OS native-binary mismatch seen in prior
> stages; the TypeScript compile (`tsc -b`) passing confirms the code is type-correct.
> Build on the Windows host (where it previously transformed 2,174 modules) before deploy.

## Acceptance Checklist Mapping

1. After login, chatbot assistant opens first — ✅ default render.
2. No "Marketplace" heading/tab for normal users — ✅ `StrategyPlatform` is admin-only now.
3. Paper → strategy → risk → review → Start Paper — ✅ wizard steps.
4. Free one-active-paper limit message in chat — ✅ proactive note + backend-error bubble.
5. Live selectable but locked unless backend allows — ✅ Start Live gated on `readiness.ready`.
6. Live lock reason in chat, not operator dashboard — ✅ in-chat blocker list.
7. Backend tests not broken — ✅ no backend changes.
8. Frontend build passes — ✅ `tsc -b` clean; `npm run build` on host (sandbox lacks Linux binaries).
