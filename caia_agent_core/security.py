from typing import Optional
from fastapi import HTTPException

def check_bearer(token_header: Optional[str], expected: Optional[str]) -> None:
    if not expected:
        return
    if not token_header or not token_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = token_header.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
