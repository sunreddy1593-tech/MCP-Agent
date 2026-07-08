"""MCP client for the Google Workspace server (Phase 6).

The pipeline is an MCP *consumer*: it calls the Google Workspace MCP server
(`sunreddy1593-tech/MCP-1`) which exposes `append_to_google_doc`, `draft_gmail`,
and `send_gmail`. This wrapper connects over **streamable HTTP** or **stdio**
using the official Python `mcp` SDK, reads each tool's schema before calling,
maps the server's structured errors onto `McpToolError`, and retries transient
failures with bounded exponential backoff.

The `mcp` SDK is imported lazily so importing this module (and running stages
1–5 / the tests) works even when the package is not installed — in that case
`call_tool` raises `McpUnavailable` and the delivery layer degrades to the
local-artifact fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from review_pulse.delivery.errors import McpToolError, McpUnavailable

if TYPE_CHECKING:
    from review_pulse.config import RunConfig

logger = logging.getLogger(__name__)

# Structured error codes worth retrying (transient). Deterministic errors like
# DOCUMENT_NOT_FOUND / INVALID_INPUT / INSUFFICIENT_SCOPE are never retried.
_TRANSIENT_CODES = {"RATE_LIMITED", "NETWORK_ERROR"}


class WorkspaceMcpClient:
    """Thin, synchronous facade over the Google Workspace MCP tools."""

    def __init__(
        self,
        *,
        transport: str = "http",
        url: str | None = None,
        auth_token: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        server_label: str = "google-workspace",
        max_retries: int = 3,
    ) -> None:
        self.transport = (transport or "http").lower()
        self.url = url
        self.auth_token = auth_token
        self.command = command
        self.args = args or []
        self.server_label = server_label
        self.max_retries = max_retries

    @classmethod
    def from_config(cls, config: "RunConfig") -> "WorkspaceMcpClient":
        m = config.mcp
        return cls(
            transport=m.transport,
            url=m.url,
            auth_token=m.auth_token,
            command=m.command,
            args=list(m.args),
            server_label=m.server_label,
            max_retries=m.max_retries,
        )

    def is_configured(self) -> bool:
        """True when enough connection info is present to attempt a call."""
        if self.transport == "http":
            return bool(self.url)
        if self.transport == "stdio":
            return bool(self.command)
        return False

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call an MCP tool and return its parsed result dict.

        Raises `McpUnavailable` when the server is not configured/reachable or
        the SDK is missing, and `McpToolError` (with `.code`) on structured tool
        errors. Transient errors are retried with exponential backoff.
        """
        if not self.is_configured():
            raise McpUnavailable(
                "Google Workspace MCP server is not configured "
                "(set MCP_WORKSPACE_URL or a stdio command)"
            )

        attempt = 0
        while True:
            attempt += 1
            try:
                return asyncio.run(self._call_async(name, arguments))
            except McpToolError as exc:
                if exc.code in _TRANSIENT_CODES and attempt <= self.max_retries:
                    self._backoff(name, exc.code, attempt)
                    continue
                raise
            except McpUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - transport/connection errors
                if attempt <= self.max_retries:
                    self._backoff(name, type(exc).__name__, attempt)
                    continue
                raise McpUnavailable(
                    f"could not reach the '{self.server_label}' MCP server: {exc}"
                ) from exc

    def _backoff(self, tool: str, reason: str, attempt: int) -> None:
        delay = 2 ** (attempt - 1)
        logger.warning(
            "MCP tool '%s' transient failure (%s); retry %d/%d in %ds",
            tool, reason, attempt, self.max_retries, delay,
        )
        time.sleep(delay)

    async def _call_async(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        ClientSession, connect = self._connect_factory()
        async with connect() as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Read each tool's schema before calling (validate it exists).
                listing = await session.list_tools()
                available = {t.name for t in listing.tools}
                if name not in available:
                    raise McpToolError(
                        "TOOL_NOT_FOUND",
                        f"server exposes {sorted(available)}, not '{name}'",
                    )

                result = await session.call_tool(name, arguments)
                return self._parse_result(result)

    def _connect_factory(self):
        """Return (ClientSession, connect) for the configured transport."""
        try:
            from mcp import ClientSession  # noqa: PLC0415 (lazy import by design)
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise McpUnavailable(
                "The 'mcp' package is not installed. Run: pip install -r requirements.txt"
            ) from exc

        if self.transport == "http":
            from mcp.client.streamable_http import (  # noqa: PLC0415
                streamablehttp_client,
            )

            headers = (
                {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else None
            )
            return ClientSession, lambda: streamablehttp_client(self.url, headers=headers)

        if self.transport == "stdio":
            from mcp import StdioServerParameters  # noqa: PLC0415
            from mcp.client.stdio import stdio_client  # noqa: PLC0415

            params = StdioServerParameters(command=self.command, args=self.args)
            return ClientSession, lambda: stdio_client(params)

        raise McpUnavailable(f"unsupported MCP transport: {self.transport!r}")

    def _parse_result(self, result: Any) -> dict[str, Any]:
        """Turn a CallToolResult into a plain dict, raising on tool errors."""
        structured = getattr(result, "structuredContent", None)
        text = self._collect_text(result)

        if getattr(result, "isError", False):
            code, message = self._extract_error(structured, text)
            raise McpToolError(code, message)

        if isinstance(structured, dict) and structured:
            return structured
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return {"result": text}
            return data if isinstance(data, dict) else {"result": data}
        return {}

    @staticmethod
    def _collect_text(result: Any) -> str:
        parts: list[str] = []
        for block in getattr(result, "content", None) or []:
            piece = getattr(block, "text", None)
            if piece:
                parts.append(piece)
        return "\n".join(parts).strip()

    @staticmethod
    def _extract_error(structured: Any, text: str) -> tuple[str, str]:
        """Pull a machine-readable (code, message) from a structured tool error."""
        candidate: Any = structured
        if not isinstance(candidate, dict) and text:
            try:
                candidate = json.loads(text)
            except json.JSONDecodeError:
                candidate = None
        if isinstance(candidate, dict):
            err = candidate.get("error", candidate)
            if isinstance(err, dict):
                code = str(err.get("code") or "GOOGLE_API_ERROR")
                message = str(err.get("message") or text or code)
                return code, message
        return "GOOGLE_API_ERROR", text or "MCP tool returned an error"
