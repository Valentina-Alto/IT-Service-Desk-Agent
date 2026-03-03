# 🛠️ Service Desk Diagnostics — MCP Server

An AI-powered IT service desk diagnostic tool exposed as a **Model Context Protocol (MCP)** server. It accepts natural-language queries (e.g. *"Why is my laptop slow?"*), uses an LLM to generate safe, read-only PowerShell commands, executes them, and returns structured diagnostic results.

**The beauty of MCP is that once you build a server, any MCP-compatible client can use it** — whether that's Microsoft Copilot Studio, Azure AI Foundry Agents, a custom agent built with Microsoft Agent Framework, or any other MCP client.

---

## Why MCP?

The [Model Context Protocol](https://github.com/modelcontextprotocol/python-sdk) is an **open standard** that separates *tool/data providers* (servers) from *AI applications* (clients). Think of it like a USB-C port for AI tools:

- **Build once, use everywhere** — your MCP server works with any MCP client
- **Standardized tool discovery** — clients automatically discover available tools, their parameters, and descriptions
- **Transport flexibility** — supports stdio, SSE, and Streamable HTTP
- **No vendor lock-in** — it's an open protocol, not tied to any single framework

This means the same diagnostic server below can be consumed from Copilot Studio, Foundry, Claude, VS Code, or your own custom agent — without changing a single line of server code.

---

## Architecture

![alt text](assets/image2.png)

---

## Quick Start

### 1. Install dependencies

```bash
pip install "mcp[cli]" openai azure-identity
```

### 2. Authenticate with Azure

```bash
az login
```

### 3. Run the MCP server

```bash
python mcp_server.py
```

The server starts at `http://127.0.0.1:8000` using Streamable HTTP transport.

### 4. Test with MCP Inspector

```bash
npx -y @modelcontextprotocol/inspector
```

![alt text](assets/image.png)

Connect to `http://127.0.0.1:8000` in the Inspector UI, then call the `diagnose` tool.

---

## How `mcp_server.py` Works

| Component | Purpose |
|---|---|
| **`FastMCP`** | High-level MCP server from the [Python SDK](https://github.com/modelcontextprotocol/python-sdk) — handles protocol, tool registration, and transport |
| **`diagnose` tool** | Accepts a natural-language query, calls Azure OpenAI to generate a PowerShell command, executes it locally, returns the result |
| **`health_check` tool** | Simple liveness probe |
| **Lazy auth** | Azure AD credentials are acquired once and cached — the server starts instantly |
| **CORS middleware** | Enables browser-based MCP clients (Inspector, web apps) to connect |
| **Stateless HTTP** | Recommended for production; each request is independent — no session state to manage |

### Key code walkthrough

```python
from mcp.server.fastmcp import FastMCP

# Create the MCP server
mcp = FastMCP(
    "Service Desk Diagnostics",
    stateless_http=True,       # No session persistence needed
    json_response=True,        # Return JSON (vs SSE streaming)
    streamable_http_path="/",  # Serve at root path
)

@mcp.tool()
def diagnose(query: str) -> dict:
    """Run an AI-powered Windows system diagnostic."""
    script = generate_powershell_command(query)   # LLM generates PowerShell
    result = run_powershell(script)               # Execute locally
    return {"result": result}
```

That's it — the `@mcp.tool()` decorator handles schema generation, parameter validation, and protocol compliance automatically.

---

## Exposing the MCP Server

Your MCP server runs locally on `http://127.0.0.1:8000`. Cloud-based clients (Foundry, Copilot Studio) need a publicly reachable URL. There are several ways to achieve this:

### Option A: Azure Functions

Package the MCP server as an Azure Function for a serverless, auto-scaling deployment. The [MCP Python SDK supports ASGI](https://github.com/modelcontextprotocol/python-sdk#streamable-http), so it can run inside an Azure Functions HTTP trigger with minimal changes.

- [Azure Functions — Python Developer Guide](https://learn.microsoft.com/azure/azure-functions/functions-reference-python)
- [Azure Functions — HTTP Trigger](https://learn.microsoft.com/azure/azure-functions/functions-bindings-http-webhook-trigger)

### Option B: Azure Container Apps

Containerize the server with Docker and deploy to Azure Container Apps for a fully managed, scalable hosting option with built-in HTTPS and authentication.

```dockerfile
FROM python:3.12-slim
COPY . /app
WORKDIR /app
RUN pip install -r requirements.txt
CMD ["uvicorn", "mcp_server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [Azure Container Apps — Overview](https://learn.microsoft.com/azure/container-apps/overview)
- [Deploy a Container App from a Docker image](https://learn.microsoft.com/azure/container-apps/quickstart-portal)

### Option C: Any cloud infrastructure

Since the MCP server is a standard ASGI app (Starlette + Uvicorn), it can run on virtually any platform that supports Python web apps — AWS Lambda, Google Cloud Run, a VM, Kubernetes, etc.

### Option D: ngrok (demo only)

For quick local development and demos, [ngrok](https://ngrok.com/) creates a secure tunnel from a public HTTPS URL to your local machine — no deployment needed.

```bash
ngrok http 8000
```

This gives you a URL like `https://your-tunnel.ngrok-free.app` that you can plug into any MCP client.

> **This is the approach used in this project** for quick iteration and live demos. Not recommended for production.

- [ngrok — Getting Started](https://ngrok.com/docs/getting-started/)
- [ngrok — Free Plan](https://ngrok.com/pricing)

### Securing your MCP Server with Azure API Management

Whichever deployment option you choose, you can place **Azure API Management** in front of your MCP server to add authentication, rate limiting, and monitoring — with no code changes. APIM has native support for MCP endpoints.

- [Expose an existing MCP server through Azure API Management](https://learn.microsoft.com/azure/api-management/expose-existing-mcp-server)

---

## Consuming the MCP Server

### Option 1: Microsoft Copilot Studio

Add the MCP server as a tool connector in Copilot Studio by pointing it to your APIM endpoint (e.g. `https://<your-apim-instance>.azure-api.net/mcp`). The agent will automatically discover the `diagnose` tool and use it when users ask diagnostic questions.

![alt text](assets/cs.gif)

### Option 2: Azure AI Foundry Agent

In the Foundry Agent playground:
1. Go to **Tools → Add → MCP Server**
2. Enter your APIM endpoint (e.g. `https://<your-apim-instance>.azure-api.net/mcp`)
3. The agent discovers tools automatically and can invoke `diagnose` during conversations

![alt text](assets/foundry.gif)

You can combine it with other tools — knowledge bases, escalation connectors (e.g. Teams), quick-fix manuals — all in the same agent.

### Option 3: Custom Agent with Microsoft Agent Framework

For full control, use the [Microsoft Agent Framework](https://pypi.org/project/agent-framework/) to build a custom agent that connects to the MCP server as a tool.

If your MCP server is exposed through **Azure API Management**, use `HostedMCPTool` with the APIM subscription key in the headers:

```python
from azure.identity import AzureCliCredential
from agent_framework import HostedMCPTool
from agent_framework.azure import AzureOpenAIResponsesClient

# Connect to the MCP server through APIM
apim_headers = {"Ocp-Apim-Subscription-Key": "<your-apim-subscription-key>"}

mcp_tool = HostedMCPTool(
    name="diagnostics",
    url="https://<your-apim-instance>.azure-api.net/mcp",
    headers=apim_headers,
    approval_mode="never_require",
)

# Create the agent
credential = AzureCliCredential()
client = AzureOpenAIResponsesClient(
    endpoint="https://<your-resource>.openai.azure.com/openai/v1/",
    deployment_name="gpt-4o",
    credential=credential,
)

agent = client.create_agent(
    name="ServiceDeskAgent",
    instructions="You are an IT support agent. Use the diagnose tool to investigate system issues.",
    tools=[mcp_tool],
)

result = await agent.run("Why is my laptop running slow?")
print(result)
```

> For direct access without APIM, you can use `MCPStreamableHTTPTool` instead and point it directly at your MCP server URL.

![alt text](assets/maf.gif)

The agent will automatically discover and call the `diagnose` MCP tool, then interpret the PowerShell output for the user.

---

## Tools Exposed

| Tool | Parameters | Description |
|---|---|---|
| `diagnose` | `query: str` | Takes a natural-language diagnostic request, generates and executes a PowerShell command via LLM, returns structured results |
| `health_check` | — | Returns server status and version |

---

## References

- [Model Context Protocol — Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP Specification](https://modelcontextprotocol.io/specification/latest)
- [Microsoft Agent Framework](https://deepwiki.com/microsoft/agent-framework/2-getting-started)
- [Microsoft Foundry](https://learn.microsoft.com/en-us/azure/foundry/?view=foundry-classic)
- [Microsoft AI Foundry UI](https://ai.azure.com)
- [Copilot Studio](https://microsoft.github.io/copilot-studio-resources/)
