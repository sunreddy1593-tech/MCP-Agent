"""Delivery orchestration (Phase 6).

Ties the Docs + Gmail MCP clients together with idempotency and graceful
degradation:

- If rendering blocked delivery, or the MCP server is not configured/reachable,
  keep the Phase 5 artifacts locally and report ``pending`` — nothing is lost.
- Idempotency: a per-week ledger prevents a re-run of the same week from
  appending a duplicate Doc section or creating a second draft. A *new* week
  appends a fresh section as intended.
- If the Doc append succeeds but the draft fails, keep the Doc (``partial``) and
  only the draft needs retrying next run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from review_pulse.delivery.docs_client import DocsClient, extract_doc_id
from review_pulse.delivery.errors import DeliveryError, McpToolError, McpUnavailable
from review_pulse.delivery.gmail_client import GmailClient
from review_pulse.delivery.mcp_client import WorkspaceMcpClient

if TYPE_CHECKING:
    from review_pulse.config import RunConfig
    from review_pulse.pipeline.render import RenderedNote
    from review_pulse.store.local_store import LocalStore

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    """Outcome of a delivery attempt, folded into the RunManifest."""

    status: str  # "delivered" | "partial" | "pending"
    doc_id: str | None = None
    doc_url: str | None = None
    draft_id: str | None = None
    issues: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def deliver(
    rendered: "RenderedNote",
    config: "RunConfig",
    store: "LocalStore",
    week_of: str,
    mcp: "WorkspaceMcpClient | None" = None,
) -> DeliveryResult:
    """Publish the pulse to Docs + Gmail via MCP, idempotently."""
    if not rendered.ok:
        issues = list(rendered.issues) or ["render blocked delivery"]
        logger.warning(
            "Delivery blocked upstream (%s); artifacts kept locally, status 'pending'",
            "; ".join(issues),
        )
        return DeliveryResult(status="pending", issues=issues)

    client = mcp or WorkspaceMcpClient.from_config(config)
    if not client.is_configured():
        logger.warning(
            "Google Workspace MCP server not configured; keeping rendered "
            "note.md/email.txt locally with delivery status 'pending'. Set "
            "MCP_WORKSPACE_URL (+ MCP_WORKSPACE_AUTH_TOKEN) or a stdio command "
            "to enable real delivery."
        )
        return DeliveryResult(
            status="pending",
            issues=["Google Workspace MCP server not configured"],
        )

    ledger = store.read_delivery_ledger()

    # --- Docs: append to the existing Doc (idempotent per week) ----------
    doc_id: str | None = None
    doc_url: str | None = None
    doc_key: str | None = None
    if config.outputs.doc_id:
        doc_key = f"doc:{extract_doc_id(config.outputs.doc_id)}:{week_of}"

    try:
        if doc_key and doc_key in ledger:
            entry = ledger[doc_key]
            doc_id, doc_url = entry.get("doc_id"), entry.get("doc_url")
            logger.info(
                "Doc already appended for week %s (idempotent skip): %s",
                week_of, doc_url,
            )
        else:
            result = DocsClient(client).publish(rendered, config)
            doc_id, doc_url = result["doc_id"], result["doc_url"]
            if doc_key:
                store.write_delivery_marker(
                    doc_key,
                    {"doc_id": doc_id, "doc_url": doc_url,
                     "week_of": week_of, "appended_at": _now()},
                )
            logger.info("Appended pulse to Doc: %s", doc_url)
    except (McpUnavailable, McpToolError, DeliveryError) as exc:
        logger.error("Docs delivery failed; artifacts kept locally: %s", exc)
        return DeliveryResult(status="pending", issues=[f"docs: {exc}"])

    # --- Gmail: create a draft (never send), idempotent per week ---------
    draft_id: str | None = None
    draft_key = f"draft:{config.outputs.email_to}:{week_of}"
    try:
        if draft_key in ledger:
            draft_id = ledger[draft_key].get("draft_id")
            logger.info(
                "Draft already created for week %s (idempotent skip): %s",
                week_of, draft_id,
            )
        else:
            result = GmailClient(client).create_draft(rendered, doc_url, config)
            draft_id = result["draft_id"]
            store.write_delivery_marker(
                draft_key,
                {"draft_id": draft_id, "week_of": week_of, "created_at": _now()},
            )
            logger.info("Created Gmail draft (not sent): %s", draft_id)
    except (McpUnavailable, McpToolError, DeliveryError) as exc:
        logger.error("Gmail draft failed (Doc kept); retry draft next run: %s", exc)
        return DeliveryResult(
            status="partial", doc_id=doc_id, doc_url=doc_url,
            issues=[f"gmail: {exc}"],
        )

    return DeliveryResult(
        status="delivered", doc_id=doc_id, doc_url=doc_url, draft_id=draft_id
    )
