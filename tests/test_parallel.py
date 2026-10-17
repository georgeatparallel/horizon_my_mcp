"""Exercise MCP dispatch and the real HTTP transport without external services."""

import asyncio
import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from horizon_my_mcp import parallel_client, server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def upstream(monkeypatch):
    state = {
        "requests": [],
        "clients": [],
        "error": False,
        "text_only": False,
        "delay": False,
        "entered": asyncio.Event(),
    }

    async def respond(request):
        assert str(request.url) == "https://search.parallel.ai/mcp"
        assert request.headers["user-agent"] == "horizon-my-mcp/0.2.0"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        message = json.loads(request.content)
        state["requests"].append(message)
        method = message["method"]
        if "id" not in message:
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "parallel-fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": name, "inputSchema": {"type": "object"}}
                    for name in ("web_search", "web_fetch")
                ]
            }
        elif method == "tools/call":
            state["entered"].set()
            if state["delay"]:
                await asyncio.sleep(60)
            payload = {
                "results": [{"url": "https://example.com", "excerpts": ["Useful source text"]}],
                "session_id": "fixture-session",
            }
            text = "quota exceeded" if state["error"] else json.dumps(payload)
            result = {"content": [{"type": "text", "text": text}], "isError": state["error"]}
            if not state["error"] and not state["text_only"]:
                result["structuredContent"] = payload
        else:
            raise AssertionError(f"Unexpected method: {method}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    def factory(**kwargs):
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs)
        state["clients"].append(client)
        return client

    def transport(url, **kwargs):
        return StreamableHttpTransport(url, httpx_client_factory=factory, **kwargs)

    monkeypatch.setattr(parallel_client, "StreamableHttpTransport", transport)
    # No provider key may change the anonymous caller's request construction.
    monkeypatch.setenv("PARALLEL_API_KEY", "unused-parallel-key")
    monkeypatch.setenv("TAVILY_API_KEY", "unused-tavily-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "unused-firecrawl-key")
    return state


@pytest.mark.anyio
async def test_search_and_fetch_through_registered_tools(upstream):
    async with Client(server.create_server()) as client:
        tools = {tool.name for tool in await client.list_tools()}
        assert tools == {
            "web_search",
            "web_extract",
            "web_agent",
            "tavily_search",
            "parallel_search",
            "parallel_fetch",
        }
        assert upstream["requests"] == []  # Listing must not require network access.
        search = await client.call_tool(
            "parallel_search",
            {
                "objective": " Find current docs ",
                "search_queries": [" FastMCP local server documentation "],
            },
        )
        data = json.loads(search.content[0].text)
        assert data["results"][0]["excerpts"] == ["Useful source text"]
        fetch = await client.call_tool(
            "parallel_fetch",
            {
                "urls": [data["results"][0]["url"]],
                "objective": "Read the setup steps",
                "session_id": data["session_id"],
                "full_content": True,
            },
        )
        assert json.loads(fetch.content[0].text)["results"][0]["url"] == "https://example.com"
    calls = [
        {key: r["params"][key] for key in ("name", "arguments")}
        for r in upstream["requests"]
        if r["method"] == "tools/call"
    ]
    assert calls == [
        {
            "name": "web_search",
            "arguments": {
                "objective": "Find current docs",
                "search_queries": ["FastMCP local server documentation"],
            },
        },
        {
            "name": "web_fetch",
            "arguments": {
                "urls": ["https://example.com"],
                "objective": "Read the setup steps",
                "full_content": True,
                "session_id": "fixture-session",
            },
        },
    ]
    assert all(client.is_closed for client in upstream["clients"])


@pytest.mark.anyio
async def test_fetch_defaults_and_search_session(upstream):
    await parallel_client.fetch(["https://example.com"])
    await parallel_client.search("Read docs", ["current MCP docs"], "stable-session")
    calls = [r["params"] for r in upstream["requests"] if r["method"] == "tools/call"]
    assert calls[0]["arguments"] == {"urls": ["https://example.com"], "full_content": False}
    assert calls[1]["arguments"]["session_id"] == "stable-session"


@pytest.mark.anyio
async def test_text_only_result_is_preserved(upstream):
    upstream["text_only"] = True
    async with Client(server.create_server()) as client:
        result = await client.call_tool(
            "parallel_search",
            {
                "objective": "Read docs",
                "search_queries": ["current MCP docs"],
            },
        )
        content = json.loads(result.content[0].text)["content"]
        assert json.loads(content[0]["text"])["results"][0]["excerpts"] == ["Useful source text"]


@pytest.mark.anyio
async def test_remote_error_remains_mcp_error(upstream):
    upstream["error"] = True
    async with Client(server.create_server()) as client:
        with pytest.raises(ToolError, match="quota exceeded"):
            await client.call_tool(
                "parallel_search",
                {
                    "objective": "Read docs",
                    "search_queries": ["current MCP docs"],
                },
            )
    assert all(client.is_closed for client in upstream["clients"])


@pytest.mark.anyio
async def test_timeout_closes_http_session(upstream, monkeypatch):
    upstream["delay"] = True
    monkeypatch.setattr(parallel_client, "_TIMEOUT_SECONDS", 0.1)
    async with Client(server.create_server()) as client:
        with pytest.raises(ToolError, match="timed out"):
            await client.call_tool("parallel_fetch", {"urls": ["https://example.com"]})
    assert upstream["entered"].is_set()
    assert all(client.is_closed for client in upstream["clients"])


@pytest.mark.anyio
async def test_cancellation_propagates_and_closes_http_session(upstream):
    upstream["delay"] = True
    task = asyncio.create_task(parallel_client.search("Read docs", ["current MCP docs"]))
    await asyncio.wait_for(upstream["entered"].wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert all(client.is_closed for client in upstream["clients"])


@pytest.mark.anyio
@pytest.mark.parametrize(
    "name,arguments",
    [
        ("parallel_search", {"objective": " ", "search_queries": ["docs"]}),
        ("parallel_search", {"objective": "docs", "search_queries": []}),
        ("parallel_search", {"objective": "docs", "search_queries": [" "]}),
        ("parallel_search", {"objective": "docs", "search_queries": ["docs"], "session_id": ""}),
        ("parallel_fetch", {"urls": []}),
        ("parallel_fetch", {"urls": ["file:///etc/hosts"]}),
        ("parallel_fetch", {"urls": ["https://"]}),
        ("parallel_fetch", {"urls": ["https://example.com"] * 21}),
        ("parallel_fetch", {"urls": ["https://example.com"], "objective": "x" * 201}),
        ("parallel_fetch", {"urls": ["https://example.com"], "session_id": "x" * 101}),
    ],
)
async def test_invalid_arguments_do_not_contact_upstream(upstream, name, arguments):
    async with Client(server.create_server()) as client:
        with pytest.raises(ToolError):
            await client.call_tool(name, arguments)
    assert upstream["requests"] == []


@pytest.mark.anyio
async def test_existing_tools_keep_their_providers_and_defaults(monkeypatch, upstream):
    calls = []

    def record(**kwargs):
        calls.append(kwargs)
        return {"legacy": True}

    for name in ("search", "extract", "agent"):
        monkeypatch.setattr(server.firecrawl, name, record)
    monkeypatch.setattr(server.tavily, "search", record)
    async with Client(server.create_server()) as client:
        for name, arguments in [
            ("web_search", {"query": "docs"}),
            ("web_extract", {"urls": ["https://example.com"]}),
            ("web_agent", {"prompt": "Read docs"}),
            ("tavily_search", {"query": "docs"}),
        ]:
            result = await client.call_tool(name, arguments)
            assert json.loads(result.content[0].text) == {"legacy": True}
    assert calls == [
        {"query": "docs", "limit": 5, "sources": None, "tbs": None},
        {"urls": ["https://example.com"], "formats": None},
        {"prompt": "Read docs", "urls": None},
        {"query": "docs", "max_results": 5, "topic": "general", "include_answer": True},
    ]
    assert upstream["requests"] == []
