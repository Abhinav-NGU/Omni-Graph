"""
tools_caller.py — calls mcp-tools-service from the Python agent.
"""
import logging
import httpx
from core.config import settings

logger = logging.getLogger(__name__)


async def call_tool(tool: str, input: str) -> dict:
    """Call a tool on the mcp-tools-service and return the result."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{settings.MCP_TOOLS_URL}/tools/execute",
                json={"tool": tool, "input": input},
            )
            res.raise_for_status()
            data = res.json()
            if "error" in data and data["error"]:
                logger.error(f"Tool '{tool}' returned error: {data['error']}")
                return {"error": data["error"]}
            return data.get("result", {})
    except Exception as e:
        logger.error(f"Failed to call tool '{tool}': {e}")
        return {"error": str(e)}