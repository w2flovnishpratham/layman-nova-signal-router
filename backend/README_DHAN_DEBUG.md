# Dhan Diagnostics

Debug endpoints are disabled unless `DEBUG_ENABLED=true`.

Use diagnostics only for short setup windows, then turn them off again:

```env
DEBUG_ENABLED=true
```

Available diagnostic endpoint:

```text
GET /api/debug/dhan/config
```

It returns masked Dhan credential status, outgoing backend IP, safety flags, resolver settings, and the last interpreted Dhan error. Raw Dhan access tokens are never returned.

For production setup, prefer:

```text
GET /api/setup/status
```

That endpoint remains safe for the normal frontend and includes the backend outgoing IP needed for Dhan static IP whitelisting.
