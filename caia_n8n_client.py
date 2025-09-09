import os
import json
import asyncio
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

# ─────────────────────────────────────────────────────────────────────────────
# 환경 변수
# ─────────────────────────────────────────────────────────────────────────────
N8N_API_URL = os.getenv("N8N_API_URL", "").rstrip("/")
N8N_API_KEY = os.getenv("N8N_API_KEY", "")

# n8n 내에서 미리 만들어둔 크레덴셜 "이름"
CREDENTIALS_MAP = {
    "telegram": os.getenv("N8N_CRED_TELEGRAM", "telegram_main"),
    "gmail": os.getenv("N8N_CRED_GMAIL", "gmail_primary"),
    "openai": os.getenv("N8N_CRED_OPENAI", "openai_prod"),
    "gdrive": os.getenv("N8N_CRED_GDRIVE", "gdrive_main"),  # 옵션
}

# 공통 라벨(네임스페이스 용도) — n8n 스키마에 공식 label 필드는 없으니 name/notes에 태깅
LABELS = {
    "project": "caia-ops",
    "owner": "caia",
    "stage": "draft",   # draft | active | suspended
    "system": "fgpt",
}

TZ = "Asia/Seoul"


# ─────────────────────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────────────────────
def _headers() -> Dict[str, str]:
    if not N8N_API_URL or not N8N_API_KEY:
        raise RuntimeError("N8N_API_URL / N8N_API_KEY 가 필요합니다.")
    return {
        "Authorization": f"Bearer {N8N_API_KEY}",
        "Content-Type": "application/json",
    }


def add_labels_to_name(base: str, extra: Optional[Dict[str, str]] = None) -> str:
    tags = {**LABELS, **(extra or {})}
    suffix = " ".join([f"[{k}:{v}]" for k, v in tags.items()])
    return f"{base} {suffix}"


