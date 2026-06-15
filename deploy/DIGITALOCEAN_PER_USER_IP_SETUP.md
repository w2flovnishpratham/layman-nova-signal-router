# Two-User Dhan Egress Setup

The browser UI is unchanged. The Hostinger VPS receives one TradingView alert,
loads every active `supertrend` subscription, and runs the existing order router
once per user. Each live Dhan HTTP client uses that user's authenticated CONNECT
proxy, so TLS still terminates at Dhan and the request exits from the user's
whitelisted DigitalOcean IP.

## Fixed routing

| User | Droplet | Dhan whitelist |
|---|---|---|
| User 1 | `nova-exec-user-001` | `64.225.87.19` |
| User 2 | `nova-exec-user-002` | `152.42.157.165` |

Do not assign either IP to both users. Attach a DigitalOcean Reserved IP before
whitelisting if these are currently ordinary droplet addresses.

## 1. Install the proxy on each droplet

Generate a different username/password for each droplet. Run from this repo,
replacing the Hostinger control-plane IP if it changes:

```bash
scp deploy/setup_dhan_egress_proxy.sh root@64.225.87.19:/root/
ssh root@64.225.87.19 \
  "bash /root/setup_dhan_egress_proxy.sh 187.127.153.128 8888 user001 '<strong-password-1>'"

scp deploy/setup_dhan_egress_proxy.sh root@152.42.157.165:/root/
ssh root@152.42.157.165 \
  "bash /root/setup_dhan_egress_proxy.sh 187.127.153.128 8888 user002 '<strong-password-2>'"
```

The firewall permits proxy traffic only from the Hostinger VPS. Dhan
credentials remain encrypted on the control plane and are sent only inside the
end-to-end HTTPS connection passing through the proxy.

## 2. Deploy schema and environment

Run on Hostinger:

```bash
cd ~/layman-nova-signal-router
git pull
cd backend
source .venv/bin/activate
python -m scripts.init_db
```

Required environment:

```dotenv
STRATEGY_WEBHOOK_SECRET=<long-random-secret-used-in-tradingview-json>
EXECUTION_NODE_ROUTING_ENABLED=false
ENABLE_LIVE_ORDERS=false
DHAN_READ_ONLY_REAL_DATA=true
```

Restart the service with the safety gates still off.

## 3. Assign users after both Google logins

Each person must sign in once so their email exists in `users`. On Hostinger:

```bash
export EGRESS_PROXY_URL='http://user001:<strong-password-1>@64.225.87.19:8888'
python -m scripts.assign_user_egress \
  --email '<user-1-google-email>' --public-ip 64.225.87.19 --verify

export EGRESS_PROXY_URL='http://user002:<strong-password-2>@152.42.157.165:8888'
python -m scripts.assign_user_egress \
  --email '<user-2-google-email>' --public-ip 152.42.157.165 --verify
unset EGRESS_PROXY_URL
```

Verification must report the same observed IP as the assigned IP.

## 4. TradingView alert

Use one alert URL:

```text
https://<backend-host>/api/webhook/strategy/supertrend
```

Use the existing NOVA JSON body and set:

```json
{
  "secret": "<STRATEGY_WEBHOOK_SECRET>",
  "signal_id": "{{ticker}}-{{time}}-{{strategy.order.action}}",
  "strategy_code": "TRADINGVIEW_NIFTY_V1",
  "action": "ENTRY",
  "side": "BUY",
  "symbol": "NIFTY",
  "qty": 1,
  "order_type": "MARKET",
  "product_type": "INTRADAY"
}
```

The backend replaces `qty` with each subscriber's selected lots multiplied by
the current NIFTY lot size.

## 5. Live arming sequence for June 16, 2026

1. Generate fresh Dhan access tokens on June 16; Dhan tokens expire after about
   24 hours.
2. Confirm User 1 whitelisted `64.225.87.19` and User 2 whitelisted
   `152.42.157.165`.
3. Both users complete the unchanged chatbot flow and select `supertrend`.
4. Verify both egress proxies again.
5. Keep all live gates off and have both users select **Paper** for one full
   fan-out test.
6. Set `EXECUTION_NODE_ROUTING_ENABLED=true`,
   `DHAN_READ_ONLY_REAL_DATA=false`, and `ENABLE_LIVE_ORDERS=true`.
7. During market hours, select **Live** and test the minimum one-lot order
   for one user at a time before enabling both.

There is deliberately no fallback to the Hostinger IP. A missing proxy,
disabled routing gate, missing credentials, or proxy failure blocks that user's
order.
