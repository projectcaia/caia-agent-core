from fastapi import FastAPI, Request
from datetime import datetime, timezone
from memory import MemoryManager
from decision import DecisionEngine

app = FastAPI(title="CaiaAgent Core", version="3.0.0")

memory = MemoryManager()
decision_engine = DecisionEngine()

@app.get("/status")
async def status():
    return {
        "status": "conscious",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "memory_count": await memory.count(),
        "decision_capability": decision_engine.get_capabilities()
    }

@app.get("/health")
async def health():
    return {
        "ok": True,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.post("/orchestrate")
async def orchestrate(request: Request):
    body = await request.json()
    context = await memory.recall(body.get("message", ""))
    decision = await decision_engine.decide(
        message=body.get("message"),
        context=context,
        trigger_type=body.get("trigger_type", "unknown"),
        metadata=body.get("metadata", {})
    )
    return {"decision": decision, "memory_context": context}

@app.post("/report")
async def report(request: Request):
    body = await request.json()
    await memory.store(body)
    return {"status": "remembered"}
