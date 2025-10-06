import os
import time
import json
import uuid
from dotenv import load_dotenv
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
import os
import uuid
import time

# Load .env variables with error handling
try:
    load_dotenv()
except Exception as e:
    print(f"Warning: Could not load .env file: {e}")
    print("Continuing with system environment variables...")

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# Azure Configuration - Direct values with defaults
AZURE_ENDPOINT = os.getenv("AI_FOUNDRY_PROJECT_ENDPOINT", "")
AGENT_ID = os.getenv("AZURE_AGENT_ID", "")
SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "")
RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP", "")
PROJECT_NAME = os.getenv("AZURE_PROJECT", "")
DATABASE_NAME = os.getenv("KQL_DATABASE", "")
TABLE_NAME = os.getenv("KQL_TABLE_NAME", "")

# Kusto client setup - using consistent FABRIC_ prefix for authentication
KUSTO_CLUSTER = os.getenv("KQL_CLUSTER", "")
KUSTO_CLIENT_ID = os.getenv("FABRIC_CLIENT_ID", "")
KUSTO_CLIENT_SECRET = os.getenv("FABRIC_CLIENT_SECRET", "")
KUSTO_TENANT_ID = os.getenv("FABRIC_TENANT_ID", "")

def get_kusto_client():
    kcsb = KustoConnectionStringBuilder.with_aad_application_key_authentication(
        KUSTO_CLUSTER,
        KUSTO_CLIENT_ID,
        KUSTO_CLIENT_SECRET,
        KUSTO_TENANT_ID
    )
    return KustoClient(kcsb)


# Check if required environment variables are set
if not AZURE_ENDPOINT:
    print("Warning: AI_FOUNDRY_PROJECT_ENDPOINT not set")
if not AGENT_ID:
    print("Warning: AZURE_AGENT_ID not set")
if not SUBSCRIPTION_ID:
    print("Warning: AZURE_SUBSCRIPTION_ID not set")
if not RESOURCE_GROUP:
    print("Warning: AZURE_RESOURCE_GROUP not set")
if not PROJECT_NAME:
    print("Warning: AZURE_PROJECT not set")

print(f"[CONFIG] Using configuration:")
print(f"   Endpoint: {AZURE_ENDPOINT}")
print(f"   Agent ID: {AGENT_ID}")
print(f"   Project: {PROJECT_NAME}")
print(f"   Subscription: {SUBSCRIPTION_ID}")
print(f"   Resource Group: {RESOURCE_GROUP}")

# Global thread storage - reuse the same thread for all conversations
CURRENT_THREAD = None
CURRENT_THREAD_ID = None

# Job tracking for async operations
ACTIVE_JOBS = {}
JOB_RESULTS = {}

# Thread pool for async operations
THREAD_POOL = ThreadPoolExecutor(max_workers=5)

