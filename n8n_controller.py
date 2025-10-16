"""
N8N Controller Module for CaiaAgent
Manages n8n service lifecycle through Railway GraphQL API
Only starts n8n when needed, stops after use to minimize costs
"""

import os
import time
import json
import logging
import requests
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Railway API Configuration
RAILWAY_API_URL = "https://backboard.railway.app/graphql"
RAILWAY_TOKEN = os.getenv("RAILWAY_API_TOKEN")
SERVICE_ID = os.getenv("N8N_SERVICE_ID")

# N8N Configuration
N8N_HOST = os.getenv("N8N_HOST", "caia-agent-production.up.railway.app")
N8N_PROTOCOL = os.getenv("N8N_PROTOCOL", "https")
N8N_STARTUP_WAIT = int(os.getenv("N8N_STARTUP_WAIT", "10"))  # Seconds to wait after starting

# Service state tracking
_service_state = {"is_running": False, "last_started": None, "last_stopped": None}


def railway_graphql(query: str) -> Dict[str, Any]:
    """
    Execute a GraphQL query against Railway API
    
    Args:
        query: GraphQL query string
        
    Returns:
        Response JSON from Railway API
        
    Raises:
        Exception: If Railway API request fails
    """
    if not RAILWAY_TOKEN:
        raise ValueError("RAILWAY_API_TOKEN is not configured")
    
    if not SERVICE_ID:
        raise ValueError("N8N_SERVICE_ID is not configured")
    
    headers = {
        "Authorization": f"Bearer {RAILWAY_TOKEN}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            RAILWAY_API_URL,
            headers=headers,
            json={"query": query},
            timeout=30
        )
        response.raise_for_status()
        
        result = response.json()
        
        # Check for GraphQL errors
        if "errors" in result:
            error_msg = json.dumps(result["errors"])
            logger.error(f"Railway GraphQL error: {error_msg}")
            raise Exception(f"Railway API error: {error_msg}")
        
        return result
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Railway API request failed: {str(e)}")
        raise Exception(f"Failed to communicate with Railway API: {str(e)}")


def start_n8n() -> Dict[str, Any]:
    """
    Start the n8n service on Railway
    Waits for service to be ready before returning
    
    Returns:
        Response from Railway API
    """
    logger.info("🚀 Starting n8n service via Railway API...")
    
    # Check if already running
    if _service_state.get("is_running"):
        logger.info("n8n service is already running, skipping start")
        return {"status": "already_running", "message": "Service was already running"}
    
    try:
        # Start service via Railway GraphQL mutation
        query = f'''
        mutation {{
            serviceStart(id: "{SERVICE_ID}")
        }}
        '''
        
        result = railway_graphql(query)
        
        # Update state
        _service_state["is_running"] = True
        _service_state["last_started"] = time.time()
        
        logger.info(f"n8n service start initiated, waiting {N8N_STARTUP_WAIT} seconds for initialization...")
        
        # Wait for service to be fully ready
        time.sleep(N8N_STARTUP_WAIT)
        
        # Verify service is accessible
        n8n_health_url = f"{N8N_PROTOCOL}://{N8N_HOST}/healthz"
        max_retries = 5
        
        for i in range(max_retries):
            try:
                health_check = requests.get(n8n_health_url, timeout=5)
                if health_check.status_code == 200:
                    logger.info("✅ n8n service is healthy and ready!")
                    break
            except:
                if i < max_retries - 1:
                    logger.info(f"Waiting for n8n to be ready... attempt {i+1}/{max_retries}")
                    time.sleep(3)
                else:
                    logger.warning("n8n health check failed but proceeding anyway")
        
        return {"status": "started", "result": result}
        
    except Exception as e:
        logger.error(f"Failed to start n8n service: {str(e)}")
        _service_state["is_running"] = False
        raise


def stop_n8n() -> Dict[str, Any]:
    """
    Stop the n8n service on Railway to save costs
    
    Returns:
        Response from Railway API
    """
    logger.info("🛑 Stopping n8n service via Railway API...")
    
    # Check if already stopped
    if not _service_state.get("is_running"):
        logger.info("n8n service is already stopped, skipping stop")
        return {"status": "already_stopped", "message": "Service was already stopped"}
    
    try:
        # Stop service via Railway GraphQL mutation
        query = f'''
        mutation {{
            serviceStop(id: "{SERVICE_ID}")
        }}
        '''
        
        result = railway_graphql(query)
        
        # Update state
        _service_state["is_running"] = False
        _service_state["last_stopped"] = time.time()
        
        logger.info("✅ n8n service stopped successfully")
        
        return {"status": "stopped", "result": result}
        
    except Exception as e:
        logger.error(f"Failed to stop n8n service: {str(e)}")
        # Don't update state on failure
        raise


