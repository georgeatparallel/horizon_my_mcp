"""Anonymous Parallel Search MCP calls over Streamable HTTP."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from horizon_my_mcp import __version__

_MCP_URL = "https://search.parallel.ai/mcp"
_TIMEOUT_SECONDS = 60


async def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    # Explicit transport prevents saved OAuth or other providers' credentials
    # from being used. Each call owns and closes its HTTP/MCP session.
    transport = StreamableHttpTransport(
        _MCP_URL, headers={"User-Agent": f"horizon-my-mcp/{__version__}"}
    )
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            async with Client(transport, timeout=_TIMEOUT_SECONDS, init_timeout=30) as client:
                result = await client.call_tool(name, arguments)
    except TimeoutError as exc:
        raise ToolError("Parallel request timed out after 60 seconds.") from exc
    if result.structured_content is not None:
        return result.structured_content
    return {"content": [block.model_dump(mode="json") for block in result.content]}


def _session(arguments: dict[str, Any], session_id: str | None) -> None:
    if session_id is not None:
        if not session_id.strip() or len(session_id) > 100:
            raise ValueError("session_id must contain 1-100 characters")
        arguments["session_id"] = session_id


async def search(
    objective: str,
    search_queries: list[str],
    session_id: str | None = None,
) -> dict[str, Any]:
    """Search for one objective using related keyword queries."""
    if not objective.strip():
        raise ValueError("objective must be a non-empty string")
    if not search_queries or any(not query.strip() for query in search_queries):
        raise ValueError("search_queries must contain non-empty strings")
    arguments: dict[str, Any] = {
        "objective": objective.strip(),
        "search_queries": [query.strip() for query in search_queries],
    }
    _session(arguments, session_id)
    return await _call_tool("web_search", arguments)


async def fetch(
    urls: list[str],
    objective: str | None = None,
    full_content: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Fetch page excerpts, optionally including complete markdown."""
    if not 1 <= len(urls) <= 20:
        raise ValueError("urls must contain 1-20 HTTP or HTTPS URLs")
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("urls must contain HTTP or HTTPS URLs")
    arguments: dict[str, Any] = {"urls": urls, "full_content": full_content}
    if objective is not None:
        if not objective.strip() or len(objective) > 200:
            raise ValueError("objective must contain 1-200 characters")
        arguments["objective"] = objective.strip()
    _session(arguments, session_id)
    return await _call_tool("web_fetch", arguments)
