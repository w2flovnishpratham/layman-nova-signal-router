# Isolated preview deployment plan — NOT EXECUTED

Prepared for the redesign branch. **Nothing in this document has been run.**
It executes only after the user says `DEPLOY ISOLATED PREVIEW`.

## What this preview is for

Visual review of the authenticated pages. Local smoke testing can only reach the
sign-in gate, so the redesigned `/app/*` screens have never been seen with real
data by anyone.

## What it must not touch

| Must stay untouched | Why |
| --- | --- |
| port 8000, `nova-signal-router.service` | production |
| port 8002, `layman-nova-signal-router.service` | frozen CAS staging at `45cf6d8` |
| the frozen staging runtime state | the pending Paper CAS acceptance depends on it |
| `main`, Vercel | not part of this work |

No existing service is restarted. The preview is additive: a new checkout, a new
unit, a new port, a new state directory.

## Proposal

| Item | Value |
| --- | --- |
| Checkout path | `/root/nova-preview-redesign` |
| Branch/SHA | `feat/nova-chatbot-and-landing-integration` at the reviewed SHA |
| Service name | `nova-preview-redesign.service` |
| Port | `127.0.0.1:8010` (loopback only, no public bind) |
| Environment file | `/etc/layman/nova-preview.env` (new file, not shared) |
| Runtime state | `/var/lib/nova-preview/state` (new tree, **not** `/var/lib/layman/state`) |
| Access | SSH tunnel only — see below |

### Access: SSH tunnel, not a new hostname

The Google OAuth client already authorises `http://localhost:8000` as a
JavaScript origin **and** `http://localhost:8000/api/auth/google/callback` as a
redirect URI. A new public hostname such as `layman-preview.manyacare.com` would
require editing that OAuth client before sign-in worked at all.

So the preview binds to loopback and is reached by:

```bash
ssh -N -L 8000:127.0.0.1:8010 -i ~/.ssh/id_ed25519 root@187.127.153.128
```

The reviewer then opens `http://localhost:8000`, which is already authorised.
This adds no public surface and requires no OAuth change.

If a public hostname is preferred later, it needs: a DNS record, a TLS
certificate, a reverse-proxy entry, **and** the user adding both the origin and
the callback URL to the Google OAuth client. That is a separate decision.

### Environment protections (all mandatory)

```
APP_ENV=isolated_preview
DHAN_MODE=MOCK
ENABLE_LIVE_ORDERS=false
PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED=false
WEBHOOK_TRADING_ENABLED=false
REQUIRE_MARKET_HOURS=true
RUNTIME_STATE_DIR=/var/lib/nova-preview/state
```

`WEBHOOK_TRADING_ENABLED=false` is stricter than frozen staging: the preview is
for looking at screens, so it should not accept an inbound alert at all.

### Database strategy

**Recommended: a separate Neon branch/database for the preview.**

The preview runs Alembic `0018`, which creates `user_preferences`. Pointing it at
the production database would run a migration against production data — not
acceptable for a visual preview. A Neon branch gives a real schema with real
shapes and no shared writes.

If a separate database is not available, the fallback is a local SQLite file at
`/var/lib/nova-preview/preview.db`. That is enough to render every page, but it
starts empty, so Reports and Signals will legitimately show their empty states.

**Not acceptable:** sharing the frozen staging database or its runtime state.

### Runtime-state isolation

`RUNTIME_STATE_DIR=/var/lib/nova-preview/state` gives the preview its own
`settings.json`, `paper_portfolio.json`, `paper_position.json` and
`daily_risk.json`. Frozen staging's Paper state under `/var/lib/layman/state`
stays byte-identical, which the pending CAS acceptance requires.

### Rollback / removal

```bash
systemctl stop nova-preview-redesign.service
systemctl disable nova-preview-redesign.service
rm /etc/systemd/system/nova-preview-redesign.service
systemctl daemon-reload
rm -rf /root/nova-preview-redesign
rm -rf /var/lib/nova-preview
rm /etc/layman/nova-preview.env
```

Nothing above touches ports 8000 or 8002, either existing unit, or any existing
state directory. Removal is complete and leaves no trace.

### Verification after deploying (when authorised)

1. `systemctl is-active nova-preview-redesign` → `active`
2. `curl -s 127.0.0.1:8010/api/health` → `app_env=isolated_preview`,
   `live_orders_enabled=false`, `dhan_mode=MOCK`
3. Confirm ports 8000 and 8002 unchanged, both units still `active`, `NRestarts`
   unchanged on both
4. Confirm frozen staging Paper state is unmodified (compare file mtimes)
5. Only then open the tunnel and review screens