# -----------------------------
# Async Azure AI SDK Client
# -----------------------------
class AsyncAzureSDKClient:
    """Async Azure AI SDK client with thread management"""
   
    def __init__(self, endpoint, agent_id, subscription_id, resource_group, project_name):
        self.endpoint = endpoint
        self.agent_id = agent_id
        self.subscription_id = subscription_id
        self.resource_group = resource_group
        self.project_name = project_name
       
        # Initialize Azure credentials
        self.credential = DefaultAzureCredential(
            shared_cache_username="jishnu.sreejith@exultpoc.onmicrosoft.com",
            shared_cache_tenant_id="703b9f1b-bad8-40a1-9c42-42ce53d79a3c"
        )
       
        # Initialize AI Project Client with error handling
        try:
            if self.credential is None:
                raise Exception("No valid credentials available")
               
            self.project_client = AIProjectClient(
                endpoint=endpoint,
                credential=self.credential,
                subscription_id=subscription_id,
                resource_group_name=resource_group,
                project_name=project_name
            )
            print("[OK] Async Azure AI SDK Client initialized successfully")
           
        except Exception as e:
            print(f"[ERROR] Failed to initialize Azure AI SDK Client: {e}")
            print("[INFO] Please check your Azure configuration and network connectivity")
            self.project_client = None
   
    def is_initialized(self):
        """Check if the client is properly initialized"""
        return self.project_client is not None and self.credential is not None

    def get_or_create_thread(self):
        """Fetch existing thread first, then reuse or create new one if needed"""
        global CURRENT_THREAD, CURRENT_THREAD_ID
       
        # First, try to fetch existing threads
        try:
            print("[INFO] Fetching existing threads...")
            existing_threads = list(self.project_client.agents.threads.list())
           
            if existing_threads:
                # Use the most recent thread
                latest_thread = existing_threads[0]  # Assuming they're ordered by creation time
                CURRENT_THREAD_ID = latest_thread.id
                CURRENT_THREAD = latest_thread
                print(f"[REUSE] Found and reusing existing thread ID: {latest_thread.id}")
                return latest_thread
            else:
                print("[INFO] No existing threads found")
               
        except Exception as e:
            print(f"[WARN] Could not fetch existing threads: {e}")
       
        # Check if we have a valid cached thread
        if CURRENT_THREAD and CURRENT_THREAD_ID:
            try:
                # Verify the cached thread still exists and is accessible
                self.project_client.agents.threads.get(thread_id=CURRENT_THREAD_ID)
                print(f"[REUSE] Reusing cached thread ID: {CURRENT_THREAD_ID}")
                return CURRENT_THREAD
            except Exception as e:
                print(f"[WARN] Cached thread {CURRENT_THREAD_ID} is invalid: {e}")
                CURRENT_THREAD = None
                CURRENT_THREAD_ID = None
       
        # Create new thread if none exists or existing ones are invalid
        print("[INFO] Creating new thread...")
        try:
            thread = self.project_client.agents.threads.create()
            CURRENT_THREAD_ID = thread.id
            CURRENT_THREAD = thread
            print(f"[OK] Created new thread ID: {thread.id}")
            return thread
        except Exception as e:
            print(f"[ERROR] Failed to create thread: {e}")
            raise e
   
    def list_all_threads(self):
        """List all available threads"""
        try:
            print("📋 Listing all available threads...")
            threads = list(self.project_client.agents.threads.list())
            print(f"Found {len(threads)} thread(s)")
            for i, thread in enumerate(threads):
                print(f"  {i+1}. Thread ID: {thread.id}")
            return threads
        except Exception as e:
            print(f"[ERROR] Failed to list threads: {e}")
            return []

    def create_new_thread(self):
        """Force create a new thread (for fresh conversations)"""
        global CURRENT_THREAD, CURRENT_THREAD_ID
       
        print("[THREAD] Force creating new thread for fresh conversation...")
        try:
            thread = self.project_client.agents.threads.create()
            CURRENT_THREAD_ID = thread.id
            CURRENT_THREAD = thread
            print(f"[OK] Created new thread ID: {thread.id}")
            return thread
        except Exception as e:
            print(f"[ERROR] Failed to create new thread: {e}")
            raise e

    def get_active_runs(self, thread_id):
        """Get active runs on a thread"""
        try:
            runs = list(self.project_client.agents.runs.list(thread_id=thread_id))
            active_runs = [run for run in runs if run.status in ["in_progress", "queued", "requires_action"]]
            return active_runs
        except Exception as e:
            print(f"[WARN] Could not get active runs: {e}")
            return []
   
    def cancel_active_runs(self, thread_id):
        """Cancel all active runs on a thread"""
        try:
            active_runs = self.get_active_runs(thread_id)
            if active_runs:
                print(f"[CANCEL] Canceling {len(active_runs)} active run(s)...")
                for run in active_runs:
                    try:
                        self.project_client.agents.runs.cancel(thread_id=thread_id, run_id=run.id)
                        print(f"[OK] Canceled run {run.id}")
                    except Exception as e:
                        print(f"[WARN] Could not cancel run {run.id}: {e}")
                return len(active_runs)
            return 0
        except Exception as e:
            print(f"[ERROR] Error canceling active runs: {e}")
            return 0
   
    def process_user_query_sync(self, user_query):
        """Process user query using the same method as main1.py"""
        try:
            # Get or create thread
            thread = self.get_or_create_thread()
           
            # Check for active runs before creating new message
            print("[INFO] Checking for active runs on thread...")
            try:
                runs = list(self.project_client.agents.runs.list(thread_id=thread.id))
                active_runs = [run for run in runs if run.status in ["in_progress", "queued", "requires_action"]]
               
                if active_runs:
                    print(f"[WARN] Found {len(active_runs)} active run(s), waiting for completion...")
                    # Wait for active runs to complete
                    for active_run in active_runs:
                        print(f"[WAIT] Waiting for run {active_run.id} (status: {active_run.status}) to complete...")
                        while True:
                            current_run = self.project_client.agents.runs.get(thread_id=thread.id, run_id=active_run.id)
                            if current_run.status in ["succeeded", "failed", "cancelled", "completed"]:
                                print(f"[OK] Run {active_run.id} completed with status: {current_run.status}")
                                break
                            print(f"[WAIT] Run {active_run.id} still {current_run.status}, waiting 1s...")
                            time.sleep(1)  # Optimized: reduced from 2s to 1s
            except Exception as e:
                print(f"[WARN] Could not check for active runs: {e}")
                # Continue anyway, might be a new thread
           
            # Create user message
            message = self.project_client.agents.messages.create(
                thread_id=thread.id,
                role="user",
                content=user_query,
            )
            print(f"[MSG] Created user message ID: {message.id}")

            # Create run
            run = self.project_client.agents.runs.create(thread_id=thread.id, agent_id=self.agent_id)
            print(f"[RUN] Created run ID: {run.id}, initial status: {run.status}")

            # Poll run status with optimized timeout
            max_wait_time = 30  # 30 seconds timeout (optimized from 120s)
            start_time = time.time()
            while True:
                run = self.project_client.agents.runs.get(thread_id=thread.id, run_id=run.id)
               
                # Check for timeout
                elapsed_time = time.time() - start_time
                if elapsed_time > max_wait_time:
                    print(f"[TIMEOUT] Run timeout after {max_wait_time} seconds, canceling...")
                    self.project_client.agents.runs.cancel(thread_id=thread.id, run_id=run.id)
                    break

                # Handle tool approvals
                if run.status == "requires_action" and hasattr(run, 'required_action') and run.required_action:
                    required_action = run.required_action
                    if hasattr(required_action, 'submit_tool_outputs'):
                        tool_calls = required_action.submit_tool_outputs.tool_calls
                        if not tool_calls:
                            print("[ERROR] No tool calls to approve. Canceling run.")
                            self.project_client.agents.runs.cancel(thread_id=thread.id, run_id=run.id)
                            break

                        # Approve tool calls - FIXED: Use tool_approvals parameter like main1.py
                        approvals = [{"tool_call_id": tc.id, "approve": True} for tc in tool_calls]
                        print(f"[OK] Approving {len(approvals)} tool calls...")
                        self.project_client.agents.runs.submit_tool_outputs(
                            thread_id=thread.id,
                            run_id=run.id,
                            tool_approvals=approvals
                        )
                        time.sleep(1)  # Optimized: reduced from 2s to 1s
                        continue

                # Check if run is complete
                if run.status in ["succeeded", "failed", "cancelled", "completed"]:
                    print(f"[OK] Run completed with status: {run.status}")
                    break

                print(f"[WAIT] Run status: {run.status}, waiting 1s...")
                time.sleep(1)  # Optimized: reduced from 2s to 1s

            # Get the response
            if run.status in ["succeeded", "completed"]:
                try:
                    messages = list(self.project_client.agents.messages.list(thread_id=thread.id))
                    assistant_message = ""
                   
                    # Find the latest assistant message
                    for msg in reversed(messages):
                        if msg.role == "assistant" and hasattr(msg, 'content'):
                            content_items = getattr(msg, "content", []) or []
                            for item in content_items:
                                if item.get("type") == "text":
                                    assistant_message = item.get('text', {}).get('value')
                                    break
                            if assistant_message:
                                break
                   
                    return {
                        "assistant_message": assistant_message or "No response received",
                        "status": run.status,
                        "thread_id": thread.id,
                        "run_id": run.id,
                        "processing_time": time.time() - start_time
                    }
                   
                except Exception as e:
                    return {
                        "assistant_message": f"Error retrieving response: {str(e)}",
                        "status": "error",
                        "thread_id": thread.id,
                        "run_id": run.id,
                        "processing_time": time.time() - start_time
                    }
            else:
                return {
                    "assistant_message": f"Run failed with status: {run.status}",
                    "status": run.status,
                    "thread_id": thread.id,
                    "run_id": run.id,
                    "processing_time": time.time() - start_time
                }
               
        except Exception as e:
            return {
                "assistant_message": f"Error processing query: {str(e)}",
                "status": "error",
                "thread_id": None,
                "run_id": None,
                "processing_time": 0
            }
   
    def process_user_query_async(self, user_query, job_id):
        """Process user query asynchronously and store result"""
        try:
            print(f"[ASYNC] Starting async job: {job_id}")
           
            # Update job status
            ACTIVE_JOBS[job_id] = {
                'status': 'processing',
                'started_at': datetime.now().isoformat(),
                'query': user_query,
                'elapsed': 0
            }
           
            # Process the query using sync method
            result = self.process_user_query_sync(user_query)
           
            # Store result
            JOB_RESULTS[job_id] = {
                'content': result["assistant_message"],
                'status': result["status"],
                'elapsed': result["processing_time"],
                'thread_id': result["thread_id"],
                'run_id': result["run_id"],
                'completed_at': datetime.now().isoformat()
            }
           
            # Remove from active jobs
            if job_id in ACTIVE_JOBS:
                del ACTIVE_JOBS[job_id]
           
            print(f"[OK] Async job {job_id} completed")
           
        except Exception as e:
            print(f"[ERROR] Async job {job_id} failed: {e}")
            JOB_RESULTS[job_id] = {
                'error': str(e),
                'status': 'error',
                'elapsed': 0,
                'completed_at': datetime.now().isoformat()
            }
            if job_id in ACTIVE_JOBS:
                del ACTIVE_JOBS[job_id]

