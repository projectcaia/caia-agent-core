# CaiaAgent Core Service

This is the updated CaiaAgent Core API with /agent/report endpoint for n8n integration.

## Endpoints
- `/` (redirects to N8N_UI_URL if set)
- `/health`, `/ping`, `/debug`
- `/agent/status`, `/agent/health`
- `/agent/orchestrate` (POST, Bearer) → forward to n8n webhook
- `/agent/report` (POST, Bearer) → receive reports from n8n
- `/agent/proxy/n8n/{path}` (Bearer) → generic proxy
