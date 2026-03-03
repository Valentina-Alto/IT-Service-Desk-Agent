"""
Interactive Service Desk Agent — powered by Microsoft Agent Framework + MCP.

This script creates a conversational agent that connects to the local MCP
diagnostic server and lets you chat interactively in the terminal.

Prerequisites:
    1. Start the MCP server first:   python mcp_server.py
    2. Then run this agent:          python agent_app.py

The agent will discover the MCP tools automatically and use them when you
ask diagnostic questions (e.g. "Why is my laptop slow?", "Check my disk usage").
Type 'exit' or 'quit' to end the conversation.
"""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from azure.identity import AzureCliCredential
from agent_framework import MCPStreamableHTTPTool
from agent_framework.azure import AzureOpenAIResponsesClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AZURE_OPENAI_ENDPOINT = os.environ.get(
    "AZURE_OPENAI_ENDPOINT",
    "https://your-azure-openai-resource.openai.azure.com/openai/v1/",
)
DEPLOYMENT_NAME = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "https://your-mcp-url")

SYSTEM_INSTRUCTIONS = (
    "You are an expert IT Service Desk agent. Your job is to help users "
    "diagnose and troubleshoot Windows system issues.\n\n"
    "When a user describes a problem, use the diagnostics tool to run "
    "PowerShell-based checks on their machine and interpret the results "
    "in a clear, helpful way.\n\n"
    "Keep your responses concise and actionable. If the diagnostic output "
    "reveals an issue, explain what it means and suggest next steps."
)


async def main() -> None:
    print("=" * 60)
    print("  Service Desk Agent  (type 'exit' to quit)")
    print("=" * 60)
    print(f"  MCP Server : {MCP_SERVER_URL}")
    print(f"  Model      : {DEPLOYMENT_NAME}")
    print("=" * 60)
    print()

    # --- Set up the MCP tool pointing at the local diagnostic server ---
    mcp_tool = MCPStreamableHTTPTool(
        name="diagnostics",
        url=MCP_SERVER_URL,
    )

    # --- Create the Azure OpenAI-backed agent ---
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

    # Create a thread to maintain conversation history across turns
    thread = agent.get_new_thread()

    # --- Interactive conversation loop ---
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("Goodbye!")
            break

        print("\nAgent: ", end="", flush=True)

        try:
            result = await agent.run(user_input, thread=thread)
            print(result)
        except Exception as e:
            print(f"\n[Error] {e}")


if __name__ == "__main__":
    asyncio.run(main())
