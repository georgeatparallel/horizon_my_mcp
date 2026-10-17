"""Top-level FastMCP server entry point for FastMCP Cloud.

This file follows the three FastMCP Cloud entrypoint conventions:

1. An inferred ``mcp`` instance (``FastMCP("horizon-my-mcp")``) at module scope
   so ``fastmcp run src/server.py`` discovers it automatically.
2. A ``create_server()`` factory function for callers that want to build a
   server programmatically (recommended for FastMCP Cloud project config).
3. A ``main()`` / ``__main__`` guard for local stdio testing
   (``python -m horizon_my_mcp.server``).

The Prefect deployment path (``prefect_flows/flows.py``) and the legacy
console script (``horizon-mcp``) both reach this module through the installed
package, so there is exactly one source of truth for tool registration.
"""
from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from horizon_my_mcp import firecrawl_client as firecrawl
from horizon_my_mcp import parallel_client as parallel
from horizon_my_mcp import tavily_client as tavily

mcp = FastMCP("horizon-my-mcp")


@mcp.tool()
def web_search(
    query: str,
    limit: int = 5,
    sources: list[str] | None = None,
    tbs: str | None = None,
) -> str:
    """Search the web via Firecrawl and return a JSON-encoded payload.

    Falls back to Firecrawl's keyless free tier when FIRECRAWL_API_KEY is not
    set. Set FIRECRAWL_API_KEY to authenticate against a paid tier.

    Args:
        query: The search query string.
        limit: Maximum number of results (1-50, default 5).
        sources: Optional list of source types (e.g. ["web", "news"]).
        tbs: Optional time-based search filter (e.g. "qdr:d" for past day).
    """
    result: dict[str, Any] = firecrawl.search(query=query, limit=limit, sources=sources, tbs=tbs)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def web_extract(
    urls: list[str],
    formats: list[str] | None = None,
) -> str:
    """Scrape one or more URLs via Firecrawl and return a JSON-encoded payload.

    Uses Firecrawl's v2 scrape endpoint. Keyless fallback applies when
    FIRECRAWL_API_KEY is not set.

    Args:
        urls: One or more URLs to scrape.
        formats: Optional list of output formats (e.g. ["markdown", "html"]).
    """
    result: dict[str, Any] = firecrawl.extract(urls=urls, formats=formats)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def web_agent(
    prompt: str,
    urls: list[str] | None = None,
) -> str:
    """Run a Firecrawl agent with a natural-language prompt and return JSON.

    Uses the v2 ``FirecrawlClient.agent`` method. Keyless fallback applies
    when FIRECRAWL_API_KEY is not set.

    Args:
        prompt: Natural-language instruction for the agent.
        urls: Optional seed URLs to constrain the agent.
    """
    result: dict[str, Any] = firecrawl.agent(prompt=prompt, urls=urls)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def tavily_search(
    query: str,
    max_results: int = 5,
    topic: str = "general",
    include_answer: bool = True,
) -> str:
    """Search the web with Tavily (backward-compatible tool).

    Args:
        query: The search query.
        max_results: Number of results (1-20, default 5).
        topic: Either "general" or "news" (default "general").
        include_answer: Whether to include Tavily's synthesized answer.
    """
    result: dict[str, Any] = tavily.search(
        query=query,
        max_results=max_results,
        topic=topic,
        include_answer=include_answer,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def parallel_search(
    objective: str,
    search_queries: list[str],
    session_id: str | None = None,
) -> str:
    """Search the web with Parallel's anonymous Fast mode, without an API key.

    Args:
        objective: One focused research goal, including freshness needs.
        search_queries: Related keyword queries of 3-6 words each.
        session_id: Reuse the returned session_id across related searches/fetches.
    """
    result = await parallel.search(objective, search_queries, session_id)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def parallel_fetch(
    urls: list[str],
    objective: str | None = None,
    full_content: bool = False,
    session_id: str | None = None,
) -> str:
    """Fetch relevant page excerpts with Parallel, without an API key.

    Args:
        urls: 1-20 HTTP or HTTPS URLs to read.
        objective: Optional extraction goal, up to 200 characters.
        full_content: Include full markdown; may produce very large responses.
        session_id: Reuse the session_id from the related search/fetch response.
    """
    result = await parallel.fetch(urls, objective, full_content, session_id)
    return json.dumps(result, ensure_ascii=False, indent=2)


def create_server() -> FastMCP:
    """Factory function for callers that want a fresh server instance.

    FastMCP Cloud prefers the factory form for explicit entrypoint
    configuration. This returns the module-level ``mcp`` instance.
    """
    return mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
