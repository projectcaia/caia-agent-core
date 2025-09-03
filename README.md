# CaiaAgent Core Service (separate from n8n)

This service exposes the CaiaAgent API as a separate Railway service and proxies to n8n via webhooks.

## Endpoints
- `/` (redirects to N8N_UI_URL if set)
- `/health`, `/ping`, `/debug`
- `/agent/status`, `/agent/health`
- `/agent/orchestrate` (POST, Bearer) → forward to n8n webhook
- `/agent/proxy/n8n/{path}` (Bearer) → generic proxy

## Env Vars
- `N8N_WEBHOOK_BASE` (required) e.g. `https://caia-agent-production.up.railway.app`
- `CAIA_AGENT_KEY` (recommended; protects /agent/*)
- `N8N_UI_URL` (optional) → root redirect
- `DEFAULT_WEBHOOK_PATH` (optional) → message-only payload support
- `CAIA_PORT=8080`

## Deploy (Railway)
1) Create a new GitHub repo with these files.
2) Railway → New Project → Deploy from GitHub.
3) Set variables:
   - `N8N_WEBHOOK_BASE=https://<your-n8n>.up.railway.app`
   - `CAIA_AGENT_KEY=<random-long-token>`
   - `N8N_UI_URL=https://<your-n8n>.up.railway.app` (optional)
4) Redeploy.