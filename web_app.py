"""
AI Service Desk — Web Chat UI

A Flask web app that provides a conversational chat interface to the
MCP-powered diagnostic agent.

Prerequisites:
    1. Start the MCP server:   python mcp_server.py
    2. Start this web app:     python web_app.py
    3. Open http://127.0.0.1:5000 in your browser
"""

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, Response, render_template, request

from azure.identity import AzureCliCredential
from agent_framework import MCPStreamableHTTPTool
from agent_framework.azure import AzureOpenAIResponsesClient
from agent_framework._middleware import FunctionInvocationContext

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AZURE_OPENAI_ENDPOINT = os.environ.get(
    "AZURE_OPENAI_ENDPOINT",
    "https://your-azure-openai-resource.openai.azure.com/openai/v1/",
)
DEPLOYMENT_NAME = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
MCP_SERVER_URL = os.environ.get(
    "MCP_SERVER_URL", "https://your-mcp-url"
)

SYSTEM_INSTRUCTIONS = (
    "You are an expert IT Service Desk agent. Your job is to help users "
    "diagnose and troubleshoot Windows system issues.\n\n"
    "When a user describes a problem, use the diagnostics tool to run "
    "PowerShell-based checks on their machine and interpret the results "
    "in a clear, helpful way.\n\n"
    "Keep your responses concise and actionable. If the diagnostic output "
    "reveals an issue, explain what it means and suggest next steps."
)

# ---------------------------------------------------------------------------
# Shared async event loop (runs in a background thread)
# ---------------------------------------------------------------------------
import threading

_loop = asyncio.new_event_loop()


def _start_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=_start_loop, args=(_loop,), daemon=True).start()

# ---------------------------------------------------------------------------
# Agent setup (created once at startup)
# ---------------------------------------------------------------------------
mcp_tool = MCPStreamableHTTPTool(name="diagnostics", url=MCP_SERVER_URL)

credential = AzureCliCredential()
client = AzureOpenAIResponsesClient(
    endpoint=AZURE_OPENAI_ENDPOINT,
    deployment_name=DEPLOYMENT_NAME,
    credential=credential,
)

agent = client.create_agent(
    name="ServiceDeskAgent",
    instructions=SYSTEM_INSTRUCTIONS,
    tools=[mcp_tool],
)


# ---------------------------------------------------------------------------
# Session store — one thread per browser session
# ---------------------------------------------------------------------------
@dataclass
class ChatSession:
    thread: object = None
    step_events: list = field(default_factory=list)


_sessions: dict[str, ChatSession] = {}


def _get_session(session_id: str) -> ChatSession:
    if session_id not in _sessions:
        _sessions[session_id] = ChatSession(thread=agent.get_new_thread())
    return _sessions[session_id]


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    """SSE endpoint — streams step updates then the final answer."""
    data = request.get_json()
    user_message = data.get("message", "").strip()
    session_id = data.get("session_id", str(uuid.uuid4()))

    if not user_message:
        return Response("data: " + json.dumps({"type": "error", "text": "Empty message"}) + "\n\n",
                        content_type="text/event-stream")

    session = _get_session(session_id)
    step_log: list[dict] = []

    # --- Middleware to capture tool invocations -------------------------
    async def tool_middleware(
        context: FunctionInvocationContext, next_fn
    ) -> None:
        func_name = str(context.function.name) if hasattr(context.function, "name") else str(context.function)
        args_str = str(context.arguments) if context.arguments else ""

        step_log.append({
            "type": "step",
            "status": "calling",
            "tool": func_name,
            "args": args_str,
        })

        await next_fn(context)

        result_preview = str(context.result)[:500] if context.result else ""
        step_log.append({
            "type": "step",
            "status": "completed",
            "tool": func_name,
            "result": result_preview,
        })

    # --- Run agent in the background loop ------------------------------
    async def _run():
        return await agent.run(
            user_message,
            thread=session.thread,
            middleware=tool_middleware,
        )

    def generate():
        # Send session id
        yield "data: " + json.dumps({"type": "session", "session_id": session_id}) + "\n\n"

        # Send "thinking" indicator
        yield "data: " + json.dumps({"type": "step", "status": "thinking"}) + "\n\n"

        # Run the agent
        future = asyncio.run_coroutine_threadsafe(_run(), _loop)

        # Poll for step updates while the agent is working
        import time
        last_sent = 0
        while not future.done():
            while last_sent < len(step_log):
                yield "data: " + json.dumps(step_log[last_sent]) + "\n\n"
                last_sent += 1
            time.sleep(0.3)

        # Flush remaining steps
        while last_sent < len(step_log):
            yield "data: " + json.dumps(step_log[last_sent]) + "\n\n"
            last_sent += 1

        # Get the final result
        try:
            result = future.result()
            yield "data: " + json.dumps({"type": "answer", "text": str(result)}) + "\n\n"
        except Exception as e:
            yield "data: " + json.dumps({"type": "error", "text": str(e)}) + "\n\n"

        yield "data: " + json.dumps({"type": "done"}) + "\n\n"

    return Response(generate(), content_type="text/event-stream")


@app.route("/reset", methods=["POST"])
def reset():
    """Reset conversation for a session."""
    data = request.get_json() or {}
    session_id = data.get("session_id")
    if session_id and session_id in _sessions:
        del _sessions[session_id]
    return {"ok": True}


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"  MCP Server : {MCP_SERVER_URL}")
    print(f"  Model      : {DEPLOYMENT_NAME}")
    print(f"  Open       : http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
