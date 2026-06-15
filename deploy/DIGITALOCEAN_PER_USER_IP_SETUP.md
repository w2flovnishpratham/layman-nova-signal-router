# Two-User Dhan Egress Setup

The Hostinger VPS receives one TradingView alert, loads every active
`supertrend` subscription, and runs the order router once per user. Each live
Dhan HTTP client uses that user's authenticated CONNECT proxy, so TLS still
terminates at Dhan and the request exits from the user's whitelisted
DigitalOcean IP.

The user UI shows both available static IPs. The authenticated user selects one,
and the database uniqueness constraint prevents another user from selecting the
same IP. TradingView webhook details remain server-side.

## Node pool

| Droplet | Proxy ingress | Dhan whitelist/outbound |
|---|---|---|
| `nova-exec-user-001` | `64.225.87.19` | `165.232.184.177` |
| `nova-exec-user-002` | `152.42.157.165` | `167.71.232.232` |

Do not assign either IP to both users. Attach a DigitalOcean Reserved IP before
whitelisting if these are currently ordinary droplet addresses.

## 1. Install the proxy on each droplet

Run from this repo, replacing the Hostinger control-plane IP if it changes:

```bash
scp deploy/setup_dhan_egress_proxy.sh root@64.225.87.19:/root/
ssh root@64.225.87.19 \
  "bash /root/setup_dhan_egress_proxy.sh 187.127.153.128 8888"

scp deploy/setup_dhan_egress_proxy.sh root@152.42.157.165:/root/
ssh root@152.42.157.165 \
  "bash /root/setup_dhan_egress_proxy.sh 187.127.153.128 8888"
```

Each droplet generates unique proxy credentials and stores them in
`/root/.config/layman-egress-proxy.env` with mode `0600`.

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
EGRESS_NODES_JSON=[{"public_ip":"165.232.184.177","proxy_url":"http://<node-1-user>:<node-1-password>@64.225.87.19:8888"},{"public_ip":"167.71.232.232","proxy_url":"http://<node-2-user>:<node-2-password>@152.42.157.165:8888"}]
```

Restart the service with the safety gates still off.

## 3. Select IPs after both Google logins

Each person signs in, sees both static IPs in the chatbot, and selects one.
Selection immediately verifies that the observed proxy IP matches the selected
IP. The other user sees that IP as unavailable and selects the remaining node.

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
2. Confirm User 1 whitelisted `165.232.184.177` and User 2 whitelisted
   `167.71.232.232`.
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
