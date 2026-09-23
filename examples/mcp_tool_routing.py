"""Route a real MCP server's tool catalog with AgentWeave.

Run an MCP server first, then:

    pip install -e '.[mcp]'
    python examples/mcp_tool_routing.py \
      --url http://localhost:8000/mcp \
      --query 'Search the codebase for the router implementation'

This example uses the production MCPToolCatalog and production adaptive router. The
matching MCPExecutor is what AgentWeaveRuntime uses after model selection and
authorization; no benchmark proxy is involved in this integration path.
"""

from __future__ import annotations

import argparse
import asyncio

from agentweave import RunContext
from agentweave.integrations.mcp import MCPExecutor, MCPToolCatalog
from agentweave_byom import AdaptiveRouter, DeterministicRouterV1


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000/mcp")
    parser.add_argument("--query", required=True)
    parser.add_argument("--max-tools", type=int, default=6)
    args = parser.parse_args()

    catalog = MCPToolCatalog(args.url)
    executor = MCPExecutor(args.url)  # used by AgentWeaveRuntime for actual tool calls
    assert executor is not None

    tools = await catalog.list_tools(RunContext())
    router = AdaptiveRouter(DeterministicRouterV1())
    routing = await router.aroute(
        args.query,
        [tool.to_function_tool() for tool in tools],
        max_tools=args.max_tools,
    )

    print(f"MCP tools discovered: {len(tools)}")
    print(f"Model-visible tools after AgentWeave routing: {len(routing.selected)}")
    print(f"Routing confidence: {routing.confidence:.3f}")
    print(f"Abstained from aggressive pruning: {routing.abstained}")
    for tool in routing.selected:
        function = tool["function"]
        print(f"- {function['name']}: {function.get('description', '')}")


if __name__ == "__main__":
    asyncio.run(main())
