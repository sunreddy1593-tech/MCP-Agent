"""Delivery layer: Google Docs + Gmail via MCP servers (never Google REST)."""

from review_pulse.delivery.deliver import DeliveryResult, deliver
from review_pulse.delivery.docs_client import DocsClient
from review_pulse.delivery.errors import DeliveryError, McpToolError, McpUnavailable
from review_pulse.delivery.gmail_client import GmailClient
from review_pulse.delivery.mcp_client import WorkspaceMcpClient

__all__ = [
    "deliver",
    "DeliveryResult",
    "DocsClient",
    "GmailClient",
    "WorkspaceMcpClient",
    "DeliveryError",
    "McpUnavailable",
    "McpToolError",
]
