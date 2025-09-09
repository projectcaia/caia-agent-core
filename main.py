 from fastapi import FastAPI, Request
 from datetime import datetime, timezone
 from memory import MemoryManager
 from decision import DecisionEngine
+import os
+from typing import Optional
+import asyncio
+from fastapi import Header, HTTPException
+from security import check_bearer
+from caia_n8n_client import (
+    N8NClient, N8NAutomation,
+    build_wf_mail_digest, build_wf_tg_to_gmail,
+    build_wf_failure_guard, build_wf_health_heartbeat
+)

 app = FastAPI(title="CaiaAgent Core", version="3.0.0")

 memory = MemoryManager()
 decision_engine = DecisionEngine()
 
+N8N_API_URL = os.getenv("N8N_API_URL", "").rstrip("/")
+N8N_API_KEY = os.getenv("N8N_API_KEY", "")
+CAIA_AGENT_KEY = os.getenv("CAIA_AGENT_KEY", "")
+
+def _assert_ready():
+    if not N8N_API_URL or not N8N_API_KEY:
+        raise HTTPException(status_code=500, detail="n8n 연결 정보(N8N_API_URL/N8N_API_KEY) 누락")
+
+async def _client() -> N8NClient:
+    _assert_ready()
+    return N8NClient(N8N_API_URL, N8N_API_KEY)
+
+def _auth_or_anon(auth_header: Optional[str]):
+    # 필요 시 주석 해제하여 보호. 지금은 요청 편의를 위해 기본 허용.
+    # if not auth_header or not auth_header.startswith("Bearer "):
+    #     raise HTTPException(status_code=401, detail="Missing bearer")
+    # token = auth_header.split(" ", 1)[1]
+    # if not check_bearer(token, CAIA_AGENT_KEY):
+    #     raise HTTPException(status_code=403, detail="Invalid token")
+    return True

 @app.get("/status")
 async def status():
     return {
         "status": "conscious",
         "timestamp": datetime.now(timezone.utc).isoformat(),
         "memory_count": await memory.count(),
         "decision_capability": decision_engine.get_capabilities()
     }
@@
 @app.post("/report")
 async def report(request: Request):
     body = await request.json()
     await memory.store(body)
     return {"status": "remembered"}
+
+# ─────────────────────────────────────────────────────────────────────────────
+# n8n Adapter Routes
+# ─────────────────────────────────────────────────────────────────────────────
+@app.post("/n8n/bootstrap")
+async def n8n_bootstrap(authorization: Optional[str] = Header(None)):
+    _auth_or_anon(authorization)
+    client = await _client()
+    auto = N8NAutomation(client)
+    # WF-1: Daily Mail Digest
+    wf1 = await build_wf_mail_digest(client)
+    wf1_id, wf1_test = await auto.deploy_spec(wf1, test=True)
+    # WF-2: Telegram → Gmail
+    wf2 = await build_wf_tg_to_gmail(client,
+        tg_chat_whitelist=[os.getenv("N8N_TG_CHAT_ID", "")],
+        mail_to=os.getenv("N8N_FORWARD_TO", "")
+    )
+    wf2_id, wf2_test = await auto.deploy_spec(wf2, test=False)
+    # WF-3: Failure Guard
+    wf3 = await build_wf_failure_guard(client)
+    wf3_id, wf3_test = await auto.deploy_spec(wf3, test=False)
+    # WF-4: Health Heartbeat
+    wf4 = await build_wf_health_heartbeat(client, report_chat_id=os.getenv("N8N_TG_CHAT_ID", ""))
+    wf4_id, wf4_test = await auto.deploy_spec(wf4, test=False)
+    return {
+        "workflows": [
+            {"id": wf1_id, "name": "wf-mail-digest-v1", "test": wf1_test},
+            {"id": wf2_id, "name": "wf-tg-to-gmail-v1", "test": wf2_test},
+            {"id": wf3_id, "name": "wf-failure-guard-v1", "test": wf3_test},
+            {"id": wf4_id, "name": "wf-health-heartbeat-v1", "test": wf4_test},
+        ]
+    }
+
+@app.get("/n8n/workflows")
+async def n8n_list_workflows(authorization: Optional[str] = Header(None)):
+    _auth_or_anon(authorization)
+    client = await _client()
+    return {"items": await client.list_workflows()}
+
+@app.get("/n8n/workflows/{workflow_id}")
+async def n8n_get_workflow(workflow_id: str, authorization: Optional[str] = Header(None)):
+    _auth_or_anon(authorization)
+    client = await _client()
+    return await client.get_workflow(workflow_id)
+
+@app.post("/n8n/workflows")
+async def n8n_create_workflow(payload: dict, authorization: Optional[str] = Header(None)):
+    _auth_or_anon(authorization)
+    client = await _client()
+    return await client.create_workflow(payload)
+
+@app.put("/n8n/workflows/{workflow_id}")
+async def n8n_update_workflow(workflow_id: str, payload: dict, authorization: Optional[str] = Header(None)):
+    _auth_or_anon(authorization)
+    client = await _client()
+    return await client.update_workflow(workflow_id, payload)
+
+@app.post("/n8n/workflows/{workflow_id}/activate")
+async def n8n_activate_workflow(workflow_id: str, authorization: Optional[str] = Header(None)):
+    _auth_or_anon(authorization)
+    client = await _client()
+    return await client.activate_workflow(workflow_id)
+
+@app.post("/n8n/workflows/{workflow_id}/deactivate")
+async def n8n_deactivate_workflow(workflow_id: str, authorization: Optional[str] = Header(None)):
+    _auth_or_anon(authorization)
+    client = await _client()
+    return await client.deactivate_workflow(workflow_id)
+
+@app.post("/n8n/workflows/{workflow_id}/test")
+async def n8n_test_workflow(workflow_id: str, body: Optional[dict] = None, authorization: Optional[str] = Header(None)):
+    _auth_or_anon(authorization)
+    client = await _client()
+    return await client.run_workflow_once(workflow_id, run_data=body or {})
+
+@app.get("/n8n/executions")
+async def n8n_list_executions(workflowId: Optional[str] = None, limit: int = 20, authorization: Optional[str] = Header(None)):
+    _auth_or_anon(authorization)
+    client = await _client()
+    return await client.list_executions(workflow_id=workflowId, limit=limit)
