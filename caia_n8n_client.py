import os
import json
import asyncio
from typing import Any, Dict, List, Optional, Tuple
import logging

import aiohttp

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Standardized response format
# ─────────────────────────────────────────────────────────────────────────────
def success_response(data: Any = None) -> Dict[str, Any]:
    """표준 성공 응답"""
    return {"ok": True, "data": data}

def error_response(error: Any, where: str = "", missing_credentials: List[str] = None) -> Dict[str, Any]:
    """표준 에러 응답"""
    response = {"ok": False, "error": error}
    if where:
        response["where"] = where
    if missing_credentials:
        response["missingCredentials"] = missing_credentials
    return response

# ─────────────────────────────────────────────────────────────────────────────
# Low-level REST client with retry and timeout
# ─────────────────────────────────────────────────────────────────────────────
class N8NClient:
    """
    Stable n8n REST API client with retry logic and error handling.
    """

    def __init__(self, base_url: str, api_key: str, session: Optional[aiohttp.ClientSession] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._session = session
        self.timeout = aiohttp.ClientTimeout(total=30)  # 30초 타임아웃
        self.max_retries = 3

    def _headers(self) -> Dict[str, str]:
        """n8n Public API 헤더 - X-N8N-API-KEY 우선"""
        return {
            "X-N8N-API-KEY": self.api_key,
            "Authorization": f"Bearer {self.api_key}",  # 호환성
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)

    async def _request_with_retry(self, method: str, path: str, params: Dict[str, Any] = None, json_body: Any = None) -> Dict[str, Any]:
        """재시도 로직이 포함된 요청"""
        await self._ensure_session()
        url = f"{self.base_url}{path}"
        
        for attempt in range(self.max_retries):
            try:
                logger.info(f"Attempt {attempt + 1}: {method} {url}")
                
                async with self._session.request(
                    method, url, 
                    headers=self._headers(), 
                    params=params, 
                    json=json_body,
                    timeout=self.timeout
                ) as r:
                    response_text = await r.text()
                    
                    if r.status >= 200 and r.status < 300:
                        # 성공
                        try:
                            data = json.loads(response_text) if response_text else {}
                            return success_response(data)
                        except json.JSONDecodeError:
                            return success_response({"raw": response_text})
                    else:
                        # 에러지만 재시도 불필요한 경우
                        if r.status in (400, 401, 403, 404, 405):
                            logger.warning(f"Client error {r.status}: {response_text[:200]}")
                            return error_response(
                                {"status": r.status, "body": response_text},
                                where=f"{method} {path}"
                            )
                        # 500대 에러는 재시도
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(2 ** attempt)  # 지수 백오프
                            continue
                        return error_response(
                            {"status": r.status, "body": response_text},
                            where=f"{method} {path}"
                        )
                        
            except asyncio.TimeoutError:
                logger.error(f"Timeout on attempt {attempt + 1}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return error_response({"type": "timeout"}, where=f"{method} {path}")
                
            except Exception as e:
                logger.error(f"Request error: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return error_response({"type": "exception", "message": str(e)}, where=f"{method} {path}")
        
        return error_response({"type": "max_retries_exceeded"}, where=f"{method} {path}")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ---- credentials ----
    async def list_credentials(self) -> Dict[str, Any]:
        """크레덴셜 목록 조회"""
        return await self._request_with_retry("GET", "/rest/credentials")

    async def get_credential_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """이름으로 크레덴셜 찾기"""
        result = await self.list_credentials()
        if not result.get("ok"):
            logger.warning(f"Failed to list credentials: {result}")
            return None
            
        creds = result.get("data", [])
        if isinstance(creds, dict):  # n8n이 {data: [...]} 형태로 반환할 수 있음
            creds = creds.get("data", [])
            
        for c in creds:
            if c.get("name") == name:
                return c
        return None

    # ---- workflows ----
    async def list_workflows(self) -> Dict[str, Any]:
        return await self._request_with_retry("GET", "/rest/workflows")

    async def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return await self._request_with_retry("GET", f"/rest/workflows/{workflow_id}")

    async def create_workflow(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request_with_retry("POST", "/rest/workflows", json_body=spec)

    async def update_workflow(self, workflow_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request_with_retry("PATCH", f"/rest/workflows/{workflow_id}", json_body=spec)

    async def delete_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return await self._request_with_retry("DELETE", f"/rest/workflows/{workflow_id}")

    async def activate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return await self._request_with_retry("POST", f"/rest/workflows/{workflow_id}/activate")

    async def deactivate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return await self._request_with_retry("POST", f"/rest/workflows/{workflow_id}/deactivate")

    async def run_workflow_once(self, workflow_id: str, run_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """워크플로우 실행 (지원 안 되면 힌트 반환)"""
        result = await self._request_with_retry("POST", f"/rest/workflows/{workflow_id}/run", json_body=run_data or {})
        
        if not result.get("ok"):
            error = result.get("error", {})
            if isinstance(error, dict) and error.get("status") in (404, 405):
                result["hint"] = "This n8n version may not support /run. Use UI 'Execute Workflow' instead."
        
        return result

    async def list_executions(self, workflow_id: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if workflow_id:
            params["workflowId"] = workflow_id
        return await self._request_with_retry("GET", "/rest/executions", params=params)


# ─────────────────────────────────────────────────────────────────────────────
# Credential Resolver with fallback
# ─────────────────────────────────────────────────────────────────────────────
class CredentialResolver:
    """크레덴셜 이름 매핑 (실패해도 계속 진행)"""
    
    def __init__(self, client: N8NClient):
        self.client = client
        self.missing_credentials = []

    async def resolve(self, type_key: str, cred_name: str) -> Dict[str, Any]:
        """크레덴셜 해결 (못 찾으면 빈 딕셔너리 반환)"""
        if not cred_name:
            logger.warning(f"No credential name provided for {type_key}")
            self.missing_credentials.append(f"{type_key}:empty")
            return {}
            
        cred = await self.client.get_credential_by_name(cred_name)
        if not cred:
            logger.warning(f"Credential '{cred_name}' not found for {type_key}")
            self.missing_credentials.append(cred_name)
            return {}
            
        return {type_key: {"id": cred.get("id"), "name": cred.get("name")}}


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Templates (with error handling)
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
    """Daily Gmail digest workflow"""
    try:
        cred = CredentialResolver(client)
        
        # 환경변수에서 크레덴셜 이름 가져오기
        gmail_name = os.getenv("N8N_CRED_GMAIL", "Gmail account")
        openai_name = os.getenv("N8N_CRED_OPENAI", "OpenAi account 2")
        telegram_name = os.getenv("N8N_CRED_TELEGRAM", "Telegram account 2")
        chat_id = os.getenv("N8N_TG_CHAT_ID", "8046036996")

        # 크레덴셜 해결
        gmail_cred = await cred.resolve("gmailOAuth2", gmail_name)
        openai_cred = await cred.resolve("openAiApi", openai_name)
        telegram_cred = await cred.resolve("telegramApi", telegram_name)

        spec = _base_spec("caia-managed/mail-digest/prod")
        
        # Schedule node
        schedule = {
            "id": "schedule-0900",
            "name": "Schedule 09:00",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": 1.1,
            "position": [300, 300],
            "parameters": {
                "rule": {
                    "interval": [{"field": "hours", "hoursInterval": 24}]
                }
            }
        }

        # Gmail node
        gmail_node = {
            "id": "gmail-get",
            "name": "Get Emails",
            "type": "n8n-nodes-base.gmail",
            "typeVersion": 2,
            "position": [560, 300],
            "parameters": {
                "resource": "message",
                "operation": "getAll",
                "q": "newer_than:1d -category:promotions -in:spam",
                "limit": 30
            }
        }
        if gmail_cred:
            gmail_node["credentials"] = gmail_cred

        # OpenAI node
        openai_node = {
            "id": "openai-sum",
            "name": "Summarize",
            "type": "n8n-nodes-base.openAi",
            "typeVersion": 1,
            "position": [820, 300],
            "parameters": {
                "resource": "text",
                "operation": "complete",
                "prompt": "Summarize these emails in Korean bullet points:\n{{ $json }}",
                "maxTokens": 800
            }
        }
        if openai_cred:
            openai_node["credentials"] = openai_cred

        # Telegram node
        tg_send = {
            "id": "tg-send",
            "name": "Send to Telegram",
            "type": "n8n-nodes-base.telegram",
            "typeVersion": 1.2,
            "position": [1080, 300],
            "parameters": {
                "chatId": chat_id,
                "text": "📬 *Daily Mail Digest*\n\n{{ $json.choices[0].text || 'No summary' }}",
                "additionalFields": {"parse_mode": "Markdown"}
            }
        }
        if telegram_cred:
            tg_send["credentials"] = telegram_cred

        spec["nodes"] = [schedule, gmail_node, openai_node, tg_send]
        spec["connections"] = {
            "Schedule 09:00": {"main": [[{"node": "Get Emails", "type": "main", "index": 0}]]},
            "Get Emails": {"main": [[{"node": "Summarize", "type": "main", "index": 0}]]},
            "Summarize": {"main": [[{"node": "Send to Telegram", "type": "main", "index": 0}]]}
        }
        
        # 누락된 크레덴셜 정보 추가
        if cred.missing_credentials:
            spec["_warnings"] = {"missingCredentials": cred.missing_credentials}
            
        return spec
        
    except Exception as e:
        logger.error(f"Error building mail digest workflow: {e}")
        return {
            "name": "mail-digest-error",
            "error": str(e),
            "nodes": [],
            "connections": {}
        }


async def build_wf_tg_to_gmail(client: N8NClient, tg_chat_whitelist: List[str], mail_to: str) -> Dict[str, Any]:
    """Telegram to Gmail forward workflow"""
    try:
        cred = CredentialResolver(client)
        
        telegram_name = os.getenv("N8N_CRED_TELEGRAM", "Telegram account 2")
        gmail_name = os.getenv("N8N_CRED_GMAIL", "Gmail account")

        telegram_cred = await cred.resolve("telegramApi", telegram_name)
        gmail_cred = await cred.resolve("gmailOAuth2", gmail_name)

        spec = _base_spec("caia-managed/tg-forward/prod")

        # Telegram Trigger
        tg_trigger = {
            "id": "tg-trigger",
            "name": "Telegram Trigger",
            "type": "n8n-nodes-base.telegramTrigger",
            "typeVersion": 1.1,
            "position": [300, 300],
            "parameters": {
                "updates": ["message"],
                "additionalFields": {"userIds": ",".join(tg_chat_whitelist or [])}
            }
        }
        if telegram_cred:
            tg_trigger["credentials"] = telegram_cred

        # Gmail Send
        gmail_send = {
            "id": "gmail-send",
            "name": "Forward to Gmail",
            "type": "n8n-nodes-base.gmail",
            "typeVersion": 2,
            "position": [560, 300],
            "parameters": {
                "resource": "message",
                "operation": "send",
                "sendTo": mail_to,
                "subject": "Telegram → Gmail",
                "message": "={{ $json.message?.text || 'No text' }}"
            }
        }
        if gmail_cred:
            gmail_send["credentials"] = gmail_cred

        # Telegram Reply
        tg_ack = {
            "id": "tg-ack",
            "name": "Reply Ack",
            "type": "n8n-nodes-base.telegram",
            "typeVersion": 1.2,
            "position": [820, 300],
            "parameters": {
                "chatId": "={{ $json.message.chat.id }}",
                "text": "✉️ Forwarded to Gmail"
            }
        }
        if telegram_cred:
            tg_ack["credentials"] = telegram_cred

        spec["nodes"] = [tg_trigger, gmail_send, tg_ack]
        spec["connections"] = {
            "Telegram Trigger": {"main": [[{"node": "Forward to Gmail", "type": "main", "index": 0}]]},
            "Forward to Gmail": {"main": [[{"node": "Reply Ack", "type": "main", "index": 0}]]}
        }
        
        if cred.missing_credentials:
            spec["_warnings"] = {"missingCredentials": cred.missing_credentials}
            
        return spec
        
    except Exception as e:
        logger.error(f"Error building tg-to-gmail workflow: {e}")
        return {
            "name": "tg-forward-error",
            "error": str(e),
            "nodes": [],
            "connections": {}
        }


async def build_wf_failure_guard(client: N8NClient) -> Dict[str, Any]:
    """Failure guard workflow"""
    try:
        spec = _base_spec("caia-managed/failure-guard/prod")
        
        # Manual trigger
        manual = {
            "id": "manual",
            "name": "Manual Trigger",
            "type": "n8n-nodes-base.manualTrigger",
            "typeVersion": 1,
            "position": [300, 300]
        }

        # Code node for checking
        check = {
            "id": "check",
            "name": "Check Status",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [560, 300],
            "parameters": {
                "jsCode": "return [{status: 'checking', timestamp: new Date().toISOString()}];"
            }
        }

        spec["nodes"] = [manual, check]
        spec["connections"] = {
            "Manual Trigger": {"main": [[{"node": "Check Status", "type": "main", "index": 0}]]}
        }
        
        return spec
        
    except Exception as e:
        logger.error(f"Error building failure guard workflow: {e}")
        return {
            "name": "failure-guard-error",
            "error": str(e),
            "nodes": [],
            "connections": {}
        }


async def build_wf_health_heartbeat(client: N8NClient, report_chat_id: str) -> Dict[str, Any]:
    """Health heartbeat workflow"""
    try:
        cred = CredentialResolver(client)
        telegram_name = os.getenv("N8N_CRED_TELEGRAM", "Telegram account 2")
        telegram_cred = await cred.resolve("telegramApi", telegram_name)
        health_url = os.getenv("CAIA_AGENT_HEALTH_URL", "https://caia-agent-core-production.up.railway.app/health")

        spec = _base_spec("caia-managed/heartbeat/prod")

        # Schedule
        schedule = {
            "id": "hb-schedule",
            "name": "Schedule 08:55",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": 1.1,
            "position": [300, 300],
            "parameters": {
                "rule": {
                    "interval": [{"field": "hours", "hoursInterval": 24}]
                }
            }
        }

        # HTTP Request
        http = {
            "id": "hb-http",
            "name": "Check Core Health",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.1,
            "position": [560, 300],
            "parameters": {
                "method": "GET",
                "url": health_url
            }
        }

        # Telegram
        tg = {
            "id": "hb-tg",
            "name": "Report",
            "type": "n8n-nodes-base.telegram",
            "typeVersion": 1.2,
            "position": [820, 300],
            "parameters": {
                "chatId": report_chat_id,
                "text": "🫀 Heartbeat: {{ $json.ok ? 'OK' : 'FAIL' }}"
            }
        }
        if telegram_cred:
            tg["credentials"] = telegram_cred

        spec["nodes"] = [schedule, http, tg]
        spec["connections"] = {
            "Schedule 08:55": {"main": [[{"node": "Check Core Health", "type": "main", "index": 0}]]},
            "Check Core Health": {"main": [[{"node": "Report", "type": "main", "index": 0}]]}
        }
        
        if cred.missing_credentials:
            spec["_warnings"] = {"missingCredentials": cred.missing_credentials}
            
        return spec
        
    except Exception as e:
        logger.error(f"Error building heartbeat workflow: {e}")
        return {
            "name": "heartbeat-error",
            "error": str(e),
            "nodes": [],
            "connections": {}
        }


# ─────────────────────────────────────────────────────────────────────────────
# High-level Automation
# ─────────────────────────────────────────────────────────────────────────────
class N8NAutomation:
    """고수준 자동화 헬퍼"""
    
    def __init__(self, client: N8NClient):
        self.client = client

    async def deploy_spec(self, spec: Dict[str, Any], test: bool = False) -> Dict[str, Any]:
        """워크플로우 배포 (에러 안전)"""
        try:
            # 에러가 있는 spec은 생성 시도하지 않음
            if "error" in spec:
                return {
                    "ok": False,
                    "name": spec.get("name", "unknown"),
                    "error": spec.get("error"),
                    "skipped": True
                }
            
            # 워크플로우 생성
            result = await self.client.create_workflow(spec)
            
            if not result.get("ok"):
                return {
                    "ok": False,
                    "name": spec.get("name"),
                    "error": result.get("error"),
                    "warnings": spec.get("_warnings")
                }
            
            # ID 추출
            data = result.get("data", {})
            wf_id = data.get("id") or data.get("_id") or ""
            
            if not wf_id:
                return {
                    "ok": False,
                    "name": spec.get("name"),
                    "error": "No workflow ID in response"
                }
            
            # 테스트 실행 (옵션)
            test_result = None
            if test:
                test_response = await self.client.run_workflow_once(wf_id)
                test_result = {
                    "ok": test_response.get("ok"),
                    "hint": test_response.get("hint")
                }
            
            return {
                "ok": True,
                "id": wf_id,
                "name": spec.get("name"),
                "test": test_result,
                "warnings": spec.get("_warnings")
            }
            
        except Exception as e:
            logger.error(f"Deploy error: {e}")
            return {
                "ok": False,
                "name": spec.get("name", "unknown"),
                "error": str(e)
            }

    async def activate(self, workflow_id: str) -> Dict[str, Any]:
        return await self.client.activate_workflow(workflow_id)

    async def deactivate(self, workflow_id: str) -> Dict[str, Any]:
        return await self.client.deactivate_workflow(workflow_id)
