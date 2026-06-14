# Deploy Backend on the Production VPS

The canonical deployment procedure is
[PRODUCTION_RUNBOOK.md](PRODUCTION_RUNBOOK.md).

Production requirements:

- code: `/opt/layman-nova-signal-router`;
- environment: `/etc/layman/layman.env`, mode `0600`;
- service user: `layman`;
- runtime state/logs: `/var/lib/layman`;
- backend bind: `127.0.0.1:8002`;
- nginx TLS endpoint: `layman-api.manyacare.com`;
- `ENABLE_LIVE_ORDERS=false`;
- one web worker and no authenticated trading workers.

Do not create `backend/.env`, runtime directories, databases, or credential
files inside the checkout. Do not enable Live trading after a Paper dry run.
Live requires later signing-relay and verified executor/egress work.
