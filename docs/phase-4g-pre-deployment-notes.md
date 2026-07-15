# Phase 4G-pre — Deployment source of truth

This release (`phase-4g-pre-readiness-unification`, branched from
`3960bc3` on `phase-4f-representative-pine-qualification`) closes three
audited product gaps: unified strategy readiness, NOVA-managed credential
provisioning, and a real engine strategy picker.

## Deployment facts (as of this branch)

| Item | Value |
| --- | --- |
| Feature checkpoint commit | `3960bc3ac2cb6c6dbc0c78ae0926227c53d4b7d3` |
| This branch | `phase-4g-pre-readiness-unification` |
| Database migration head | `0013_manual_tradingview_flow` (no new migration in this branch) |
| Frontend (Vercel) source | Configured to build from `main` |
| Backend (systemd pull+restart) source | Feature branch on the trading host |

## Important provenance caveat

The public deployment's frontend bundle contains the full user-submitted
Pine workflow (imported-Pine page, managed-setup queue, conversion package,
admin review), but **`origin/main` does not contain the strategy-integration
work** — the merge-base between `main` and the checkpoint predates it. The
live deployment is therefore **pinned to a feature checkpoint**, not to
`main`.

**Do not treat `main` as representing the deployed product.**

## Recommendation (future branch reconciliation — not part of this task)

1. Decide the single release branch (e.g. fast-forward `main` to the
   qualified feature checkpoint once Phase 4G passes).
2. Point both Vercel and the backend host at that same branch/commit so the
   deployed artifact is reproducible from the release branch.
3. Record the deployed commit SHA in the release process so `/api/health`
   and `git rev-parse HEAD` on the host agree.

This task does **not** merge to `main` or change any deployment
configuration.

## Runtime flag verification (Phase 4G controlled paper test)

`/api/health` now reports the private-webhook execution flags. For the
controlled TradingView → PaperBroker test the deployed backend should show:

```
"private_webhook_execution_enabled": true
"live_orders_enabled": false
"private_webhook_live_execution_enabled": false
"dhan_mode": "MOCK"
```

Verify by curling `https://<backend-host>/api/health`. Do not enable live
execution or AI conversion.
