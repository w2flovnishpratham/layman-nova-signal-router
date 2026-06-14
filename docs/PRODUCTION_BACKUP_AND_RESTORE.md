# Production Backup and Restore

This procedure covers the PostgreSQL database used by the authenticated
multi-user Paper beta. It does not make Live trading ready.

## Backup Location and Schedule

- Script: `/usr/local/sbin/layman-backup-postgres`
- Environment: `/etc/layman/layman.env`
- Backup directory: `/var/backups/layman/postgres`
- Format: compressed PostgreSQL custom archive
- Retention: latest 14 backups by default
- Schedule: `layman-postgres-backup.timer`, daily at approximately 02:15 UTC
- File permissions: directory `0700`, backup and checksum files `0600`

Check the timer:

```bash
sudo systemctl status layman-postgres-backup.timer
sudo systemctl list-timers layman-postgres-backup.timer
```

Run and verify a backup:

```bash
sudo systemctl start layman-postgres-backup.service
sudo systemctl status layman-postgres-backup.service
sudo -u layman sha256sum -c /var/backups/layman/postgres/layman-postgres-YYYYMMDDTHHMMSSZ.dump.sha256
```

The script reads `DATABASE_URL` without printing it. Do not run with shell
tracing enabled.

## Off-Host Copy

Local VPS backups do not protect against total VPS loss. Copy encrypted
backups to access-controlled object storage or another host. Do not put them
in the repository, OneDrive project folder, or a public bucket.

## Restore Drill

Restore into a separate drill database first:

```bash
sudo -u postgres createdb layman_restore_drill
sudo -u layman pg_restore \
  --exit-on-error \
  --no-owner \
  --dbname=postgresql://layman@127.0.0.1:5432/layman_restore_drill \
  /var/backups/layman/postgres/layman-postgres-YYYYMMDDTHHMMSSZ.dump
```

Validate:

```bash
sudo -u postgres psql -d layman_restore_drill -c '\dt'
sudo -u postgres psql -d layman_restore_drill -c 'select count(*) from alembic_version;'
```

Destroy the drill database after validation:

```bash
sudo -u postgres dropdb layman_restore_drill
```

## Production Restore

1. Keep `ENABLE_LIVE_ORDERS=false`.
2. Stop the backend:

   ```bash
   sudo systemctl stop layman-nova-signal-router
   ```

3. Take a final backup if the database is reachable.
4. Restore into a new database rather than overwriting the damaged database.
5. Update `DATABASE_URL` in `/etc/layman/layman.env`.
6. Run migrations:

   ```bash
   cd /opt/layman-nova-signal-router/backend
   sudo -u layman env LAYMAN_ENV_FILE=/etc/layman/layman.env .venv/bin/alembic upgrade head
   ```

7. Start and verify:

   ```bash
   sudo systemctl start layman-nova-signal-router
   curl --fail https://layman-api.manyacare.com/api/readiness
   ```

Document the drill date, archive checksum, restore duration, row-count
validation, and operator.
