"""Gmail delivery via MCP (Phase 6).

Creates a Gmail **draft** (never sends) containing the rendered note and a link
to the Doc, through the Workspace MCP server's `draft_gmail` tool. `send_gmail`
exists on the server but is intentionally never called — a human sends the
draft. No Google REST/OAuth here: MCP owns auth.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from review_pulse.delivery.errors import DeliveryError
from review_pulse.pipeline.render import DOC_LINK_PLACEHOLDER

if TYPE_CHECKING:
    from review_pulse.config import RunConfig
    from review_pulse.delivery.mcp_client import WorkspaceMcpClient
    from review_pulse.pipeline.render import RenderedNote

logger = logging.getLogger(__name__)


class GmailClient:
    """Creates a draft email via the Workspace MCP tool. Draft-only by design."""

    def __init__(self, mcp: "WorkspaceMcpClient") -> None:
        self.mcp = mcp

    def create_draft(
        self,
        rendered: "RenderedNote",
        doc_url: str | None,
        config: "RunConfig",
    ) -> dict:
        """Create a draft to `outputs.email_to`. Never sends.

        Resolves the Doc-link placeholder in the email body and returns
        ``{"draft_id", "message_id", "status"}``.
        """
        email_to = config.outputs.email_to
        if not email_to:
            raise DeliveryError("outputs.email_to is required to create a Gmail draft")

        link = doc_url or "(document link unavailable)"
        body = rendered.email_body.replace(DOC_LINK_PLACEHOLDER, link)

        logger.info("Creating Gmail draft (never sent) to configured recipient")
        # NOTE: draft_gmail only — send_gmail is deliberately never invoked.
        result = self.mcp.call_tool(
            "draft_gmail",
            {
                "to": [email_to],
                "subject": rendered.subject,
                "body": body,
                "body_type": "text",
            },
        )
        return {
            "draft_id": result.get("draft_id"),
            "message_id": result.get("message_id"),
            "status": result.get("status", "drafted"),
        }
