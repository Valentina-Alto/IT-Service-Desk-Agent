"""
MCP Server wrapper for the AI Service Desk Diagnostic tool.

Exposes the same LLM-powered PowerShell diagnostic capability from test3.py
as an MCP tool over Streamable HTTP transport.

Run:
    python mcp_server.py

Then connect with MCP Inspector:
    npx -y @modelcontextprotocol/inspector
    -> connect to http://localhost:8000/mcp
"""

import contextlib
import json
import os
import subprocess

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from openai import OpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

# ---------------------------------------------------------------------------
# Azure OpenAI configuration (same as test3.py)
# Lazily initialized to avoid blocking server startup with token acquisition.
# ---------------------------------------------------------------------------
AZURE_OPENAI_ENDPOINT = os.environ.get(
    "AZURE_OPENAI_ENDPOINT",
    "https://your-azure-openai-resource.openai.azure.com/openai/v1/",
)
DEPLOYMENT_NAME = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

_client: OpenAI | None = None


def get_openai_client() -> OpenAI:
    """Return a cached OpenAI client, initializing on first call."""
    global _client
    if _client is None:
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        )
        _client = OpenAI(
            base_url=AZURE_OPENAI_ENDPOINT,
            api_key=token_provider,
        )
    return _client

# ---------------------------------------------------------------------------
# System prompt for PowerShell generation
# ---------------------------------------------------------------------------
PWSH_SYSTEM_PROMPT = """You are an expert PowerShell command generator for Windows system diagnostics.
Generate ONLY valid PowerShell commands that are safe and read-only (no modifications).

CRITICAL: Always output VALID JSON by piping to ConvertTo-Json.

Template:
@{
    result = (your command here)
} | ConvertTo-Json -Depth 10

Requirements:
- Wrap ALL results in @{ } | ConvertTo-Json
- Use -Depth 10 for nested objects
- Use proper Select-Object for filtering
- No destructive operations
- Return ONLY the PowerShell script, no explanations or comments"""

# ---------------------------------------------------------------------------
# Core helpers (ported from test3.py, without FastAPI dependency)
# ---------------------------------------------------------------------------

def generate_powershell_command(request_description: str) -> str:
    """Use LLM to generate a safe PowerShell command based on the request."""
    try:
        completion = get_openai_client().chat.completions.create(
            model=DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": PWSH_SYSTEM_PROMPT},
                {"role": "user", "content": f"Generate a PowerShell command to: {request_description}"},
            ],
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        raise RuntimeError(f"LLM generation failed: {e}")


def run_powershell(script: str) -> dict:
    """Execute a PowerShell script and return the parsed result."""
    try:
        completed = subprocess.run(
            ["powershell", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if completed.returncode != 0:
            return {"error": completed.stderr.strip(), "script_used": script}

        output = completed.stdout.strip()
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {"raw_output": output, "script_used": script}
    except subprocess.TimeoutExpired:
        return {"error": "Command execution timed out", "script_used": script}
    except Exception as e:
        return {"error": str(e), "script_used": script}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "Service Desk Diagnostics",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
)


@mcp.tool()
def diagnose(query: str) -> dict:
    """Run an AI-powered Windows system diagnostic.

    Accepts a natural-language description of a diagnostic task (e.g.
    "check disk space", "list running services", "show CPU usage").
    An LLM generates a safe, read-only PowerShell command which is then
    executed locally, and the result is returned.

    Args:
        query: A natural-language description of the diagnostic to perform.
               Must be at least 3 characters.
    """
    if not query or len(query) < 3:
        return {"error": "query must be at least 3 characters"}

    script = generate_powershell_command(query)
    result = run_powershell(script)
    return {"result": result}


@mcp.tool()
def health_check() -> dict:
    """Check if the Service Desk Diagnostics MCP server is running."""
    return {
        "status": "running",
        "version": "2.0",
        "transport": "streamable-http",
    }


# ---------------------------------------------------------------------------
# ASGI app with CORS (required for browser-based MCP Inspector)
# ---------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    # Pre-warm the Azure credential + OpenAI client so first tool call is fast
    print("Pre-warming Azure OpenAI client...")
    get_openai_client()
    print("Azure OpenAI client ready.")
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Mount("/", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)

app = CORSMiddleware(
    app,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
