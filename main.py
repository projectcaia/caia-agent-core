from fastapi import FastAPI, Request, Header, HTTPException
from datetime import datetime, timezone
from typing import Optional
import os
import logging
import traceback

from memory import MemoryManager
from decision import DecisionEngine
from security import check_bearer
from caia_n8n_client import (
    N8NClient, N8NAutomation,
    build_wf_mail_digest, build_wf_tg_to_gmail,
    build_wf_failure_guard, build_wf_health_heartbeat,
)
from n8n_controller import (
    start_n8n, stop_n8n, use_n8n_workflow,
    get_service_status, test_railway_connection,
    N8NBatchOperation
)

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="CaiaAgent Core", version="3.1.0")

memory = MemoryManager()
decision_engine = DecisionEngine()

# 환경변수
N8N_API_URL = os.getenv("N8N_API_URL", "").rstrip("/")
N8N_API_KEY = os.getenv("N8N_API_KEY", "")
N8N_BASIC_AUTH_USER = os.getenv("N8N_BASIC_AUTH_USER", "")
N8N_BASIC_AUTH_PASSWORD = os.getenv("N8N_BASIC_AUTH_PASSWORD", "")
CAIA_AGENT_KEY = os.getenv("CAIA_AGENT_KEY", "")
RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN")
N8N_SERVICE_ID = os.getenv("N8N_SERVICE_ID")

def _assert_n8n_ready() -> bool:
    """
    Validate that n8n configuration is present. n8n is considered
    configured if the API URL is set and either an API key or
    BasicAuth credentials are provided. This ensures that the
    downstream N8NClient can authenticate.
    """
    if not N8N_API_URL:
        logger.error("Missing N8N_API_URL")
        return False

    # At least one authentication method must be present
    has_api_key = bool(N8N_API_KEY)
    has_basic = bool(N8N_BASIC_AUTH_USER and N8N_BASIC_AUTH_PASSWORD)
    if not (has_api_key or has_basic):
        logger.error("Missing n8n authentication; set N8N_API_KEY or N8N_BASIC_AUTH_USER/PASSWORD")
        return False

    return True

async def _n8n_client() -> Optional[N8NClient]:
    """n8n 클라이언트 생성"""
    if not _assert_n8n_ready():
        return None
    # Pass both API key and BasicAuth credentials to the client; it
    # will determine which to use internally.
    return N8NClient(
        base_url=N8N_API_URL,
        api_key=N8N_API_KEY if N8N_API_KEY else None,
        basic_user=N8N_BASIC_AUTH_USER if N8N_BASIC_AUTH_USER else None,
        basic_password=N8N_BASIC_AUTH_PASSWORD if N8N_BASIC_AUTH_PASSWORD else None,
    )

def _auth_or_anon(authorization: Optional[str]):
    """인증 체크 (옵션)"""
    # 필요시 활성화
    # if not authorization or not authorization.startswith("Bearer "):
    #     raise HTTPException(status_code=401, detail="Missing bearer")
    # token = authorization.split(" ", 1)[1]
    # if not check_bearer(token, CAIA_AGENT_KEY):
    #     raise HTTPException(status_code=403, detail="Invalid token")
    return True

# ── Core Endpoints ──
@app.get("/health")
async def health():
    """헬스체크"""
    return {"ok": True, "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/status")
async def status():
    """상태 확인"""
    n8n_status = get_service_status()
    return {
        "status": "conscious",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "memory_count": await memory.count(),
        "decision_capability": decision_engine.get_capabilities(),
        "n8n_configured": _assert_n8n_ready(),
        "n8n_service": n8n_status,
        "railway_configured": bool(RAILWAY_API_TOKEN and N8N_SERVICE_ID)
    }

@app.post("/orchestrate")
async def orchestrate(request: Request):
    """오케스트레이션 - n8n 워크플로우 실행 시 자동 제어"""
    try:
        body = await request.json()
        
        # Check if this request needs n8n workflow
        workflow_id = body.get("workflow_id")
        
        if workflow_id and RAILWAY_API_TOKEN and N8N_SERVICE_ID:
            # Use n8n controller for workflow execution
            logger.info(f"Orchestrating with n8n workflow: {workflow_id}")
            workflow_payload = {
                "message": body.get("message", ""),
                "trigger_type": body.get("trigger_type", "unknown"),
                "metadata": body.get("metadata", {})
            }
            
            try:
                # This will automatically start/stop n8n
                workflow_result = use_n8n_workflow(workflow_id, workflow_payload)
                return {
                    "ok": True,
                    "workflow_result": workflow_result,
                    "controlled_execution": True
                }
            except Exception as wf_error:
                logger.error(f"Workflow execution failed: {wf_error}")
                # Fall back to regular orchestration
        
        # Regular orchestration without n8n workflow
        context = await memory.recall(body.get("message", ""))
        decision = await decision_engine.decide(
            message=body.get("message"),
            context=context,
            trigger_type=body.get("trigger_type", "unknown"),
            metadata=body.get("metadata", {}),
        )
        return {"decision": decision, "memory_context": context}
    except Exception as e:
        logger.error(f"Orchestrate error: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/report")