# Initialize the client
try:
    azure_sdk_client = AsyncAzureSDKClient(
        endpoint=AZURE_ENDPOINT,
        agent_id=AGENT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
        project_name=PROJECT_NAME
    )
    print("[OK] Async Azure AI SDK client initialized successfully")
except Exception as e:
    print(f"[ERROR] Failed to initialize client: {e}")
    azure_sdk_client = None

# -----------------------------
# API Endpoints
# -----------------------------

@app.route('/api/chat', methods=['POST'])
def chat():
    """Send user question to REAL Azure AI main agent using PURE SDK approach"""
    try:
        data = request.get_json() or {}
        user_message = data.get("message", "").strip()

        if not user_message:
            return jsonify({"error": "Message is required"}), 400

        print(f"[USER] User Question: {user_message}")
        print(f"[AGENT] Sending to REAL Main Agent: {AGENT_ID}")

        if azure_sdk_client and azure_sdk_client.is_initialized():
            try:
                # Process the user query using the same method as main1.py
                result = azure_sdk_client.process_user_query_sync(user_message)
               
                if result["assistant_message"]:
                    print(f"[OK] REAL Azure AI Response: {result['assistant_message'][:100]}...")
                   
                    return jsonify({
                        "user_message": user_message,
                        "assistant_message": {
                            "content": result["assistant_message"]
                        },
                        "status": "success",
                        "thread_id": result["thread_id"],
                        "agent_id": AGENT_ID,
                        "run_id": result["run_id"],
                        "processing_time": f"{result.get('processing_time', 0):.2f}s",
                        "note": "REAL Azure AI agent response - using ASYNC SDK approach!"
                    }), 200
                else:
                    print("[ERROR] No assistant response found")
                    return jsonify({
                        "user_message": user_message,
                        "assistant_message": {"content": "No response received"},
                        "status": "error",
                        "thread_id": result.get("thread_id"),
                        "run_id": result.get("run_id"),
                        "note": "No response from agent"
                    }), 200
                   
            except Exception as e:
                print(f"[ERROR] Error processing query: {e}")
                return jsonify({
                    "user_message": user_message,
                    "assistant_message": {"content": f"Error: {str(e)}"},
                    "status": "error",
                    "note": "Error processing query"
                }), 500
        else:
            return jsonify({
                "user_message": user_message,
                "assistant_message": {"content": "Azure AI SDK not configured"},
                "status": "error",
                "note": "SDK not initialized"
            }), 500
           
    except Exception as e:
        print(f"[ERROR] Chat endpoint error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/chat/async', methods=['POST'])
def chat_async():
    """Start async processing and return job ID immediately"""
    try:
        data = request.get_json() or {}
        user_message = data.get("message", "").strip()
       
        if not user_message:
            return jsonify({"error": "Message is required"}), 400
       
        if not azure_sdk_client or not azure_sdk_client.is_initialized():
            return jsonify({"error": "Azure AI SDK not properly configured or initialized"}), 500

        # Generate unique job ID
        job_id = str(uuid.uuid4())
       
        print(f"[ASYNC] Starting async job {job_id}: {user_message[:50]}...")
       
        # Start processing in background thread
        future = THREAD_POOL.submit(azure_sdk_client.process_user_query_async, user_message, job_id)
       
        return jsonify({
            "job_id": job_id,
            "status": "started",
            "message": "Processing started in background",
            "user_message": user_message,
            "check_url": f"/api/jobs/{job_id}",
            "estimated_time": "1-5 minutes",
            "note": "Using async thread management"
        }), 202
       
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/jobs/<job_id>', methods=['GET'])
def get_job_status(job_id):
    """Get status of async job"""
    if job_id in ACTIVE_JOBS:
        job_info = ACTIVE_JOBS[job_id]
        return jsonify({
            "job_id": job_id,
            "status": "running",
            "current_status": job_info['status'],
            "elapsed": f"{job_info['elapsed']:.1f}s",
            "started_at": job_info['started_at'],
            "query": job_info['query']
        }), 200
   
    elif job_id in JOB_RESULTS:
        result = JOB_RESULTS[job_id]
        return jsonify({
            "job_id": job_id,
            "status": "completed",
            "result": result
        }), 200
   
    else:
        return jsonify({
            "job_id": job_id,
            "status": "not_found",
            "error": "Job not found"
        }), 404

@app.route('/api/jobs/<job_id>/result', methods=['GET'])
def get_job_result(job_id):
    """Get result of completed async job"""
    if job_id in JOB_RESULTS:
        result = JOB_RESULTS[job_id]
        return jsonify({
            "job_id": job_id,
            "status": "completed",
            "assistant_message": {
                "content": result.get('content', result.get('error', 'No result'))
            },
            "processing_time": f"{result.get('elapsed', 0):.2f}s",
            "completed_at": result.get('completed_at'),
            "run_status": result.get('status', 'unknown')
        }), 200
    else:
        return jsonify({
            "job_id": job_id,
            "status": "not_ready",
            "message": "Job not completed yet or not found"
        }), 202

@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    active_runs_count = 0
    client_initialized = False
   
    if azure_sdk_client:
        client_initialized = azure_sdk_client.is_initialized()
        if client_initialized and CURRENT_THREAD_ID:
            try:
                active_runs = azure_sdk_client.get_active_runs(CURRENT_THREAD_ID)
                active_runs_count = len(active_runs)
            except:
                pass
   
    return jsonify({
        "status": "ok",
        "message": "Async Azure AI SDK Server (app18 style)",
        "features": ["Synchronous chat", "Async processing", "Job tracking", "Thread reuse", "Active run management", "Kusto sessions"],
        "agent_id": AGENT_ID,
        "endpoint": AZURE_ENDPOINT,
        "client_available": azure_sdk_client is not None,
        "client_initialized": client_initialized,
        "active_jobs": len(ACTIVE_JOBS),
        "completed_jobs": len(JOB_RESULTS),
        "current_thread_id": CURRENT_THREAD_ID,
        "active_runs": active_runs_count,
     }), 200

@app.route('/api/cancel-runs', methods=['POST'])
def cancel_runs():
    """Cancel all active runs on the current thread"""
    try:
        if not azure_sdk_client:
            return jsonify({"error": "Azure AI SDK not configured"}), 500
       
        if not CURRENT_THREAD_ID:
            return jsonify({"error": "No active thread"}), 400
       
        canceled_count = azure_sdk_client.cancel_active_runs(CURRENT_THREAD_ID)
       
        return jsonify({
            "message": f"Canceled {canceled_count} active run(s)",
            "thread_id": CURRENT_THREAD_ID,
            "canceled_count": canceled_count
        }), 200
       
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/new-thread', methods=['POST'])
def create_new_thread():
    """Create a new thread for fresh conversation"""
    try:
        if not azure_sdk_client:
            return jsonify({"error": "Azure AI SDK not configured"}), 500
       
        old_thread_id = CURRENT_THREAD_ID
        new_thread = azure_sdk_client.create_new_thread()
       
        return jsonify({
            "message": "New thread created successfully",
            "old_thread_id": old_thread_id,
            "new_thread_id": new_thread.id,
            "status": "success"
        }), 200
       
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/threads', methods=['GET'])
def list_threads():
    """List all available threads"""
    try:
        if not azure_sdk_client:
            return jsonify({"error": "Azure AI SDK not configured"}), 500
       
        threads = azure_sdk_client.list_all_threads()
       
        return jsonify({
            "message": "Threads retrieved successfully",
            "thread_count": len(threads),
            "threads": [{"id": thread.id} for thread in threads],
            "current_thread_id": CURRENT_THREAD_ID,
            "status": "success"
        }), 200
       
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- New endpoint: Get recent session IDs from Kusto ---
@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    """Get list of sessions with first question"""
    try:
        if not DATABASE_NAME or not TABLE_NAME:
            return jsonify({"sessions": [], "error": "KQL not configured"}), 200

        kusto_client = get_kusto_client()

        # Query to get first question per session
        query = f"""
        {TABLE_NAME}
        | summarize FirstQuestion = any(Question) by SessionID
        | take 20
        """
        print(f"[SESSIONS] Executing query: {query}")
        response = kusto_client.execute(DATABASE_NAME, query)

        sessions = []
        if response.primary_results and len(response.primary_results) > 0:
            for row in response.primary_results[0]:
                row_dict = row.to_dict()
                session_id = row_dict.get('SessionID')
                first_question = row_dict.get('FirstQuestion', 'No question found')

                # Convert object/list to string if necessary
                if isinstance(first_question, (dict, list)):
                    first_question = str(first_question)

                if session_id:
                    sessions.append({
                        "id": session_id,
                        "title": first_question
                    })

        print(f"[SESSIONS] Found {len(sessions)} sessions from KQL")
        return jsonify({"sessions": sessions}), 200

    except Exception as e:
        print(f"[SESSIONS] Error: {e}")
        return jsonify({"sessions": [], "error": str(e)}), 200

       
    except Exception as e:
        print(f"[SESSIONS] Error: {e}")
        return jsonify({"sessions": [], "error": str(e)}), 200

@app.route('/api/sessions/<session_id>', methods=['GET'])
def get_session_details(session_id):
    """Get details of a specific session (return first question as title)"""
    try:
        if not DATABASE_NAME or not TABLE_NAME:
            return jsonify({"error": "KQL not configured"}), 200

        kusto_client = get_kusto_client()

        # Query to get the earliest question for this session
        query = f"""
        {TABLE_NAME}
        | where SessionID == '{session_id}'
        | order by Timestamp asc
        | take 1
        """

        print(f"[SESSION_DETAILS] Executing query: {query}")
        response = kusto_client.execute(DATABASE_NAME, query)

        if response.primary_results and len(response.primary_results) > 0 and len(response.primary_results[0]) > 0:
            row = response.primary_results[0][0]
            row_dict = row.to_dict()

            first_question = row_dict.get('Question', 'No question found')
            if isinstance(first_question, dict) or isinstance(first_question, list):
                first_question = str(first_question)
                print(first_question)

            session_details = {
                "id": session_id,
                "title": first_question,  # <-- first question as title
                "question": first_question,
                "response": row_dict.get('Response', 'No response found'),
                "timestamp": row_dict.get('Timestamp', 'No timestamp found')
            }

            print(f"[SESSION_DETAILS] Found session: {session_id} with first question: {first_question}")
            return jsonify(session_details), 200
        else:
            return jsonify({"error": f"Session {session_id} not found"}), 404

    except Exception as e:
        print(f"[SESSION_DETAILS] Error: {e}")
        return jsonify({"error": str(e)}), 200


@app.route('/api/kusto/test', methods=['GET'])
def test_kusto_connection():
    """Test Kusto connection and configuration"""
    try:
        config_info = {
            "database_name": DATABASE_NAME or "Not set",
            "table_name": TABLE_NAME or "Not set",
            "cluster": KUSTO_CLUSTER or "Not set",
            "client_id": KUSTO_CLIENT_ID or "Not set",
            "tenant_id": KUSTO_TENANT_ID or "Not set",
            "client_secret": "***" if KUSTO_CLIENT_SECRET else "Not set"
        }
       
        if not DATABASE_NAME or not TABLE_NAME or not KUSTO_CLUSTER:
            return jsonify({
                "status": "not_configured",
                "message": "Missing required Kusto configuration",
                "config": config_info
            }), 200
           
        if not KUSTO_CLIENT_ID or not KUSTO_CLIENT_SECRET or not KUSTO_TENANT_ID:
            return jsonify({
                "status": "auth_not_configured",
                "message": "Missing Kusto authentication credentials",
                "config": config_info
            }), 200
           
        # Test actual connection
        kusto_client = get_kusto_client()
       
        # Test 1: Simple connection test
        test_query1 = f"{TABLE_NAME} | take 1"
        print(f"[KUSTO_TEST] Testing query: {test_query1}")
        response1 = kusto_client.execute(DATABASE_NAME, test_query1)
       
        # Test 2: Check table schema - try multiple methods
        columns = []
       
        # Method 1: getschema
        try:
            schema_query = f"{TABLE_NAME} | getschema | project ColumnName, ColumnType"
            print(f"[KUSTO_TEST] Schema query: {schema_query}")
            response2 = kusto_client.execute(DATABASE_NAME, schema_query)
           
            if response2.primary_results and len(response2.primary_results) > 0:
                for row in response2.primary_results[0]:
                    if 'ColumnName' in row:
                        columns.append(row['ColumnName'])
        except Exception as e:
            print(f"[KUSTO_TEST] getschema failed: {e}")
       
        # Method 2: Sample data to get column names
        if not columns:
            try:
                sample_query = f"{TABLE_NAME} | take 1"
                print(f"[KUSTO_TEST] Sample query: {sample_query}")
                response2b = kusto_client.execute(DATABASE_NAME, sample_query)
               
                if response2b.primary_results and len(response2b.primary_results) > 0 and len(response2b.primary_results[0]) > 0:
                    # Get column names from the first row
                    first_row = response2b.primary_results[0][0]
                    columns = list(first_row.keys())
                    print(f"[KUSTO_TEST] Found columns from sample: {columns}")
            except Exception as e:
                print(f"[KUSTO_TEST] Sample query failed: {e}")
       
        # Test 3: Check for SessionID column specifically - try different variations
        sessionid_variations = ["SessionID", "sessionid", "Session_Id", "session_id", "sessionId"]
        sessionid_results = {}
       
        for variation in sessionid_variations:
            try:
                sessionid_query = f"{TABLE_NAME} | summarize count() by {variation} | take 5"
                print(f"[KUSTO_TEST] Testing column: {variation}")
                response3 = kusto_client.execute(DATABASE_NAME, sessionid_query)
                sessionid_results[variation] = len(response3.primary_results[0]) if response3.primary_results else 0
            except Exception as e:
                sessionid_results[variation] = f"Error: {str(e)}"
                print(f"[KUSTO_TEST] Column {variation} failed: {e}")
       
        # Use the first successful variation for the main test
        main_sessionid_test = sessionid_results
       
        return jsonify({
            "status": "success",
            "message": "Kusto connection successful",
            "config": config_info,
            "tests": {
                "connection": {
                    "query": test_query1,
                    "result_count": len(response1.primary_results[0]) if response1.primary_results else 0
                },
                "schema": {
                    "columns": columns,
                    "column_count": len(columns)
                },
                "sessionid_variations": sessionid_results,
                "recommended_column": next((col for col, result in sessionid_results.items() if isinstance(result, int) and result > 0), None)
            }
        }), 200
       
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Kusto connection failed: {str(e)}",
            "config": config_info,
            "error": str(e)
        }), 200

@app.route('/')
def index():
    """Serve the index page"""
    return render_template('index.html')

if __name__ == '__main__':
    print("[STARTUP] Starting Async Azure AI SDK Server ")
    print("[ENDPOINTS] Available endpoints:")
    print("   - POST /api/chat         - Synchronous chat endpoint")
    print("   - POST /api/chat/async   - Start async processing")
    print("   - GET  /api/jobs/<id>    - Check job status")
    print("   - GET  /api/jobs/<id>/result - Get job result")
    print("   - GET  /api/health       - Health check")
    print("   - POST /api/cancel-runs  - Cancel active runs")
    print("   - POST /api/new-thread   - Create new thread")
    print("   - GET  /api/threads      - List all threads")
    print("   - GET  /api/sessions     - List Kusto sessions")
    print("   - GET  /                 - Index page")
   
    app.run(host='0.0.0.0', port=5000, debug=True)
