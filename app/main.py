import os
import time
from datetime import datetime, timezone
from typing import Optional, Any, Dict

import httpx
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from .security import check_bearer

SERVICE_NAME = "CaiaAgent Core"
VERSION = "1.0.1"

CAIA_PORT = int(os.getenv("CAIA_PORT", "8080"))
N8N_WEBHOOK_BASE = os.getenv("N8N_WEBHOOK_BASE", "").rstrip("/")
N8N_UI_URL = os.getenv("N8N_UI_URL", "").rstrip("/")
CAIA_AGENT_KEY = os.getenv("CAIA_AGENT_KEY", "")
DEFAULT_WEBHOOK_PATH = os.getenv("DEFAULT_WEBHOOK_PATH", "").lstrip("/")
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

app = FastAPI(
    title="CaiaAgent Core API",
    version=VERSION,
    openapi_version="3.1.0",
    docs_url="/agent/docs",
    redoc_url="/agent/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

START_TIME = time.time()

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

@app.get("/", operation_id="getRoot", summary="Service info")
async def root():
    if N8N_UI_URL:
        return RedirectResponse(url=N8N_UI_URL, status_code=302)
    return {
        "service": SERVICE_NAME,
        "version": VERSION,
        "status": "ok",
        "port": CAIA_PORT,
    }

@app.get("/health", operation_id="getHealth", summary="Health check")
async def health():
    uptime = time.time() - START_TIME
    return {
        "status": "healthy",
        "version": VERSION,
        "timestamp": now_iso(),
        "port": CAIA_PORT,
        "uptime": f"{uptime:.1f}s",
    }

@app.get("/ping", operation_id="getPing", summary="Ping test")
async def ping():
    return {"pong": True, "timestamp": now_iso(), "port": CAIA_PORT}

@app.get("/debug", operation_id="getDebug", summary="Debug information")
async def debug():
    env_names = [
        "CAIA_PORT",
        "N8N_WEBHOOK_BASE",
        "N8N_UI_URL",
        "DEFAULT_WEBHOOK_PATH",
        "LOG_LEVEL",
    ]
    env = {k: os.getenv(k, "") for k in env_names}
    return {
        "service": SERVICE_NAME,
        "version": VERSION,
        "port": CAIA_PORT,
        "environment": env,
        "endpoints": [
            "/", "/health", "/ping", "/debug",
            "/agent/status", "/agent/health",
            "/agent/orchestrate", "/agent/proxy/n8n/{path}"
        ],
    }

@app.get("/agent/status", operation_id="getAgentStatus")
async def agent_status():
    return {"status": "ok", "version": VERSION, "n8n_base": N8N_WEBHOOK_BASE or None}

@app.get("/agent/health", operation_id="getAgentHealth")
async def agent_health():
    return {"status": "healthy", "timestamp": now_iso(), "port": CAIA_PORT}

@app.post("/agent/orchestrate", operation_id="postAgentOrchestrate")
async def post_orchestrate(request: Request, authorization: Optional[str] = Header(default=None)):
    check_bearer(authorization, CAIA_AGENT_KEY)

    content_type = request.headers.get("content-type", "")
    body: Dict[str, Any] = {}
    if "application/json" in content_type.lower():
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
    elif "text/plain" in content_type.lower():
        text = await request.body()
        body = {"message": text.decode("utf-8", errors="ignore")}
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}

    # webhook path 결정
    webhook_path = (body.get("webhook_path") or body.get("path") or DEFAULT_WEBHOOK_PATH).lstrip("/")
    method = str(body.get("method") or "POST").upper()
    payload = body.get("payload")

    if not webhook_path:
        return JSONResponse(
            status_code=400,
            content={"error": "webhook_path is required", "hint": "set webhook_path in body or DEFAULT_WEBHOOK_PATH env"}
        )

    if not N8N_WEBHOOK_BASE:
        return JSONResponse(
            status_code=500,
            content={"error": "N8N_WEBHOOK_BASE not configured", "hint": "set N8N_WEBHOOK_BASE env to your n8n domain"}
        )

    target_url = f"{N8N_WEBHOOK_BASE}/{webhook_path}"
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            if method in ["GET", "DELETE"]:
                resp = await client.request(method, target_url, params=payload if isinstance(payload, dict) else None)
            else:
                resp = await client.request(method, target_url, json=payload if payload is not None else {})
        try:
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except Exception:
            return PlainTextResponse(resp.text, status_code=resp.status_code)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": f"Proxy to n8n failed", "detail": str(e)})


from fastapi import Path as FPath
@app.api_route("/agent/proxy/n8n/{path:path}", methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"], include_in_schema=False)
async def proxy_n8n(path: str = FPath(..., description="path after base"),
                    request: Request = None,
                    authorization: Optional[str] = Header(default=None)):
    check_bearer(authorization, CAIA_AGENT_KEY)

    if not N8N_WEBHOOK_BASE:
        raise HTTPException(status_code=500, detail="N8N_WEBHOOK_BASE is not configured")

    target_url = f"{N8N_WEBHOOK_BASE}/{path}".rstrip("/")
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    method = request.method.upper()

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            if method in ["GET", "DELETE"]:
                resp = await client.request(method, target_url, params=request.query_params, headers=headers)
            else:
                body = await request.body()
                resp = await client.request(method, target_url, params=request.query_params, headers=headers, content=body)
        try:
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except Exception:
            return PlainTextResponse(resp.text, status_code=resp.status_code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Proxy error: {e}")