async def report(request: Request):
    """리포트 저장"""
    try:
        body = await request.json()
        await memory.store(body)
        return {"status": "remembered"}
    except Exception as e:
        logger.error(f"Report error: {e}")
        return {"ok": False, "error": str(e)}

# ── n8n Adapter Endpoints (모두 안전하게 래핑) ──

@app.post("/n8n/bootstrap")
async def n8n_bootstrap(authorization: Optional[str] = Header(None)):
    """표준 워크플로우 4종 생성"""
    try:
        _auth_or_anon(authorization)
        
        # Start n8n service if controller is configured
        if RAILWAY_API_TOKEN and N8N_SERVICE_ID:
            logger.info("Starting n8n for bootstrap operation...")
            try:
                start_n8n()
            except Exception as e:
                logger.warning(f"Failed to start n8n service: {e}")
        
        client = await _n8n_client()
        if not client:
            return {
                "ok": False,
                "where": "bootstrap.init",
                "error": "n8n not configured",
                "hint": "Set N8N_API_URL and N8N_API_KEY"
            }
        
        auto = N8NAutomation(client)
        results = []
        
        # 1. Mail Digest
        try:
            logger.info("Creating mail digest workflow...")
            wf1 = await build_wf_mail_digest(client)
            wf1_result = await auto.deploy_spec(wf1, test=False)
            wf1_result["name"] = "mail-digest"
            results.append(wf1_result)
        except Exception as e:
            logger.error(f"Mail digest failed: {e}")
            results.append({
                "ok": False,
                "name": "mail-digest",
                "error": str(e)
            })
        
        # 2. Telegram to Gmail
        try:
            logger.info("Creating tg-to-gmail workflow...")
            wf2 = await build_wf_tg_to_gmail(
                client,
                tg_chat_whitelist=[os.getenv("N8N_TG_CHAT_ID", "8046036996")],
                mail_to=os.getenv("N8N_FORWARD_TO", "flyartnam@gmail.com"),
            )
            wf2_result = await auto.deploy_spec(wf2, test=False)
            wf2_result["name"] = "tg-to-gmail"
            results.append(wf2_result)
        except Exception as e:
            logger.error(f"TG to Gmail failed: {e}")
            results.append({
                "ok": False,
                "name": "tg-to-gmail",
                "error": str(e)
            })
        
        # 3. Failure Guard
        try:
            logger.info("Creating failure guard workflow...")
            wf3 = await build_wf_failure_guard(client)
            wf3_result = await auto.deploy_spec(wf3, test=False)
            wf3_result["name"] = "failure-guard"
            results.append(wf3_result)
        except Exception as e:
            logger.error(f"Failure guard failed: {e}")
            results.append({
                "ok": False,
                "name": "failure-guard",
                "error": str(e)
            })
        
        # 4. Health Heartbeat
        try:
            logger.info("Creating heartbeat workflow...")
            wf4 = await build_wf_health_heartbeat(
                client,
                report_chat_id=os.getenv("N8N_TG_CHAT_ID", "8046036996")
            )
            wf4_result = await auto.deploy_spec(wf4, test=False)
            wf4_result["name"] = "heartbeat"
            results.append(wf4_result)
        except Exception as e:
            logger.error(f"Heartbeat failed: {e}")
            results.append({
                "ok": False,
                "name": "heartbeat",
                "error": str(e)
            })
        
        # 결과 집계
        success_count = sum(1 for r in results if r.get("ok"))
        total_count = len(results)
        
        # 누락된 크레덴셜 수집
        all_missing = []
        for r in results:
            warnings = r.get("warnings", {})
            missing = warnings.get("missingCredentials", [])
            all_missing.extend(missing)
        
        await client.close()
        
        # Stop n8n service if controller is configured
        if RAILWAY_API_TOKEN and N8N_SERVICE_ID:
            logger.info("Stopping n8n after bootstrap operation...")
            try:
                stop_n8n()
            except Exception as e:
                logger.warning(f"Failed to stop n8n service: {e}")
        
        return {
            "ok": success_count > 0,  # 하나라도 성공하면 ok
            "summary": f"{success_count}/{total_count} workflows created",
            "results": results,
            "missingCredentials": list(set(all_missing)) if all_missing else None
        }
        
    except Exception as e:
        logger.error(f"Bootstrap error: {traceback.format_exc()}")
        return {
            "ok": False,
            "where": "bootstrap",
            "error": str(e)
        }

