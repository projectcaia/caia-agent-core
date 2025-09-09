import os
import json
import asyncio
from typing import Any, Dict, List, Optional, Tuple

import aiohttp


class N8NError(RuntimeError):
    def __init__(self, status: int, body: Any):
        super().__init__(f"n8n HTTP {status}: {body}")
        self.status = status
        self.body = body


# ─────────────────────────────────────────────────────────────────────────────
# Low-level REST client
# ─────────────────────────────────────────────────────────────────────────────
class N8NClient:
    """
    Small wrapper for n8n REST API.
    - Base URL: N8N_API_URL (no trailing slash)
    - Authorization: Bearer N8N_API_KEY
    """

    def __init__(self, base_url: str, api_key: str, session: Optional[aiohttp.ClientSession] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._session = session

    # ---- internal HTTP ----
    def _headers(self) -> Dict[str, str]:
        # n8n Public API는 X-N8N-API-KEY 헤더를 기본으로 사용
        return {
            "X-N8N-API-KEY": self.api_key,
            # 호환성 위해 Authorization도 함께 전송(없어도 됨)
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def _request(self, method: str, path: str, params: Dict[str, Any] = None, json_body: Any = None):
        await self._ensure_session()
        url = f"{self.base_url}{path}"
        async with self._session.request(method, url, headers=self._headers(), params=params, json=json_body) as r:
            if r.status >= 200 and r.status < 300:
                # Some endpoints return empty body
                if r.content_type and "application/json" in r.content_type:
                    return await r.json()
                text = await r.text()
                try:
                    return json.loads(text)
                except Exception:
                    return {"ok": True, "raw": text}
            else:
                try:
                    body = await r.json()
                except Exception:
                    body = await r.text()
                raise N8NError(r.status, body)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ---- credentials ----
    async def list_credentials(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/rest/credentials")

    async def get_credential_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        creds = await self.list_credentials()
        for c in creds:
            if c.get("name") == name:
                return c
        return None

    # ---- workflows ----
    async def list_workflows(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/rest/workflows")

    async def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/rest/workflows/{workflow_id}")

    async def create_workflow(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        # n8n requires { name, nodes, connections, active, settings? }
        return await self._request("POST", "/rest/workflows", json_body=spec)

    async def update_workflow(self, workflow_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("PATCH", f"/rest/workflows/{workflow_id}", json_body=spec)

    async def delete_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return await self._request("DELETE", f"/rest/workflows/{workflow_id}")

    async def activate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return await self._request("POST", f"/rest/workflows/{workflow_id}/activate")

    async def deactivate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return await self._request("POST", f"/rest/workflows/{workflow_id}/deactivate")

    # ---- execution ----
    async def run_workflow_once(self, workflow_id: str, run_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Some n8n versions support POST /rest/workflows/{id}/run for a 1-off execution.
        If not supported, n8n returns 404 or 405 → we surface the hint in error.
        """
        run_data = run_data or {}
        try:
            return await self._request("POST", f"/rest/workflows/{workflow_id}/run", json_body=run_data)
        except N8NError as e:
            # Provide a helpful hint
            if e.status in (404, 405):
                return {"ok": False, "hint": "This n8n version may not support /run; use UI 'Execute Workflow' instead.", "error": e.body}
            raise

    async def list_executions(self, workflow_id: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if workflow_id:
            params["workflowId"] = workflow_id
        return await self._request("GET", "/rest/executions", params=params)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: credentials resolution
# ─────────────────────────────────────────────────────────────────────────────
class CredentialResolver:
    """
    Resolve n8n credential "names" (from env) to the structure n8n expects on nodes:
    e.g.
      "credentials": {
        "telegramApi": { "id": "<uuid>", "name": "Telegram account 2" }
      }
    """
    def __init__(self, client: N8NClient):
        self.client = client

    async def resolve(self, type_key: str, cred_name: str) -> Dict[str, Dict[str, str]]:
        """
        type_key: the key used in node.credentials (e.g., "telegramApi", "gmailOAuth2", "openAiApi", "googleCalendarOAuth2Api")
        cred_name: the DISPLAY name in n8n UI (e.g., "Telegram account 2")
        """
        if not cred_name:
            raise ValueError(f"Missing credential name for {type_key}")
        cred = await self.client.get_credential_by_name(cred_name)
        if not cred:
            raise RuntimeError(f"Credential '{cred_name}' not found in n8n")
        return {type_key: {"id": cred.get("id"), "name": cred.get("name")}}


# ─────────────────────────────────────────────────────────────────────────────
# Templates (spec builders)
# ─────────────────────────────────────────────────────────────────────────────
def _base_spec(name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "nodes": [],
        "connections": {},
        "active": False,
        "settings": {"executionOrder": "v1"},
    }


async def build_wf_mail_digest(client: N8NClient) -> Dict[str, Any]:
    """
    Daily Gmail digest → summarize with OpenAI → send to Telegram.
    Uses:
      - N8N_CRED_GMAIL          (Gmail account)
      - N8N_CRED_OPENAI         (OpenAI)
      - N8N_CRED_TELEGRAM       (Telegram)
      - N8N_TG_CHAT_ID          (destination chat id)
    """
    cred = CredentialResolver(client)
    gmail_name = os.getenv("N8N_CRED_GMAIL", "Gmail account")
    openai_name = os.getenv("N8N_CRED_OPENAI", "OpenAi account 2")
    telegram_name = os.getenv("N8N_CRED_TELEGRAM", "Telegram account 2")
    chat_id = os.getenv("N8N_TG_CHAT_ID", "")

    gmail_cred = await cred.resolve("gmailOAuth2", gmail_name)
    openai_cred = await cred.resolve("openAiApi", openai_name)
    telegram_cred = await cred.resolve("telegramApi", telegram_name)

    spec = _base_spec("caia-managed/mail-digest/prod")
    nodes = []
    conns = {}

    # 1) Schedule Trigger (09:00 every day)
    schedule = {
        "id": "schedule-0900",
        "name": "Schedule 09:00",
        "type": "n8n-nodes-base.cron",
        "typeVersion": 2,
        "position": [300, 300],
        "parameters": {
            "triggerTimes": [{"hour": 9, "minute": 0}],
        },
    }

    # 2) Gmail → get recent emails
    gmail_node = {
        "id": "gmail-get",
        "name": "Get Emails",
        "type": "n8n-nodes-base.gmail",
        "typeVersion": 3,
        "position": [560, 300],
        "parameters": {
            "operation": "getMany",
            "filters": {
                # 24h window + skip promotions/spam
                "q": "newer_than:1d -category:promotions -in:spam",
                "maxResults": 30,
            },
        },
        "credentials": gmail_cred,
    }

    # 3) OpenAI summarize
    openai_node = {
        "id": "openai-sum",
        "name": "Summarize",
        "type": "@n8n/n8n-nodes-langchain.lmChatOpenAi",
        "typeVersion": 1.2,
        "position": [820, 300],
        "parameters": {
            "model": {"__rl": True, "value": "gpt-4o-mini", "mode": "list"},
            "options": {
                "temperature": 0.2,
                "maxTokens": 800,
                "systemMessage": (
                    "You are a helpful assistant summarizing emails into a concise Korean daily digest.\n"
                    "- Keep it under 12 bullet points.\n"
                    "- Group by topic if possible.\n"
                    "- Include sender and subject when meaningful.\n"
                ),
            },
            "promptType": "define",
            "text": "={{ ($json || []).map(e => (e.payload?.headers || []).find(h => h.name==='Subject')?.value || 'No Subject').join('\\n') }}",
        },
        "credentials": openai_cred,
    }

    # 4) Telegram send
    tg_send = {
        "id": "tg-send",
        "name": "Send to Telegram",
        "type": "n8n-nodes-base.telegram",
        "typeVersion": 1.2,
        "position": [1080, 300],
        "parameters": {
            "chatId": chat_id,
            "text": "📬 *Daily Mail Digest*\\n\\n{{ $json.text || 'No summary' }}",
            "additionalFields": {"parseMode": "Markdown"},
        },
        "credentials": telegram_cred,
    }

    nodes += [schedule, gmail_node, openai_node, tg_send]
    conns["Schedule 09:00"] = {"main": [[{"node": "Get Emails", "type": "main", "index": 0}]]}
    conns["Get Emails"] = {"main": [[{"node": "Summarize", "type": "main", "index": 0}]]}
    conns["Summarize"] = {"main": [[{"node": "Send to Telegram", "type": "main", "index": 0}]]}

    # Fix names in connection keys
    spec["nodes"] = nodes
    spec["connections"] = {
        "Schedule 09:00": {"main": [[{"node": "Get Emails", "type": "main", "index": 0}]]},
        "Get Emails": {"main": [[{"node": "Summarize", "type": "main", "index": 0}]]},
        "Summarize": {"main": [[{"node": "Send to Telegram", "type": "main", "index": 0}]]},
    }
    return spec


async def build_wf_tg_to_gmail(
    client: N8NClient,
    tg_chat_whitelist: List[str],
    mail_to: str,
) -> Dict[str, Any]:
    """
    Telegram → Gmail forward.
    Uses:
      - N8N_CRED_TELEGRAM
      - N8N_CRED_GMAIL
      - N8N_FORWARD_TO (mail_to)
    """
    cred = CredentialResolver(client)
    telegram_name = os.getenv("N8N_CRED_TELEGRAM", "Telegram account 2")
    gmail_name = os.getenv("N8N_CRED_GMAIL", "Gmail account")

    telegram_cred = await cred.resolve("telegramApi", telegram_name)
    gmail_cred = await cred.resolve("gmailOAuth2", gmail_name)

    spec = _base_spec("caia-managed/tg-forward/prod")
    nodes = []
    conns = {}

    # 1) Telegram Trigger (whitelist)
    tg_trigger = {
        "id": "tg-trigger",
        "name": "Telegram Trigger",
        "type": "n8n-nodes-base.telegramTrigger",
        "typeVersion": 1.1,
        "position": [300, 300],
        "parameters": {"updates": ["message"], "additionalFields": {"userIds": ",".join(tg_chat_whitelist or [])}},
        "credentials": telegram_cred,
    }

    # 2) Gmail Send
    gmail_send = {
        "id": "gmail-send",
        "name": "Forward to Gmail",
        "type": "n8n-nodes-base.gmail",
        "typeVersion": 3,
        "position": [560, 300],
        "parameters": {
            "operation": "send",
            "toList": mail_to,
            "subject": "Telegram → Gmail",
            "message": "={{$json.message?.text || 'No text'}}",
        },
        "credentials": gmail_cred,
    }

    # 3) Telegram Ack
    tg_ack = {
        "id": "tg-ack",
        "name": "Reply Ack",
        "type": "n8n-nodes-base.telegram",
        "typeVersion": 1.2,
        "position": [820, 300],
        "parameters": {"chatId": "={{$json.message.chat.id}}", "text": "✉️ Forwarded to Gmail"},
        "credentials": telegram_cred,
    }

    nodes += [tg_trigger, gmail_send, tg_ack]
    conns["Telegram Trigger"] = {"main": [[{"node": "Forward to Gmail", "type": "main", "index": 0}]]}
    conns["Forward to Gmail"] = {"main": [[{"node": "Reply Ack", "type": "main", "index": 0}]]}

    spec["nodes"] = nodes
    spec["connections"] = conns
    return spec


async def build_wf_failure_guard(client: N8NClient) -> Dict[str, Any]:
    """
    Failure guard (placeholder for policy):
    - Manual trigger → checks recent executions of target workflow → if 3 consecutive errors, deactivate it.
    Requires:
      - ENV: TARGET_WORKFLOW_ID (optionally)
      - N8N_CRED_TELEGRAM for notify (optional)
    """
    telegram_name = os.getenv("N8N_CRED_TELEGRAM", "Telegram account 2")
    chat_id = os.getenv("N8N_TG_CHAT_ID", "")
    target_id = os.getenv("TARGET_WORKFLOW_ID", "")

    spec = _base_spec("caia-managed/failure-guard/prod")
    nodes = []
    conns = {}

    # Manual Trigger
    manual = {
        "id": "manual",
        "name": "Manual Trigger",
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [300, 300],
        "parameters": {},
    }

    # Function: checks last 5 executions and decides
    func = {
        "id": "check",
        "name": "Check Executions",
        "type": "n8n-nodes-base.function",
        "typeVersion": 2,
        "position": [560, 300],
        "parameters": {
            "functionCode": (
                "const wfId = $json.TARGET_WORKFLOW_ID || $env.TARGET_WORKFLOW_ID || '';\n"
                "if (!wfId) { return [{ decision: 'skip', reason: 'no target' }]; }\n"
                "return [{ decision: 'inspect', workflowId: wfId }];"
            )
        },
    }

    # HTTP Request → n8n /rest/executions
    http = {
        "id": "http-exe",
        "name": "List Executions",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.1,
        "position": [820, 300],
        "parameters": {
            "method": "GET",
            "url": f"{os.getenv('N8N_API_URL','').rstrip('/')}/rest/executions",
            "sendQuery": True,
            "queryParameters": {"parameters": [{"name": "workflowId", "value": "={{$json.workflowId}}"}, {"name": "limit", "value": "5"}]},
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "Authorization", "value": "Bearer {{$env.N8N_API_KEY}}"}]},
        },
    }

    # Function → detect 3 consecutive errors
    func2 = {
        "id": "eval",
        "name": "Eval Errors",
        "type": "n8n-nodes-base.function",
        "typeVersion": 2,
        "position": [1080, 300],
        "parameters": {
            "functionCode": (
                "const items = items[0].json.data || items[0].json.items || [];\n"
                "let c=0; for (const it of items) { if ((it.status||it.finished||it.error) && (it.status==='error' || it.error)) c++; else break; }\n"
                "if (c>=3) { return [{ action: 'deactivate' }]; } else { return [{ action: 'noop', count: c }]; }"
            )
        },
    }

    # Deactivate request
    http_deact = {
        "id": "http-deact",
        "name": "Deactivate Workflow",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.1,
        "position": [1340, 240],
        "parameters": {
            "method": "POST",
            "url": f"{os.getenv('N8N_API_URL','').rstrip('/')}/rest/workflows/={{$json.workflowId}}/deactivate",
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "Authorization", "value": "Bearer {{$env.N8N_API_KEY}}"}]},
        },
    }

    # Telegram notify (optional)
    telegram_name_env = os.getenv("N8N_CRED_TELEGRAM", "")
    telegram_cred = None
    if telegram_name_env:
        cred = CredentialResolver(client)
        telegram_cred = await cred.resolve("telegramApi", telegram_name_env)

    tg_notify = {
        "id": "tg-notify",
        "name": "Notify",
        "type": "n8n-nodes-base.telegram",
        "typeVersion": 1.2,
        "position": [1340, 360],
        "parameters": {"chatId": chat_id, "text": "🛑 FailureGuard: took action {{ $json.action || 'noop' }}"},
        "credentials": telegram_cred or {},
    }

    nodes += [manual, func, http, func2, http_deact, tg_notify]
    conns["Manual Trigger"] = {"main": [[{"node": "Check Executions", "type": "main", "index": 0}]]}
    conns["Check Executions"] = {"main": [[{"node": "List Executions", "type": "main", "index": 0}]]}
    conns["List Executions"] = {"main": [[{"node": "Eval Errors", "type": "main", "index": 0}]]}
    conns["Eval Errors"] = {
        "main": [
            [{"node": "Deactivate Workflow", "type": "main", "index": 0}],
            [{"node": "Notify", "type": "main", "index": 0}],
        ]
    }

    spec["nodes"] = nodes
    spec["connections"] = conns
    return spec


async def build_wf_health_heartbeat(client: N8NClient, report_chat_id: str) -> Dict[str, Any]:
    """
    Heartbeat: 08:55 daily → ping Core /health → send to Telegram.
    Uses:
      - N8N_CRED_TELEGRAM
      - CAIA_AGENT_HEALTH_URL
    """
    cred = CredentialResolver(client)
    telegram_name = os.getenv("N8N_CRED_TELEGRAM", "Telegram account 2")
    telegram_cred = await cred.resolve("telegramApi", telegram_name)

    health_url = os.getenv("CAIA_AGENT_HEALTH_URL", "https://caia-agent-core-production.up.railway.app/health")

    spec = _base_spec("caia-managed/heartbeat/prod")
    nodes = []
    conns = {}

    schedule = {
        "id": "hb-schedule",
        "name": "Schedule 08:55",
        "type": "n8n-nodes-base.cron",
        "typeVersion": 2,
        "position": [300, 300],
        "parameters": {"triggerTimes": [{"hour": 8, "minute": 55}]},
    }

    http = {
        "id": "hb-http",
        "name": "Check Core Health",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.1,
        "position": [560, 300],
        "parameters": {"method": "GET", "url": health_url},
    }

    tg = {
        "id": "hb-tg",
        "name": "Report",
        "type": "n8n-nodes-base.telegram",
        "typeVersion": 1.2,
        "position": [820, 300],
        "parameters": {"chatId": report_chat_id, "text": "🫀 Heartbeat: {{ $json.ok ? 'OK' : 'FAIL' }} @ {{ $json.timestamp || $now }}"},
        "credentials": telegram_cred,
    }

    nodes += [schedule, http, tg]
    conns["Schedule 08:55"] = {"main": [[{"node": "Check Core Health", "type": "main", "index": 0}]]}
    conns["Check Core Health"] = {"main": [[{"node": "Report", "type": "main", "index": 0}]]}

    spec["nodes"] = nodes
    spec["connections"] = {
        "Schedule 08:55": {"main": [[{"node": "Check Core Health", "type": "main", "index": 0}]]},
        "Check Core Health": {"main": [[{"node": "Report", "type": "main", "index": 0}]]},
    }
    return spec


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator for automated deploy/test/activate
# ─────────────────────────────────────────────────────────────────────────────
class N8NAutomation:
    """
    High-level automation helpers.
    """

    def __init__(self, client: N8NClient):
        self.client = client

    async def deploy_spec(self, spec: Dict[str, Any], test: bool = False) -> Tuple[str, Dict[str, Any]]:
        """Create workflow from spec, optionally run a test."""
        created = await self.client.create_workflow(spec)
        wf_id = str(created.get("id") or created.get("_id") or created.get("data", {}).get("id") or "")
        if not wf_id:
            raise RuntimeError(f"Cannot determine workflow id from response: {created}")
        test_result: Dict[str, Any] = {}
        if test:
            try:
                test_result = await self.client.run_workflow_once(wf_id, run_data={})
            except N8NError as e:
                test_result = {"ok": False, "error": {"status": e.status, "body": e.body}}
        return wf_id, test_result

    async def activate(self, workflow_id: str) -> Dict[str, Any]:
        return await self.client.activate_workflow(workflow_id)

    async def deactivate(self, workflow_id: str) -> Dict[str, Any]:
        return await self.client.deactivate_workflow(workflow_id)

    async def destroy(self, workflow_id: str) -> Dict[str, Any]:
        """Deactivate then delete."""
        try:
            await self.client.deactivate_workflow(workflow_id)
        except Exception:
            pass
        return await self.client.delete_workflow(workflow_id)


# ─────────────────────────────────────────────────────────────────────────────
# CLI (optional quick manual test)
#   python caia_n8n_client.py bootstrap
#   python caia_n8n_client.py list
# ─────────────────────────────────────────────────────────────────────────────
async def _cmd_bootstrap():
    base = os.getenv("N8N_API_URL", "").rstrip("/")
    key = os.getenv("N8N_API_KEY", "")
    if not base or not key:
        raise SystemExit("Missing N8N_API_URL / N8N_API_KEY")

    client = N8NClient(base, key)
    auto = N8NAutomation(client)

    wf1 = await build_wf_mail_digest(client)
    wf1_id, wf1_test = await auto.deploy_spec(wf1, test=True)

    wf2 = await build_wf_tg_to_gmail(
        client,
        tg_chat_whitelist=[os.getenv("N8N_TG_CHAT_ID", "")],
        mail_to=os.getenv("N8N_FORWARD_TO", ""),
    )
    wf2_id, wf2_test = await auto.deploy_spec(wf2, test=False)

    wf3 = await build_wf_failure_guard(client)
    wf3_id, wf3_test = await auto.deploy_spec(wf3, test=False)

    wf4 = await build_wf_health_heartbeat(client, report_chat_id=os.getenv("N8N_TG_CHAT_ID", ""))
    wf4_id, wf4_test = await auto.deploy_spec(wf4, test=False)

    out = {
        "workflows": [
            {"id": wf1_id, "name": "wf-mail-digest-v1", "test": wf1_test},
            {"id": wf2_id, "name": "wf-tg-to-gmail-v1", "test": wf2_test},
            {"id": wf3_id, "name": "wf-failure-guard-v1", "test": wf3_test},
            {"id": wf4_id, "name": "wf-health-heartbeat-v1", "test": wf4_test},
        ]
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    await client.close()


async def _cmd_list():
    base = os.getenv("N8N_API_URL", "").rstrip("/")
    key = os.getenv("N8N_API_KEY", "")
    if not base or not key:
        raise SystemExit("Missing N8N_API_URL / N8N_API_KEY")
    client = N8NClient(base, key)
    wfs = await client.list_workflows()
    print(json.dumps(wfs, ensure_ascii=False, indent=2))
    await client.close()


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "bootstrap":
        asyncio.run(_cmd_bootstrap())
    elif cmd == "list":
        asyncio.run(_cmd_list())
    else:
        print("Usage:")
        print("  python caia_n8n_client.py bootstrap   # create 4 standard workflows")
        print("  python caia_n8n_client.py list        # list workflows")
