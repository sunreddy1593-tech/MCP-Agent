"""Gmail delivery via MCP (Phase 6 stub).

Creates a DRAFT message (never sends) to self/alias containing the note and a
link to the Doc, through a Gmail MCP server. No Google REST/OAuth here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from review_pulse.config import RunConfig
    from review_pulse.pipeline.render import RenderedNote

logger = logging.getLogger(__name__)


class GmailClient:
    """Thin wrapper over the Gmail MCP tools. Draft-only by design."""

    def create_draft(
        self,
        rendered: "RenderedNote",
        doc_url: str | None,
        config: "RunConfig",
    ) -> dict:
        """Create a draft email. Phase 0 stub returns a placeholder id.

        Returns a dict with at least {"draft_id"} in Phase 6. Never sends.
        """
        logger.info("[stub] GmailClient.create_draft - Gmail MCP integration in Phase 6")
        return {"draft_id": None}
