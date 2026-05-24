# Dhan Connection Guide

## Getting Your Dhan Credentials

1. Log in at https://dhan.co
2. Go to **My Profile** → **API Access** (or Dhan developer portal)
3. Generate an **Access Token**
4. Note your **Client ID** (visible in profile)

## Connecting in NOVA Signal Router

1. Log in to NOVA Signal Router dashboard
2. Go to **Connect Dhan** page
3. Enter your **Client ID** and **Access Token**
4. Click **Save Connection**
5. Click **Test Connection** to verify the token is valid

## Security Notes

- Your access token is encrypted using Fernet symmetric encryption immediately upon submission
- The raw token is never stored in the database
- The raw token is never transmitted back to the frontend
- The token is only decrypted in memory when placing orders
- You can **Disconnect** at any time to revoke access

## Token Expiry

Dhan access tokens generated from Dhan Web are valid for 24 hours. When a token expires:
- The system marks `is_token_valid = false`
- Orders will be BLOCKED with reason "Dhan token invalid"
- Reconnect from the Connect Dhan page with a new token

## Mock Mode vs Real Mode

| Mode | What happens               |
|------|----------------------------|
| MOCK | Orders are simulated, no real API calls to Dhan |
| REAL | Real orders are placed in your Dhan account |

Default: **MOCK mode**. Switch to REAL only when ready for live trading.

## Troubleshooting

- "Token validation failed" → Generate a new access token from Dhan portal
- "No Dhan account connected" → Complete the Connect Dhan flow first
- "Order rejected" → Check Dhan account for sufficient balance and permissions