@app.get("/n8n/workflows")
async def n8n_list_workflows(authorization: Optional[str] = Header(None)):
    """워크플로우 목록"""
    try:
        _auth_or_anon(authorization)
        
        client = await _n8n_client()
        if not client:
            return {"ok": False, "error": "n8n not configured"}
        
        result = await client.list_workflows()
        await client.close()
        
        if result.get("ok"):
            return {"ok": True, "items": result.get("data", [])}
        else:
            return result
            
    except Exception as e:
        logger.error(f"List workflows error: {e}")
        return {"ok": False, "where": "list_workflows", "error": str(e)}

@app.get("/n8n/workflows/{workflow_id}")
async def n8n_get_workflow(workflow_id: str, authorization: Optional[str] = Header(None)):
    """워크플로우 상세"""
    try:
        _auth_or_anon(authorization)
        
        client = await _n8n_client()
        if not client:
            return {"ok": False, "error": "n8n not configured"}
        
        result = await client.get_workflow(workflow_id)
        await client.close()
        return result
        
    except Exception as e:
        logger.error(f"Get workflow error: {e}")
        return {"ok": False, "where": f"get_workflow.{workflow_id}", "error": str(e)}

@app.post("/n8n/workflows")
async def n8n_create_workflow(payload: dict, authorization: Optional[str] = Header(None)):
    """워크플로우 생성"""
    try:
        _auth_or_anon(authorization)
        
        client = await _n8n_client()
        if not client:
            return {"ok": False, "error": "n8n not configured"}
        
        result = await client.create_workflow(payload)
        await client.close()
        return result
        
    except Exception as e:
        logger.error(f"Create workflow error: {e}")
        return {"ok": False, "where": "create_workflow", "error": str(e)}

@app.put("/n8n/workflows/{workflow_id}")
async def n8n_update_workflow(workflow_id: str, payload: dict, authorization: Optional[str] = Header(None)):
    """워크플로우 수정"""
    try:
        _auth_or_anon(authorization)
        
        client = await _n8n_client()
        if not client:
            return {"ok": False, "error": "n8n not configured"}
        
        result = await client.update_workflow(workflow_id, payload)
        await client.close()
        return result
        
    except Exception as e:
        logger.error(f"Update workflow error: {e}")
        return {"ok": False, "where": f"update_workflow.{workflow_id}", "error": str(e)}

@app.post("/n8n/workflows/{workflow_id}/activate")
async def n8n_activate_workflow(workflow_id: str, authorization: Optional[str] = Header(None)):
    """워크플로우 활성화"""
    try:
        _auth_or_anon(authorization)
        
        client = await _n8n_client()
        if not client:
            return {"ok": False, "error": "n8n not configured"}
        
        result = await client.activate_workflow(workflow_id)
        await client.close()
        return result
        
    except Exception as e:
        logger.error(f"Activate workflow error: {e}")
        return {"ok": False, "where": f"activate.{workflow_id}", "error": str(e)}

@app.post("/n8n/workflows/{workflow_id}/deactivate")
async def n8n_deactivate_workflow(workflow_id: str, authorization: Optional[str] = Header(None)):
    """워크플로우 비활성화"""
    try:
        _auth_or_anon(authorization)
        
        client = await _n8n_client()
        if not client:
            return {"ok": False, "error": "n8n not configured"}
        
        result = await client.deactivate_workflow(workflow_id)
        await client.close()
        return result
        
    except Exception as e:
        logger.error(f"Deactivate workflow error: {e}")
        return {"ok": False, "where": f"deactivate.{workflow_id}", "error": str(e)}

@app.delete("/n8n/workflows/{workflow_id}")
async def n8n_delete_workflow(workflow_id: str, authorization: Optional[str] = Header(None)):
    """워크플로우 삭제"""
    try:
        _auth_or_anon(authorization)
        
        client = await _n8n_client()
        if not client:
            return {"ok": False, "error": "n8n not configured"}
        
        result = await client.delete_workflow(workflow_id)
        await client.close()
        return result
        
    except Exception as e:
        logger.error(f"Delete workflow error: {e}")
        return {"ok": False, "where": f"delete.{workflow_id}", "error": str(e)}