# ─────────────────────────────────────────────────────────────────────────────
# n8n REST 클라이언트 (Self-hosted)
# 참고: n8n 버전에 따라 일부 엔드포인트가 다를 수 있음.
# - 워크플로우 CRUD:       POST/GET/PUT   /rest/workflows
# - 활성/비활성:           POST           /rest/workflows/{id}/activate|deactivate
# - 수동 실행(테스트):      POST           /rest/workflows/{id}/run  (버전에 따라 다름)
# - 실행 이력 조회:        GET            /rest/executions?workflowId=...
# - 크레덴셜 목록:         GET            /rest/credentials
# ─────────────────────────────────────────────────────────────────────────────
class N8NClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def _req(self, method: str, path: str, **kwargs) -> Any:
        async with aiohttp.ClientSession(headers=_headers()) as sess:
            url = f"{self.base_url}{path}"
            for _ in range(3):
                async with sess.request(method, url, **kwargs) as r:
                    # 429/5xx 백오프
                    if r.status in (429, 500, 502, 503, 504):
                        await asyncio.sleep(1.5)
                        continue
                    if r.status >= 400:
                        text = await r.text()
                        raise RuntimeError(f"{method} {path} -> {r.status} {text}")
                    return await r.json()

    # ── Workflows
    async def create_workflow(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._req("POST", "/rest/workflows", data=json.dumps(payload))

    async def update_workflow(self, wf_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._req("PUT", f"/rest/workflows/{wf_id}", data=json.dumps(payload))

    async def activate_workflow(self, wf_id: str) -> Dict[str, Any]:
        return await self._req("POST", f"/rest/workflows/{wf_id}/activate")

    async def deactivate_workflow(self, wf_id: str) -> Dict[str, Any]:
        return await self._req("POST", f"/rest/workflows/{wf_id}/deactivate")

    async def list_workflows(self) -> List[Dict[str, Any]]:
        return await self._req("GET", "/rest/workflows")

    async def get_workflow(self, wf_id: str) -> Dict[str, Any]:
        return await self._req("GET", f"/rest/workflows/{wf_id}")

    # 일부 버전에서 수동 실행 엔드포인트가 다를 수 있음.
    async def run_workflow_once(self, wf_id: str, run_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # n8n 1.2x+에서 /run 지원. 미지원이면 UI에서 수동 테스트.
        try:
            return await self._req("POST", f"/rest/workflows/{wf_id}/run", data=json.dumps(run_data or {}))
        except RuntimeError as e:
            # 버전 차이 호환: 실행 이력 확인만 반환
            return {"ok": False, "hint": "This n8n version may not support /run; use UI test.", "error": str(e)}

    # ── Executions
    async def list_executions(self, workflow_id: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
        q = f"?limit={limit}"
        if workflow_id:
            q += f"&workflowId={workflow_id}"
        return await self._req("GET", f"/rest/executions{q}")

    # ── Credentials (이름→ID 매핑용, 값은 읽지 않음)
    async def list_credentials(self) -> List[Dict[str, Any]]:
        return await self._req("GET", "/rest/credentials")

    async def find_credential_id(self, name: str) -> Optional[str]:
        creds = await self.list_credentials()
        for c in creds:
            # n8n는 {id, name, type, ...}
            if c.get("name") == name:
                return c.get("id")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 워크플로우 빌더 (WF-1~4)
#  - n8n 워크플로우 스키마의 최소 필수 필드만 사용 (name, nodes, connections, active, settings)
#  - 노드 type은 n8n 기본 노드 명칭 사용 (버전에 따라 약간 다를 수 있음)
# ─────────────────────────────────────────────────────────────────────────────
def _settings_with_notes(notes: str) -> Dict[str, Any]:
    return {
        "timezone": TZ,
        "notesInFlow": True,
        "notes": notes[:5000],
    }


def _cred_block(node_key: str, cred_name: str, cred_id: Optional[str]) -> Dict[str, Any]:
    """
    n8n은 노드별 credentials 속성에 {typeKey: {id, name}} 형태를 기대.
    node_key 예) "telegramApi", "gmailOAuth2", "openAIApi"
    """
    block = {node_key: {"name": cred_name}}
    if cred_id:
        block[node_key]["id"] = cred_id
    return block


async def build_wf_mail_digest(client: N8NClient) -> Dict[str, Any]:
    # 크레덴셜 ID 조회 (이름만 넣어도 되지만, 가급적 ID 동봉)
    gmail_id = await client.find_credential_id(CREDENTIALS_MAP["gmail"])
    openai_id = await client.find_credential_id(CREDENTIALS_MAP["openai"])
    telegram_id = await client.find_credential_id(CREDENTIALS_MAP["telegram"])

    name = add_labels_to_name("wf-mail-digest-v1", {"stage": "draft", "tier": "p1"})
    nodes = [
        # 1) Schedule Trigger (09:00 KST)
        {
            "id": "scheduleTrigger",
            "name": "Schedule (09:00)",
            "type": "n8n-nodes-base.scheduleTrigger",
            "position": [240, 300],
            "parameters": {
                "rule": {
                    "interval": 1,
                    "unit": "days",
                    "hour": 9,
                    "minute": 0,
                }
            },
        },
        # 2) Gmail Read
        {
            "id": "gmailRead",
            "name": "Gmail → List (24h)",
            "type": "n8n-nodes-base.gmail",
            "position": [520, 300],
            "credentials": _cred_block("gmailOAuth2", CREDENTIALS_MAP["gmail"], gmail_id),
            "parameters": {
                "operation": "getAll",
                "additionalFields": {
                    "q": "newer_than:1d -category:promotions -in:spam",
                    "limit": 30,
                },
            },
        },
        # 3) Function: normalize
        {
            "id": "fnNormalize",
            "name": "Normalize",
            "type": "n8n-nodes-base.function",
            "position": [800, 300],
            "parameters": {
                "functionCode":
                "return items.map(i => ({ json: {\n"
                "  from: i.json.payload?.headers?.find(h=>h.name==='From')?.value || i.json.from || '',\n"
                "  subject: i.json.snippet || i.json.subject || '',\n"
                "  body: (i.json.snippet || '').slice(0, 500),\n"
                "}}));"
            },
        },
        # 4) OpenAI: summarize & classify
        {
            "id": "openaiSumm",
            "name": "OpenAI Summarize",
            "type": "n8n-nodes-base.openAi",
            "position": [1080, 300],
            "credentials": _cred_block("openAIApi", CREDENTIALS_MAP["openai"], openai_id),
            "parameters": {
                "operation": "chat",
                "chatModel": "gpt-4o-mini",  # 사용 모델은 자유롭게 변경
                "messages": [
                    {
                        "text": (
                            "다음 이메일 항목들을 업무/중요/개인/광고 4종으로 분류하고, "
                            "각 항목은 한 줄(120자 내)로 요약해줘. 한국어로."
                        ),
                        "type": "system",
                    },
                    {"text": "{{$json}}", "type": "user"},
                ],
                "options": {"temperature": 0.2},
            },
        },
        # 5) Telegram Send
        {
            "id": "tgSend",
            "name": "Telegram → Send",
            "type": "n8n-nodes-base.telegram",
            "position": [1360, 300],
            "credentials": _cred_block("telegramApi", CREDENTIALS_MAP["telegram"], telegram_id),
            "parameters": {
                "operation": "sendMessage",
                "text": (
                    "📬 *Daily Mail Digest*\n"
                    "{{$json.choices[0].message?.content || $json.data || '요약 결과 없음'}}"
                ),
                "additionalFields": {
                    "parse_mode": "Markdown",
                },
            },
        },
    ]
    connections = {
        "scheduleTrigger": {"main": [[{"node": "gmailRead", "type": "main", "index": 0}]]},
        "gmailRead": {"main": [[{"node": "fnNormalize", "type": "main", "index": 0}]]},
        "fnNormalize": {"main": [[{"node": "openaiSumm", "type": "main", "index": 0}]]},
        "openaiSumm": {"main": [[{"node": "tgSend", "type": "main", "index": 0}]]},
    }

    payload = {
        "name": name,
        "active": False,
        "nodes": nodes,
        "connections": connections,
        "settings": _settings_with_notes("매일 09:00 지난 24시간 Gmail 요약 → Telegram 전송"),
    }
    return payload


async def build_wf_tg_to_gmail(client: N8NClient, tg_chat_whitelist: List[str], mail_to: str) -> Dict[str, Any]:
    gmail_id = await client.find_credential_id(CREDENTIALS_MAP["gmail"])
    telegram_id = await client.find_credential_id(CREDENTIALS_MAP["telegram"])
    name = add_labels_to_name("wf-tg-to-gmail-v1", {"stage": "draft", "tier": "p2"})

    nodes = [
        {
            "id": "tgTrigger",
            "name": "Telegram Trigger",
            "type": "n8n-nodes-base.telegramTrigger",
            "position": [240, 300],
            "credentials": _cred_block("telegramApi", CREDENTIALS_MAP["telegram"], telegram_id),
            "parameters": {
                "updates": ["message"],
                "additionalFields": {},
            },
        },
        {
            "id": "fnFilter",
            "name": "Filter (chat & keyword)",
            "type": "n8n-nodes-base.function",
            "position": [520, 300],
            "parameters": {
                "functionCode":
                "const whitelist = " + json.dumps(tg_chat_whitelist) + ";\n"
                "const kw = ['#sendmail','보고','승인','에러'];\n"
                "return items.filter(i=>{\n"
                "  const chat = i.json.message?.chat?.id?.toString() || '';\n"
                "  const text = i.json.message?.text || '';\n"
                "  const okChat = whitelist.includes(chat);\n"
                "  const okKw = kw.some(k=> text.includes(k));\n"
                "  return okChat && okKw;\n"
                "});"
            },
        },
        {
            "id": "gmailSend",
            "name": "Gmail → Send",
            "type": "n8n-nodes-base.gmail",
            "position": [800, 300],
            "credentials": _cred_block("gmailOAuth2", CREDENTIALS_MAP["gmail"], gmail_id),
            "parameters": {
                "operation": "send",
                "toList": mail_to,
                "subject": "[TG] {{$json.message?.chat?.title || 'chat'}} - {{$json.message?.text?.slice(0,30) || ''}}",
                "message": "{{$json.message?.text || ''}}",
            },
        },
    ]
    connections = {
        "tgTrigger": {"main": [[{"node": "fnFilter", "type": "main", "index": 0}]]},
        "fnFilter": {"main": [[{"node": "gmailSend", "type": "main", "index": 0}]]},
    }

    payload = {
        "name": name,
        "active": False,
        "nodes": nodes,
        "connections": connections,
        "settings": _settings_with_notes("텔레그램(화이트리스트+키워드) → Gmail 포워드"),
    }
    return payload


async def build_wf_failure_guard(client: N8NClient) -> Dict[str, Any]:
    name = add_labels_to_name("wf-failure-guard-v1", {"stage": "draft", "tier": "p0"})
    nodes = [
        {
            "id": "sched",
            "name": "Schedule (Every 5min)",
            "type": "n8n-nodes-base.scheduleTrigger",
            "position": [240, 300],
            "parameters": {"rule": {"interval": 5, "unit": "minutes"}},
        },
        {
            "id": "fnScan",
            "name": "Scan Executions (owner=caia)",
            "type": "n8n-nodes-base.httpRequest",
            "position": [520, 300],
            "parameters": {
                "authentication": "predefinedCredentialType",
                "requestMethod": "GET",
                "url": f"{N8N_API_URL}/rest/executions?limit=50",
                "jsonParameters": True,
            },
        },
        {
            "id": "fnDecide",
            "name": "Decide Suspend & Patch",
            "type": "n8n-nodes-base.function",
            "position": [800, 300],
            "parameters": {
                "functionCode":
                "const failsByWf = {};\n"
                "for (const e of (items[0].json.data || [])) {\n"
                "  if (e?.status !== 'error') continue;\n"
                "  const wf = e.workflowId?.toString();\n"
                "  if (!wf) continue;\n"
                "  failsByWf[wf] = (failsByWf[wf]||0)+1;\n"
                "}\n"
                "const targets = Object.entries(failsByWf)\n"
                "  .filter(([wf,cnt])=> cnt >= 3)\n"
                "  .map(([wf])=> ({json:{workflowId:wf}}));\n"
                "return targets;"
            },
        },
        # 여기선 실제 비활성화/패치를 HTTP Request로 호출하도록 설계(엔드포인트는 운영 버전에 맞춰 조정)
        {
            "id": "httpSuspend",
            "name": "Suspend (POST /deactivate)",
            "type": "n8n-nodes-base.httpRequest",
            "position": [1080, 300],
            "parameters": {
                "requestMethod": "POST",
                "jsonParameters": True,
                "url": f"{N8N_API_URL}/rest/workflows/{{$json.workflowId}}/deactivate",
            },
        },
    ]
    connections = {
        "sched": {"main": [[{"node": "fnScan", "type": "main", "index": 0}]]},
        "fnScan": {"main": [[{"node": "fnDecide", "type": "main", "index": 0}]]},
        "fnDecide": {"main": [[{"node": "httpSuspend", "type": "main", "index": 0}]]},
    }
    payload = {
        "name": name,
        "active": False,
        "nodes": nodes,
        "connections": connections,
        "settings": _settings_with_notes("최근 30~50건 실행에서 동일 WF 3회 이상 실패시 suspend"),
    }
    return payload


async def build_wf_health_heartbeat(client: N8NClient, report_chat_id: Optional[str] = None) -> Dict[str, Any]:
    telegram_id = await client.find_credential_id(CREDENTIALS_MAP["telegram"])
    name = add_labels_to_name("wf-health-heartbeat-v1", {"stage": "draft", "tier": "p3"})
    nodes = [
        {
            "id": "sched",
            "name": "Schedule (08:55)",
            "type": "n8n-nodes-base.scheduleTrigger",
            "position": [240, 300],
            "parameters": {"rule": {"interval": 1, "unit": "days", "hour": 8, "minute": 55}},
        },
        {
            "id": "httpSelf",
            "name": "HTTP → CaiaAgent /health",
            "type": "n8n-nodes-base.httpRequest",
            "position": [520, 300],
            "parameters": {
                "requestMethod": "GET",
                "url": os.getenv("CAIA_AGENT_HEALTH_URL", "https://caia-agent-core-production.up.railway.app/health"),
                "jsonParameters": True,
            },
        },
        {
            "id": "tgSend",
            "name": "Telegram → Send",
            "type": "n8n-nodes-base.telegram",
            "position": [800, 300],
            "credentials": _cred_block("telegramApi", CREDENTIALS_MAP["telegram"], telegram_id),
            "parameters": {
                "operation": "sendMessage",
                "chatId": report_chat_id or "",
                "text": "❤️ CaiaAgent Health: {{$json.status || 'unknown'}}",
                "additionalFields": {"parse_mode": "Markdown"},
            },
        },
    ]
    connections = {
        "sched": {"main": [[{"node": "httpSelf", "type": "main", "index": 0}]]},
        "httpSelf": {"main": [[{"node": "tgSend", "type": "main", "index": 0}]]},
    }
    payload = {
        "name": name,
        "active": False,
        "nodes": nodes,
        "connections": connections,
        "settings": _settings_with_notes("08:55 하트비트 + 헬스체크 리포트"),
    }
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# 오케스트레이션: 생성 → 테스트 → 활성화/중단
# ─────────────────────────────────────────────────────────────────────────────
class N8NAutomation:
    def __init__(self, client: N8NClient):
        self.client = client

    async def create_draft(self, payload: Dict[str, Any]) -> str:
        wf = await self.client.create_workflow(payload)
        return wf.get("id") or wf.get("_id") or ""

    async def test_workflow(self, workflow_id: str) -> Dict[str, Any]:
        # 버전에 따라 /run 미지원일 수 있음 → graceful fallback
        result = await self.client.run_workflow_once(workflow_id)
        return result

    async def set_active(self, workflow_id: str, active: bool) -> None:
        if active:
            await self.client.activate_workflow(workflow_id)
        else:
            await self.client.deactivate_workflow(workflow_id)

    async def suspend_if_needed(self, workflow_id: str) -> None:
        await self.client.deactivate_workflow(workflow_id)

    # 고수준: “설계→생성→테스트” 한 번에
    async def deploy_spec(self, payload: Dict[str, Any], test: bool = True) -> Tuple[str, Optional[Dict[str, Any]]]:
        wf_id = await self.create_draft(payload)
        test_result = None
        if test:
            try:
                test_result = await self.test_workflow(wf_id)
            except Exception as e:
                test_result = {"ok": False, "error": str(e)}
        return wf_id, test_result


# ─────────────────────────────────────────────────────────────────────────────
# 예시 실행(스크립트): 4개 워크플로우를 드래프트로 생성
#   - 실제 서비스에서는 FastAPI 핸들러에서 이 로직을 호출하면 됨.
# ─────────────────────────────────────────────────────────────────────────────
async def main():
    client = N8NClient(N8N_API_URL, N8N_API_KEY)
    auto = N8NAutomation(client)

    # WF-1: 09:00 Gmail 요약 → TG
    wf1 = await build_wf_mail_digest(client)
    wf1_id, wf1_test = await auto.deploy_spec(wf1, test=True)

    # WF-2: 텔레그램 → Gmail (화이트리스트/키워드)
    tg_whitelist = [os.getenv("N8N_TG_CHAT_ID", "")]  # 보고 받을 채팅 ID
    mail_to = os.getenv("N8N_FORWARD_TO", "me@example.com")
    wf2 = await build_wf_tg_to_gmail(client, tg_whitelist, mail_to)
    wf2_id, wf2_test = await auto.deploy_spec(wf2, test=False)  # 트리거형은 UI에서 수동 테스트가 편함

    # WF-3: 실패 감시 & 자동중단
    wf3 = await build_wf_failure_guard(client)
    wf3_id, wf3_test = await auto.deploy_spec(wf3, test=False)

    # WF-4: 하트비트
    wf4 = await build_wf_health_heartbeat(client, report_chat_id=os.getenv("N8N_TG_CHAT_ID", ""))
    wf4_id, wf4_test = await auto.deploy_spec(wf4, test=False)

    summary = {
        "wf1_mail_digest": {"id": wf1_id, "test": wf1_test},
        "wf2_tg_to_gmail": {"id": wf2_id, "test": wf2_test},
        "wf3_failure_guard": {"id": wf3_id, "test": wf3_test},
        "wf4_health_heartbeat": {"id": wf4_id, "test": wf4_test},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # asyncio.run이 이미 event loop가 있는 환경에서 충돌할 수 있으니 조건 처리
    try:
        asyncio.run(main())
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
