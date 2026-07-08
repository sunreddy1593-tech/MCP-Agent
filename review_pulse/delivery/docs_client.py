"""Google Docs delivery via MCP (Phase 6 stub).

Creates or updates the weekly pulse document through a Google Docs MCP server
and returns a shareable link. No Google REST/OAuth here — MCP owns auth.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from review_pulse.config import RunConfig
    from review_pulse.pipeline.render import RenderedNote

logger = logging.getLogger(__name__)


class DocsClient:
    """Thin wrapper over the Google Docs MCP tools."""

    def publish(self, rendered: "RenderedNote", config: "RunConfig") -> dict:
        """Create/update the pulse Doc. Phase 0 stub returns placeholders.

        Returns a dict with at least {"doc_id", "doc_url"} in Phase 6.
        """
        logger.info("[stub] DocsClient.publish - Docs MCP integration in Phase 6")
        return {"doc_id": None, "doc_url": None}
