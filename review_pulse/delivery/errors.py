"""Delivery-layer error types (Phase 6).

These distinguish *why* delivery could not complete so the orchestrator can
degrade gracefully: `McpUnavailable` (server missing/unreachable — fall back to
local artifacts) vs `McpToolError` (the server ran the tool but returned a
structured error) vs `DeliveryError` (a local/config problem before any call).
"""

from __future__ import annotations


class DeliveryError(Exception):
    """A local/config problem that prevents delivery (e.g. missing doc_id)."""


class McpUnavailable(DeliveryError):
    """The Google Workspace MCP server is not configured or not reachable.

    Signals the artifact-only fallback: keep the rendered pulse locally and
    report delivery status ``pending`` — nothing upstream is lost.
    """


class McpToolError(DeliveryError):
    """A tool returned a structured MCP error (``isError: true``).

    Carries the server's machine-readable ``code`` (e.g. ``DOCUMENT_NOT_FOUND``,
    ``INSUFFICIENT_SCOPE``, ``RATE_LIMITED``) alongside the human message.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