@app.post("/n8n/workflows/{workflow_id}/test")
async def n8n_test_workflow(workflow_id: str, body: Optional[dict] = None, authorization: Optional[str] = Header(None)):
    """워크플로우 테스트 실행"""
    try:
        _auth_or_anon(authorization)
        
        client = await _n8n_client()
        if not client:
            return {"ok": False, "error": "n8n not configured"}
        
        result = await client.run_workflow_once(workflow_id, run_data=body or {})
        await client.close()
        return result
        
    except Exception as e:
        logger.error(f"Test workflow error: {e}")
        return {"ok": False, "where": f"test.{workflow_id}", "error": str(e)}

# ── Railway n8n Controller Test Endpoints ──

@app.get("/test/n8n/start")
async def test_n8n_start(authorization: Optional[str] = Header(None)):
    """n8n 서비스 시작 테스트"""
    try:
        _auth_or_anon(authorization)
        
        if not (RAILWAY_API_TOKEN and N8N_SERVICE_ID):
            return {
                "ok": False,
                "error": "Railway controller not configured",
                "missing": {
                    "RAILWAY_API_TOKEN": not bool(RAILWAY_API_TOKEN),
                    "N8N_SERVICE_ID": not bool(N8N_SERVICE_ID)
                }
            }
        
        result = start_n8n()
        return {"ok": True, "result": result, "status": get_service_status()}
        
    except Exception as e:
        logger.error(f"Test start error: {e}")
        return {"ok": False, "error": str(e)}

@app.get("/test/n8n/stop")
async def test_n8n_stop(authorization: Optional[str] = Header(None)):
    """n8n 서비스 중단 테스트"""
    try:
        _auth_or_anon(authorization)
        
        if not (RAILWAY_API_TOKEN and N8N_SERVICE_ID):
            return {
                "ok": False,
                "error": "Railway controller not configured",
                "missing": {
                    "RAILWAY_API_TOKEN": not bool(RAILWAY_API_TOKEN),
                    "N8N_SERVICE_ID": not bool(N8N_SERVICE_ID)
                }
            }
        
        result = stop_n8n()
        return {"ok": True, "result": result, "status": get_service_status()}
        
    except Exception as e:
        logger.error(f"Test stop error: {e}")
        return {"ok": False, "error": str(e)}

@app.get("/test/n8n/status")
async def test_n8n_status(authorization: Optional[str] = Header(None)):
    """n8n 서비스 상태 확인"""
    try:
        _auth_or_anon(authorization)
        
        status = get_service_status()
        railway_ok = False
        
        if RAILWAY_API_TOKEN and N8N_SERVICE_ID:
            railway_ok = test_railway_connection()
        
        return {
            "ok": True,
            "service_status": status,
            "railway_connection": railway_ok,
            "configuration": {
                "railway_token_set": bool(RAILWAY_API_TOKEN),
                "service_id_set": bool(N8N_SERVICE_ID),
                "n8n_api_configured": _assert_n8n_ready()
            }
        }
        
    except Exception as e:
        logger.error(f"Test status error: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/test/n8n/workflow/{workflow_id}")
async def test_n8n_workflow_with_controller(
    workflow_id: str,
    payload: dict = {},
    authorization: Optional[str] = Header(None)
):
    """n8n 워크플로우 실행 테스트 (자동 start/stop)"""
    try:
        _auth_or_anon(authorization)
        
        if not (RAILWAY_API_TOKEN and N8N_SERVICE_ID):
            return {
                "ok": False,
                "error": "Railway controller not configured",
                "hint": "Set RAILWAY_API_TOKEN and N8N_SERVICE_ID"
            }
        
        logger.info(f"Testing workflow {workflow_id} with auto start/stop")
        result = use_n8n_workflow(workflow_id, payload)
        
        return {
            "ok": True,
            "workflow_result": result,
            "service_status": get_service_status()
        }
        
    except Exception as e:
        logger.error(f"Test workflow error: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/webhook/{workflow_id}")
