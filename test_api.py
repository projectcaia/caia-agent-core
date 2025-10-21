#!/usr/bin/env python3
"""
CaiaAgent Core API 테스트 스크립트
"""
import httpx
import json
import sys
import os
from datetime import datetime

# API 설정
BASE_URL = os.getenv("CAIA_CORE_URL", "http://localhost:8080")
API_KEY = os.getenv("N8N_API_KEY", "")
CAIA_KEY = os.getenv("CAIA_AGENT_KEY", "")

def print_section(title):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print(f"{'='*50}")

def test_endpoint(method, path, data=None, headers=None):
    """엔드포인트 테스트"""
    url = f"{BASE_URL}{path}"
    print(f"\n[{method}] {url}")
    
    try:
        with httpx.Client() as client:
            if method == "GET":
                response = client.get(url, headers=headers)
            elif method == "POST":
                response = client.post(url, json=data, headers=headers)
            elif method == "PUT":
                response = client.put(url, json=data, headers=headers)
            elif method == "DELETE":
                response = client.delete(url, headers=headers)
            else:
                print(f"Unknown method: {method}")
                return None
        
        print(f"Status: {response.status_code}")
        
        # JSON 응답 파싱 시도
        try:
            result = response.json()
            print(f"Response: {json.dumps(result, indent=2, ensure_ascii=False)}")
            return result
        except:
            print(f"Response: {response.text}")
            return response.text
            
    except Exception as e:
        print(f"Error: {e}")
        return None

def main():
    print_section("CaiaAgent Core API Test")
    print(f"Base URL: {BASE_URL}")
    print(f"Time: {datetime.now().isoformat()}")
    
    # 1. 루트 엔드포인트 테스트
    print_section("1. Root Endpoint Test")
    test_endpoint("GET", "/")
    
    # 2. 헬스체크 테스트
    print_section("2. Health Check")
    test_endpoint("GET", "/health")
    
    # 3. 상태 확인
    print_section("3. Status Check")
    status = test_endpoint("GET", "/status")
    
    if status and isinstance(status, dict):
        if status.get("n8n_configured"):
            print("\n✅ n8n is configured!")
        else:
            print("\n⚠️  n8n is NOT configured - please set N8N_API_URL and N8N_API_KEY")
    
    # 4. API 문서 확인
    print_section("4. API Documentation")
    print(f"Interactive Docs: {BASE_URL}/docs")
    print(f"OpenAPI Schema: {BASE_URL}/openapi.json")
    
    # 5. n8n 워크플로우 목록 (n8n이 설정된 경우)
    if status and isinstance(status, dict) and status.get("n8n_configured"):
        print_section("5. n8n Workflows List")
        headers = {}
        if CAIA_KEY:
            headers["Authorization"] = f"Bearer {CAIA_KEY}"
        
        workflows = test_endpoint("GET", "/n8n/workflows", headers=headers)
        
        if workflows and isinstance(workflows, dict) and workflows.get("ok"):
            items = workflows.get("items", [])
            print(f"\nFound {len(items)} workflow(s):")
            for wf in items:
                active = "✅" if wf.get("active") else "❌"
                print(f"  {active} [{wf.get('id')}] {wf.get('name')}")
    
    # 6. 오케스트레이션 테스트
    print_section("6. Orchestration Test")
    test_data = {
        "message": "Test orchestration request",
        "trigger_type": "api_test",
        "metadata": {
            "source": "test_api.py",
            "timestamp": datetime.now().isoformat()
        }
    }
    test_endpoint("POST", "/orchestrate", data=test_data)
    
    print_section("Test Complete!")
    
    # 요약
    print("\n📊 Summary:")
    print(f"  - Server: {'Running' if status else 'Not responding'}")
    if status and isinstance(status, dict):
        print(f"  - n8n: {'Configured ✅' if status.get('n8n_configured') else 'Not configured ⚠️'}")
        print(f"  - Memory Count: {status.get('memory_count', 0)}")
        print(f"  - Status: {status.get('status', 'unknown')}")
    
    print("\n💡 Tips:")
    print("  - Check /docs for interactive API documentation")
    print("  - Use /n8n/bootstrap to create standard workflows")
    print("  - Monitor /health for service availability")

if __name__ == "__main__":
    main()