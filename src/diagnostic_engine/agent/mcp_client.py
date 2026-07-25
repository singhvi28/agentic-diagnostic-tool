"""Sync FastMCP client facade for LangGraph nodes (stdio / HTTP / in-process)."""

from __future__ import annotations

import asyncio
import json
import threading
from contextvars import ContextVar
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

from diagnostic_engine.config import Settings, get_settings

_mcp_client_var: ContextVar["DiagnosticMcpClient | None"] = ContextVar(
    "diagnostic_mcp_client", default=None
)


def set_mcp_client(client: DiagnosticMcpClient | None) -> None:
    _mcp_client_var.set(client)


def get_mcp_client() -> DiagnosticMcpClient:
    client = _mcp_client_var.get()
    if client is None:
        raise RuntimeError(
            "MCP client not set. Use DiagnosticMcpClient as a context manager "
            "(diagnostic-agent CLI does this automatically)."
        )
    return client


def _unwrap_tool_result(result: Any) -> Any:
    """Normalize FastMCP CallToolResult into a plain Python value."""
    if result is None:
        return None
    if getattr(result, "is_error", False):
        content = getattr(result, "content", None) or []
        texts = []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                texts.append(text)
        raise RuntimeError("; ".join(texts) or "MCP tool returned an error")

    data = getattr(result, "data", None)
    if data is not None:
        return data
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured

    content = getattr(result, "content", None) or []
    texts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    if not texts:
        return None
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        return joined


def _unwrap_resource(contents: Any) -> str:
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    parts: list[str] = []
    for item in contents:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(item))
    return "\n".join(parts)


class DiagnosticMcpClient:
    """Keeps one FastMCP Client open for the duration of an agent run."""

    def __init__(self, transport: Any, *, timeout: float = 120.0) -> None:
        self._transport = transport
        self._timeout = timeout
        self._client: Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        server: Any | None = None,
    ) -> DiagnosticMcpClient:
        settings = settings or get_settings()
        if settings.mcp_transport == "inprocess":
            if server is None:
                from diagnostic_engine.mcp.server import mcp as default_mcp

                server = default_mcp
            return cls(server)
        if settings.mcp_transport == "http":
            return cls(StreamableHttpTransport(url=settings.mcp_url))
        # stdio — spawn MCP server subprocess (force child onto stdio)
        import os

        command = settings.mcp_stdio_command.strip()
        child_env = {**os.environ, "MCP_TRANSPORT": "stdio"}
        if " " in command:
            parts = command.split()
            transport = StdioTransport(command=parts[0], args=parts[1:], env=child_env)
        else:
            transport = StdioTransport(command=command, args=[], env=child_env)
        return cls(transport)

    def __enter__(self) -> DiagnosticMcpClient:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="mcp-client-loop", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=30)
        if self._error is not None:
            raise RuntimeError(f"Failed to start MCP client: {self._error}") from self._error
        set_mcp_client(self)
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._loop and self._client is not None:
                fut = asyncio.run_coroutine_threadsafe(self._aclose(), self._loop)
                fut.result(timeout=30)
        finally:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=10)
            set_mcp_client(None)
            self._client = None
            self._loop = None
            self._thread = None

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._aenter())
            self._ready.set()
            self._loop.run_forever()
        except BaseException as exc:  # noqa: BLE001
            self._error = exc
            self._ready.set()

    async def _aenter(self) -> None:
        self._client = Client(self._transport, timeout=self._timeout)
        await self._client.__aenter__()

    async def _aclose(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)

    def _run(self, coro: Any) -> Any:
        if self._loop is None or self._client is None:
            raise RuntimeError("MCP client is not started")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=self._timeout)

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        assert self._client is not None
        result = self._run(self._client.call_tool(name, arguments or {}))
        return _unwrap_tool_result(result)

    def read_resource(self, uri: str) -> str:
        assert self._client is not None
        result = self._run(self._client.read_resource(uri))
        return _unwrap_resource(result)

    def list_tools(self) -> list[str]:
        assert self._client is not None
        tools = self._run(self._client.list_tools())
        return [t.name for t in tools]
