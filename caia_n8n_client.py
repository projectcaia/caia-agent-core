import os
import json
import asyncio
import base64
from typing import Any, Dict, List, Optional
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

    def __init__(self, base_url: str, api_key: Optional[str] = None,
                 basic_user: Optional[str] = None, basic_password: Optional[str] = None,
                 session: Optional[aiohttp.ClientSession] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("N8N_API_KEY")
        self.basic_user = basic_user or os.getenv("N8N_BASIC_AUTH_USER")
        self.basic_password = basic_password or os.getenv("N8N_BASIC_AUTH_PASSWORD")
        self._session = session
        self.timeout = aiohttp.ClientTimeout(total=30)  # 30초 타임아웃
        self.max_retries = 3

    def _headers(self) -> Dict[str, str]:
        """n8n Public API 헤더 조립"""
        # ✅ BasicAuth 분기
        if self.basic_user and self.basic_password:
            token = base64.b64encode(f"{self.basic_user}:{self.basic_password}".encode()).decode()
            return {
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

        # ✅ API Key 분기
        if self.api_key:
            return {
                "X-N8N-API-KEY": self.api_key,
                "Authorization": f"Bearer {self.api_key}",  # 호환성
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

        # ❌ 인증정보 없음
        raise ValueError("No authentication configured: set N8N_API_KEY or N8N_BASIC_AUTH_USER/PASSWORD")

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)

    async def _request_with_retry(self, method: str, path: str,
                                  params: Dict[str, Any] = None, json_body: Any = None) -> Dict[str, Any]:
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
                    
                    if 200 <= r.status < 300:
                        try:
                            data = json.loads(response_text) if response_text else {}
                            return success_response(data)
                        except json.JSONDecodeError:
                            return success_response({"raw": response_text})
                    else:
                        if r.status in (400, 401, 403, 404, 405):
                            logger.warning(f"Client error {r.status}: {response_text[:200]}")
                            return error_response(
                                {"status": r.status, "body": response_text},
                                where=f"{method} {path}"
                            )
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
        return await self._request_with_retry("GET", "/rest/credentials")

    async def get_credential_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        result = await self.list_credentials()
        if not result.get("ok"):
            logger.warning(f"Failed to list credentials: {result}")
            return None
            
        creds = result.get("data", [])
        if isinstance(creds, dict):
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
    def __init__(self, client: N8NClient):
        self.client = client
        self.missing_credentials = []

    async def resolve(self, type_key: str, cred_name: str) -> Dict[str, Any]:
        if not cred_name:
            self.missing_credentials.append(f"{type_key}:empty")
            return {}
            
        cred = await self.client.get_credential_by_name(cred_name)
        if not cred:
            self.missing_credentials.append(cred_name)
            return {}
            
        return {type_key: {"id": cred.get("id"), "name": cred.get("name")}}


# ─────────────────────────────────────────────────────────────────────────────
# High-level Automation
# ─────────────────────────────────────────────────────────────────────────────
class N8NAutomation:
    def __init__(self, client: N8NClient):
        self.client = client

    async def deploy_spec(self, spec: Dict[str, Any], test: bool = False) -> Dict[str, Any]:
        try:
            if "error" in spec:
                return {"ok": False, "name": spec.get("name", "unknown"), "error": spec.get("error"), "skipped": True}
            
            result = await self.client.create_workflow(spec)
            if not result.get("ok"):
                return {"ok": False, "name": spec.get("name"), "error": result.get("error"), "warnings": spec.get("_warnings")}
            
            data = result.get("data", {})
            wf_id = data.get("id") or data.get("_id") or ""
            if not wf_id:
                return {"ok": False, "name": spec.get("name"), "error": "No workflow ID in response"}
            
            test_result = None
            if test:
                test_response = await self.client.run_workflow_once(wf_id)
                test_result = {"ok": test_response.get("ok"), "hint": test_response.get("hint")}
            
            return {"ok": True, "id": wf_id, "name": spec.get("name"), "test": test_result, "warnings": spec.get("_warnings")}
            
        except Exception as e:
            logger.error(f"Deploy error: {e}")
            return {"ok": False, "name": spec.get("name", "unknown"), "error": str(e)}

    async def activate(self, workflow_id: str) -> Dict[str, Any]:
        return await self.client.activate_workflow(workflow_id)

    async def deactivate(self, workflow_id: str) -> Dict[str, Any]:
        return await self.client.deactivate_workflow(workflow_id)
