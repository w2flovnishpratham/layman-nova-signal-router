# Layman NOVA Executor Deployment

This package deploys **only** the executor service (`app.executor_service.main:app`)
to a DigitalOcean droplet. Each droplet is one user's dedicated egress: its
DigitalOcean Reserved IP is whitelisted in that user's Dhan account, and all of
that user's real order placement leaves from this IP.

The executor does **not** run the frontend, the main Nova API, database
migrations, the paper worker, the live-pilot worker, or any admin UI.

## Topology

```
Hostinger main app + live-pilot worker  ──signed HTTPS──▶  Executor droplet ──▶ Dhan
Neon PostgreSQL (shared)                                   (Reserved IP = whitelisted IP)

EXECUTOR_001  nova-exec-user-001  64.225.87.19   -> Dhan Account 1 whitelist 64.225.87.19
EXECUTOR_002  nova-exec-user-002  152.42.157.165 -> Dhan Account 2 whitelist 152.42.157.165
```

## Endpoints (the only ones exposed)

- `GET /health` — returns `{"status":"ok","executor_code":...}`.
- `GET /egress-ip` — returns the droplet's observed public egress IP.
- `POST /execute-order` — signed, single-use order relay (dry-run or real).

## Files

| File | Purpose |
|---|---|
| `install_executor.sh` | Idempotent installer: non-root user, venv, env file, systemd unit. |
| `configure_executor_env.sh` | Safely write `/etc/layman-executor/executor.env` (mode 0600). |
| `executor.env.example` | Template environment (placeholders only). |
| `layman-executor.service` | Hardened systemd unit (non-root, locked-down filesystem). |
| `nginx-executor.conf` | TLS termination, three-path allowlist, Hostinger-IP allow, rate limit. |
| `check_executor_health.sh` | Verify `/health` and executor code. |
| `check_executor_egress.sh` | Verify `/egress-ip` equals the reserved IP. |
| `check_reserved_ip_route.sh` | Verify the droplet's ACTUAL outbound IP equals the reserved IP. |

## Install (per droplet)

```bash
# 1. Get the code onto the droplet at /opt/layman-executor (git clone or rsync;
#    exclude node_modules/dist/.git/runtime per deploy/deploy-excludes.txt).
sudo bash /opt/layman-executor/backend/deploy/executor/install_executor.sh

# 2. Configure identity (executor code, reserved IP, Hostinger IP).
sudo bash /opt/layman-executor/backend/deploy/executor/configure_executor_env.sh \
  EXECUTOR_001 64.225.87.19 <HOSTINGER_PUBLIC_IP>

# 3. By hand, set the strong shared secret and DATABASE_URL in
#    /etc/layman-executor/executor.env (mode 0600). Leave EXECUTOR_REAL_ORDERS_ENABLED=false.
sudo openssl rand -hex 32   # paste into EXECUTOR_SHARED_SECRET

# 4. Start and verify.
sudo systemctl restart layman-executor.service
bash /opt/layman-executor/backend/deploy/executor/check_executor_health.sh
bash /opt/layman-executor/backend/deploy/executor/check_reserved_ip_route.sh
```

The service **fails to start** if the executor code is empty, the shared secret is
weaker than 32 characters, the reserved IP is missing, or real orders are enabled
without the production prerequisites.

## TLS / nginx

Use a per-droplet hostname (e.g. `exec-001.example.com`, `exec-002.example.com`)
with Let's Encrypt, or terminate TLS on the Reserved IP directly if no domain is
available yet. Copy `nginx-executor.conf`, replace the server name, certificate
paths, and `HOSTINGER_PUBLIC_IP`, then reload nginx. Only `/health`, `/egress-ip`,
and `/execute-order` are proxied; all other paths return 404.

## DigitalOcean cloud firewall (per executor)

Configure these rules in the DigitalOcean firewall attached to each droplet, in
addition to the nginx allowlist (defence in depth).

Inbound:

```
SSH    22/tcp   -> source: admin IP only
HTTPS 443/tcp   -> source: Hostinger main server IP only
```

Outbound:

```
HTTPS 443/tcp   -> Dhan API (api.dhan.co)
HTTPS 443/tcp   -> egress IP-check endpoint (api.ipify.org or equivalent)
HTTPS 443/tcp + DNS 53 -> apt/package mirrors during maintenance windows only
PostgreSQL 5432/tcp -> Neon database host (if the executor uses Neon for nonce receipts)
```

Do **not** expose the executor to the public internet. There is no path that
should accept traffic from anywhere other than the Hostinger main server (plus
admin SSH).

## Reserved IP verification

`check_reserved_ip_route.sh` queries an external IP-echo service directly (not the
executor service) and fails if the droplet's actual outbound IP differs from the
configured reserved IP. **No order may be placed unless this passes** — a mismatch
means orders would leave from a non-whitelisted IP and Dhan would reject them (or,
worse, the whitelist guarantee would be void).

## Real orders

`EXECUTOR_REAL_ORDERS_ENABLED` stays `false` by default. The executor still
fails closed even when the flag is true unless a fully-formed, signed, real
request with short-lived credentials arrives. Enabling real mode is a deliberate,
temporary step in `docs/CONTROLLED_REAL_LIVE_PILOT_RUNBOOK.md`; disable it again
immediately after the controlled pilot order.