def use_n8n_workflow(workflow_id: str, payload: Dict[str, Any], keep_alive: bool = False) -> Dict[str, Any]:
    """
    Execute an n8n workflow with automatic service management
    Starts n8n, executes workflow, then stops n8n (unless keep_alive is True)
    
    Args:
        workflow_id: The n8n workflow ID or webhook path
        payload: Data to send to the workflow
        keep_alive: If True, don't stop n8n after execution (for batch operations)
        
    Returns:
        Response from the n8n workflow
        
    Raises:
        Exception: If workflow execution fails
    """
    logger.info(f"📋 Executing n8n workflow: {workflow_id}")
    
    workflow_response = None
    error = None
    
    try:
        # Start n8n if not running
        start_n8n()
        
        # Construct webhook URL
        n8n_webhook_url = f"{N8N_PROTOCOL}://{N8N_HOST}/webhook/{workflow_id}"
        logger.info(f"Calling n8n webhook: {n8n_webhook_url}")
        
        # Execute workflow
        response = requests.post(
            n8n_webhook_url,
            json=payload,
            timeout=120  # 2 minutes timeout for workflow execution
        )
        
        response.raise_for_status()
        workflow_response = response.json()
        
        logger.info(f"✅ Workflow executed successfully: {workflow_id}")
        
    except requests.exceptions.RequestException as e:
        error = e
        logger.error(f"Failed to execute workflow {workflow_id}: {str(e)}")
        
    except Exception as e:
        error = e
        logger.error(f"Unexpected error executing workflow {workflow_id}: {str(e)}")
        
    finally:
        # Stop n8n after use (unless keep_alive is set)
        if not keep_alive:
            try:
                stop_n8n()
            except Exception as stop_error:
                logger.error(f"Failed to stop n8n after workflow execution: {str(stop_error)}")
                # Don't raise here - we want to return the workflow response if it succeeded
    
    # Raise the original error if workflow failed
    if error:
        raise error
    
    return workflow_response


def get_service_status() -> Dict[str, Any]:
    """
    Get the current status of the n8n service
    
    Returns:
        Dictionary containing service state information
    """
    return {
        "is_running": _service_state.get("is_running", False),
        "last_started": _service_state.get("last_started"),
        "last_stopped": _service_state.get("last_stopped"),
        "railway_configured": bool(RAILWAY_TOKEN and SERVICE_ID),
        "n8n_host": N8N_HOST,
        "n8n_protocol": N8N_PROTOCOL
    }


# Batch operation context manager
class N8NBatchOperation:
    """
    Context manager for batch operations that need n8n to stay alive
    Usage:
        with N8NBatchOperation():
            # Multiple workflow calls here
            use_n8n_workflow("workflow1", data1, keep_alive=True)
            use_n8n_workflow("workflow2", data2, keep_alive=True)
        # n8n stops automatically after the context exits
    """
    
    def __enter__(self):
        logger.info("Starting n8n batch operation")
        start_n8n()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        logger.info("Ending n8n batch operation")
        try:
            stop_n8n()
        except Exception as e:
            logger.error(f"Failed to stop n8n after batch operation: {str(e)}")
        return False  # Don't suppress exceptions


# Test functions for development
def test_railway_connection() -> bool:
    """
    Test if Railway API connection is working
    
    Returns:
        True if connection is successful
    """
    try:
        # Simple query to test connection
        query = '''
        query {
            me {
                id
            }
        }
        '''
        result = railway_graphql(query)
        logger.info("✅ Railway API connection test successful")
        return True
    except Exception as e:
        logger.error(f"❌ Railway API connection test failed: {str(e)}")
        return False


if __name__ == "__main__":
    # Test module functionality
    logging.basicConfig(level=logging.INFO)
    
    print("Testing n8n Controller Module...")
    print(f"Railway Token configured: {bool(RAILWAY_TOKEN)}")
    print(f"Service ID configured: {bool(SERVICE_ID)}")
    print(f"n8n Host: {N8N_HOST}")
    
    if RAILWAY_TOKEN and SERVICE_ID:
        print("\nTesting Railway connection...")
        if test_railway_connection():
            print("\nCurrent service status:")
            print(json.dumps(get_service_status(), indent=2))