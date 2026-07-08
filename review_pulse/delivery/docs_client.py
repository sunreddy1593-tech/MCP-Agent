"""Google Docs delivery via MCP (Phase 6).

Appends the weekly pulse to an existing Google Doc through the Workspace MCP
server's `append_to_google_doc` tool. The server is **append-only** — it never
creates or overwrites a Doc — so `outputs.doc_id` must point to a Doc that
already exists. No Google REST/OAuth here: MCP owns auth.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from review_pulse.delivery.errors import DeliveryError

if TYPE_CHECKING:
    from review_pulse.config import RunConfig
    from review_pulse.delivery.mcp_client import WorkspaceMcpClient
    from review_pulse.pipeline.render import RenderedNote

logger = logging.getLogger(__name__)

# Extracts the id from a full Docs URL like
# https://docs.google.com/document/d/<ID>/edit
_DOC_URL_ID = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")


def extract_doc_id(doc_ref: str) -> str:
    """Return the raw document id from a raw id or a full Docs URL."""
    match = _DOC_URL_ID.search(doc_ref)
    return match.group(1) if match else doc_ref.strip()


def doc_url(document_id: str) -> str:
    """Build the shareable edit URL for a document id."""
    return f"https://docs.google.com/document/d/{document_id}/edit"


class DocsClient:
    """Appends the pulse to an existing Doc via the Workspace MCP tool."""

    def __init__(self, mcp: "WorkspaceMcpClient") -> None:
        self.mcp = mcp

    def publish(self, rendered: "RenderedNote", config: "RunConfig") -> dict:
        """Append `rendered.doc_body` to the configured Doc.

        Returns ``{"doc_id", "doc_url", "status"}``. Raises `DeliveryError` when
        no `outputs.doc_id` is configured (the server cannot create a Doc), and
        `McpToolError` for structured tool failures (e.g. DOCUMENT_NOT_FOUND).
        """
        doc_ref = config.outputs.doc_id
        if not doc_ref:
            raise DeliveryError(
                "outputs.doc_id is required: the Workspace MCP server appends to "
                "an existing Doc and cannot create one. Create a Doc once and set "
                "its id (or URL) in the run config."
            )

        logger.info("Appending pulse to Doc %s via MCP", extract_doc_id(doc_ref))
        result = self.mcp.call_tool(
            "append_to_google_doc",
            {
                "document_id": doc_ref,
                "content": rendered.doc_body,
                "add_newline_before": True,
            },
        )
        # The tool returns {document_id, status}; no URL, so derive it.
        returned_id = str(result.get("document_id") or extract_doc_id(doc_ref))
        return {
            "doc_id": returned_id,
            "doc_url": doc_url(returned_id),
            "status": result.get("status", "appended"),
        }
