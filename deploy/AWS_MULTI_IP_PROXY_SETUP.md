# AWS Multi-IP Proxy Setup

Nova live order routing uses one AWS EC2 proxy host with multiple Squid listener
ports. Each port exits through a dedicated Elastic IP, so each live user gets one
stable public IP to whitelist in Dhan.

## Architecture

- Hostinger/backend calls the AWS proxy host.
- The AWS proxy host runs Squid on one port per slot.
- Each Squid port maps to one Elastic IP.
- Dhan sees the assigned Elastic IP, not the Hostinger/backend IP.
- One live user is assigned one Nova Static IP.
- Proxy credentials stay backend-only.
- The frontend shows only the public IP the user must whitelist in Dhan.

## Slot Model

The app builds the five AWS proxy slots server-side from configuration. Do not
store or expose full credential-bearing proxy URLs in frontend state, API
responses, logs, screenshots, or docs.

Each slot has:

- shared AWS proxy host
- dedicated Squid port
- dedicated Elastic IP / expected egress IP
- dedicated proxy username
- backend-only password from env/secret storage

## Required Backend Env

Use secret storage for the password in production. Never commit a real proxy
password.

```env
AWS_PROXY_SLOTS_ENABLED=true
AWS_PROXY_HOST=13.203.58.220
AWS_PROXY_SHARED_PASSWORD=REPLACE_WITH_SECRET
```

Optional per-slot overrides:

```env
AWS_PROXY_SLOT_1_PASSWORD=
AWS_PROXY_SLOT_2_PASSWORD=
AWS_PROXY_SLOT_3_PASSWORD=
AWS_PROXY_SLOT_4_PASSWORD=
AWS_PROXY_SLOT_5_PASSWORD=
```

If a per-slot password is set, that slot uses it. Otherwise the slot falls back
to `AWS_PROXY_SHARED_PASSWORD`.

## Legacy Fallback

`EGRESS_NODES_JSON` is legacy manual egress JSON fallback only. It is not the
AWS proxy slot path and should not be used for new AWS deployments.

## Operational Rules

- Assign exactly one Nova Static IP to each live user.
- The user whitelists only their assigned public Elastic IP in Dhan.
- The backend stores encrypted proxy routing details.
- Paper mode must not require AWS egress.
- Live mode must fail closed if the assigned static IP is missing or unverified.
- Do not log proxy URLs, proxy passwords, encoded credentials, or Dhan tokens.