async def controlled_webhook(
    workflow_id: str,
    request: Request,
    authorization: Optional[str] = Header(None)
):
    """
    n8n webhook 프록시 - 자동으로 n8n을 시작하고 webhook을 실행한 후 종료
    이 엔드포인트를 통해 외부 서비스가 n8n webhook을 호출할 때
    자동으로 n8n 서비스가 관리됩니다.
    """
    try:
        _auth_or_anon(authorization)
        
        # Get request body
        body = await request.json() if request.headers.get("content-type") == "application/json" else {}
        
        # Use Railway controller if configured
        if RAILWAY_API_TOKEN and N8N_SERVICE_ID:
            logger.info(f"Controlled webhook execution for workflow: {workflow_id}")
            
            try:
                # This will automatically start/stop n8n
                result = use_n8n_workflow(workflow_id, body)
                return {
                    "ok": True,
                    "result": result,
                    "controlled": True,
                    "message": "Workflow executed with automatic n8n lifecycle management"
                }
            except Exception as e:
                logger.error(f"Controlled webhook execution failed: {e}")
                return {
                    "ok": False,
                    "error": str(e),
                    "controlled": True
                }
        else:
            # Fallback: Direct webhook call (n8n must be running)
            logger.warning(f"Railway controller not configured, attempting direct webhook call to workflow: {workflow_id}")
            
            import aiohttp
            webhook_url = f"{N8N_PROTOCOL}://{N8N_HOST}/webhook/{workflow_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=body) as response:
                    result = await response.json()
                    return {
                        "ok": response.status == 200,
                        "result": result,
                        "controlled": False,
                        "message": "Direct webhook call (n8n must be running manually)"
                    }
    except Exception as e:
        logger.error(f"Webhook proxy error: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/batch/workflows")
async def batch_workflows_execution(
    workflows: list,
    authorization: Optional[str] = Header(None)
):
    """
    여러 워크플로우를 배치로 실행 (n8n은 한 번만 시작/종료)
    
    Request body example:
    {
        "workflows": [
            {"id": "workflow1", "payload": {...}},
            {"id": "workflow2", "payload": {...}}
        ]
    }
    """
    try:
        _auth_or_anon(authorization)
        
        if not (RAILWAY_API_TOKEN and N8N_SERVICE_ID):
            return {
                "ok": False,
                "error": "Railway controller not configured",
                "hint": "Set RAILWAY_API_TOKEN and N8N_SERVICE_ID"
            }
        
        results = []
        
        # Use batch operation context manager
        with N8NBatchOperation():
            for workflow in workflows:
                workflow_id = workflow.get("id")
                payload = workflow.get("payload", {})
                
                try:
                    logger.info(f"Executing workflow in batch: {workflow_id}")
                    # Use keep_alive=True to prevent stopping n8n between workflows
                    result = use_n8n_workflow(workflow_id, payload, keep_alive=True)
                    results.append({
                        "workflow_id": workflow_id,
                        "ok": True,
                        "result": result
                    })
                except Exception as e:
                    logger.error(f"Batch workflow {workflow_id} failed: {e}")
                    results.append({
                        "workflow_id": workflow_id,
                        "ok": False,
                        "error": str(e)
                    })
        
        success_count = sum(1 for r in results if r.get("ok"))
        total_count = len(results)
        
        return {
            "ok": success_count > 0,
            "summary": f"{success_count}/{total_count} workflows executed",
            "results": results
        }
        
    except Exception as e:
        logger.error(f"Batch execution error: {e}")
        return {"ok": False, "error": str(e)}

@app.get("/n8n/executions")
async def n8n_list_executions(workflowId: Optional[str] = None, limit: int = 20, authorization: Optional[str] = Header(None)):
    """실행 이력"""
    try:
        _auth_or_anon(authorization)
        
        client = await _n8n_client()
        if not client:
            return {"ok": False, "error": "n8n not configured"}
        
        result = await client.list_executions(workflow_id=workflowId, limit=limit)
        await client.close()
        
        if result.get("ok"):
            return {"ok": True, "items": result.get("data", [])}
        else:
            return result
            
    except Exception as e:
        logger.error(f"List executions error: {e}")
        return {"ok": False, "where": "list_executions", "error": str(e)}

# 서버 시작시 로그
@app.on_event("startup")
async def startup_event():
    logger.info(f"CaiaAgent Core v3.1.0 started")
    logger.info(f"n8n configured: {_assert_n8n_ready()}")
    if N8N_API_URL:
        logger.info(f"n8n URL: {N8N_API_URL}")
    
    # Railway controller status
    if RAILWAY_API_TOKEN and N8N_SERVICE_ID:
        logger.info("✅ Railway n8n controller configured")
        logger.info(f"n8n service ID: {N8N_SERVICE_ID}")
    else:
        logger.warning("⚠️ Railway n8n controller not configured - n8n will run continuously")
        if not RAILWAY_API_TOKEN:
            logger.warning("Missing RAILWAY_API_TOKEN")
        if not N8N_SERVICE_ID:
            logger.warning("Missing N8N_SERVICE_ID")
