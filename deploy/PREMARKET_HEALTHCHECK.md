# NOVA Pre-Market Healthcheck

This repo installs two VPS-side protections during `deploy/deploy_vps.sh`:

- `configure_hostinger_firewall.sh` configures the VPS OS firewall with `ufw`.
- `layman-premarket-healthcheck.timer` runs the backend healthcheck before market open.

The Hostinger panel firewall is provider-side. Code inside the VPS cannot
repair that layer if Hostinger loses or blocks provider firewall state. Keep the
provider firewall simple, or disable it and rely on `ufw` if Hostinger support
confirms the panel firewall is unstable.

## What The Timer Checks

The timer runs at 08:55 IST and 09:10 IST on weekdays. It verifies:

- local backend health: `http://127.0.0.1:8002/api/health`
- public nginx/API health: `BACKEND_PUBLIC_BASE_URL/api/health`
- every configured egress proxy exits from its expected public IP
- every egress proxy can reach Dhan's API host over HTTPS
- every active user egress assignment is re-verified and written to the DB

If an assigned egress fails, its `last_verified_at` is cleared. Existing live
gates then block real orders with `egress_not_verified` / `egress not verified`.

## Hostinger Commands

Check timer state:

```bash
systemctl status layman-premarket-healthcheck.timer --no-pager
systemctl list-timers layman-premarket-healthcheck.timer
```

Run the check manually:

```bash
cd /root/layman-nova-signal-router/backend
.venv/bin/python -m scripts.premarket_healthcheck
```

View logs:

```bash
journalctl -u layman-premarket-healthcheck.service -n 120 --no-pager
```

Re-apply VPS firewall rules:

```bash
/usr/local/sbin/layman-configure-hostinger-firewall
ufw status verbose
```

