# CaiaAgent Core Service (separate from n8n)

This service exposes the CaiaAgent API as a separate Railway service and proxies to n8n via webhooks.

## Endpoints
- `/` (redirects to N8N_UI_URL if set)
- `/health`, `/ping`, `/debug`
- `/agent/status`, `/agent/health`
- `/agent/orchestrate` (POST, Bearer) → forward to n8n webhook
- `/agent/proxy/n8n/{path}` (Bearer) → generic proxy

## Env Vars
- `N8N_WEBHOOK_BASE` (required) e.g. `https://caia-agent-production.up.railway.app/webhook`
- `CAIA_AGENT_KEY` (recommended; protects /agent/*)
- `N8N_UI_URL` (optional) → root redirect
- `DEFAULT_WEBHOOK_PATH` (optional) → default webhook when none provided
- `LOG_LEVEL` (optional)

## Deploy (Railway)
1. Create a new Railway project.
2. Upload or link this repo.
3. Set environment variables.
4. Deploy and check `/health`.
