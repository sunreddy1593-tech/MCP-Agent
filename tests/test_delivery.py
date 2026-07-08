"""Phase 6 tests: MCP delivery (append-only Docs, draft-only Gmail).

The MCP transport is faked so these run offline: a `FakeMcp` records tool calls
and returns canned results (or raises structured errors).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from review_pulse.config import MCPConfig, OutputTargets, RunConfig
from review_pulse.delivery.deliver import deliver
from review_pulse.delivery.docs_client import DocsClient, doc_url, extract_doc_id
from review_pulse.delivery.errors import DeliveryError, McpToolError
from review_pulse.delivery.gmail_client import GmailClient
from review_pulse.delivery.mcp_client import WorkspaceMcpClient
from review_pulse.pipeline.render import DOC_LINK_PLACEHOLDER, RenderedNote
from review_pulse.store.local_store import LocalStore

WEEK = "2026-07-06"


class FakeMcp:
    """Stand-in for WorkspaceMcpClient that records calls."""

    def __init__(self, responses=None, errors=None, configured=True):
        self.configured = configured
        self.responses = responses or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, dict]] = []

    def is_configured(self) -> bool:
        return self.configured

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        if name in self.errors:
            raise self.errors[name]
        return self.responses.get(name, {})


def _config(doc_id="DOC123", email_to="me@example.com") -> RunConfig:
    return RunConfig(
        product_id="com.test",
        product_name="Groww",
        outputs=OutputTargets(doc_id=doc_id, email_to=email_to),
    )


def _rendered(ok: bool = True) -> RenderedNote:
    return RenderedNote(
        doc_body="# Groww Weekly Pulse\n\nBody.\n",
        email_body=f"Weekly pulse.\n\nFull document: {DOC_LINK_PLACEHOLDER}\n",
        subject="Groww Weekly Review Pulse — 2026-07-06",
        ok=ok,
        issues=[] if ok else ["render blocked delivery"],
    )


def _happy_mcp() -> FakeMcp:
    return FakeMcp(
        responses={
            "append_to_google_doc": {"document_id": "DOC123", "status": "appended"},
            "draft_gmail": {"draft_id": "DRAFT1", "message_id": "M1", "status": "drafted"},
        }
    )


# --- doc id helpers -------------------------------------------------------

def test_extract_doc_id_from_raw_and_url():
    assert extract_doc_id("DOC123") == "DOC123"
    assert extract_doc_id("https://docs.google.com/document/d/ABC_9-x/edit") == "ABC_9-x"


def test_doc_url_builder():
    assert doc_url("DOC123") == "https://docs.google.com/document/d/DOC123/edit"


# --- DocsClient -----------------------------------------------------------

def test_docs_client_appends_with_correct_args():
    mcp = _happy_mcp()
    result = DocsClient(mcp).publish(_rendered(), _config())
    name, args = mcp.calls[0]
    assert name == "append_to_google_doc"
    assert args["document_id"] == "DOC123"
    assert args["add_newline_before"] is True
    assert result["doc_url"] == "https://docs.google.com/document/d/DOC123/edit"
    assert result["status"] == "appended"


def test_docs_client_requires_doc_id():
    with pytest.raises(DeliveryError):
        DocsClient(_happy_mcp()).publish(_rendered(), _config(doc_id=None))


# --- GmailClient ----------------------------------------------------------

def test_gmail_client_creates_draft_and_resolves_link():
    mcp = _happy_mcp()
    result = GmailClient(mcp).create_draft(
        _rendered(), "https://docs.google.com/document/d/DOC123/edit", _config()
    )
    name, args = mcp.calls[0]
    assert name == "draft_gmail"
    assert args["to"] == ["me@example.com"]
    assert DOC_LINK_PLACEHOLDER not in args["body"]
    assert "document/d/DOC123" in args["body"]
    assert result["draft_id"] == "DRAFT1"


def test_gmail_client_never_calls_send():
    mcp = _happy_mcp()
    GmailClient(mcp).create_draft(_rendered(), None, _config())
    assert all(name != "send_gmail" for name, _ in mcp.calls)


def test_gmail_client_requires_email_to():
    with pytest.raises(DeliveryError):
        GmailClient(_happy_mcp()).create_draft(_rendered(), None, _config(email_to=None))


# --- deliver() orchestration ---------------------------------------------

def test_deliver_pending_when_render_blocked(tmp_path):
    store = LocalStore(tmp_path)
    res = deliver(_rendered(ok=False), _config(), store, WEEK, mcp=_happy_mcp())
    assert res.status == "pending"


def test_deliver_pending_when_mcp_not_configured(tmp_path):
    store = LocalStore(tmp_path)
    res = deliver(_rendered(), _config(), store, WEEK, mcp=FakeMcp(configured=False))
    assert res.status == "pending"
    assert res.doc_id is None and res.draft_id is None


def test_deliver_happy_path(tmp_path):
    store = LocalStore(tmp_path)
    mcp = _happy_mcp()
    res = deliver(_rendered(), _config(), store, WEEK, mcp=mcp)
    assert res.status == "delivered"
    assert res.doc_url == "https://docs.google.com/document/d/DOC123/edit"
    assert res.draft_id == "DRAFT1"
    # ledger recorded both markers
    ledger = store.read_delivery_ledger()
    assert f"doc:DOC123:{WEEK}" in ledger
    assert f"draft:me@example.com:{WEEK}" in ledger


def test_deliver_is_idempotent_for_same_week(tmp_path):
    store = LocalStore(tmp_path)
    deliver(_rendered(), _config(), store, WEEK, mcp=_happy_mcp())
    # Second run of the same week must not call any tool again.
    second = FakeMcp(responses={})
    res = deliver(_rendered(), _config(), store, WEEK, mcp=second)
    assert res.status == "delivered"
    assert second.calls == []
    assert res.doc_url == "https://docs.google.com/document/d/DOC123/edit"


def test_deliver_partial_when_gmail_fails(tmp_path):
    store = LocalStore(tmp_path)
    mcp = FakeMcp(
        responses={"append_to_google_doc": {"document_id": "DOC123", "status": "appended"}},
        errors={"draft_gmail": McpToolError("RATE_LIMITED", "slow down")},
    )
    res = deliver(_rendered(), _config(), store, WEEK, mcp=mcp)
    assert res.status == "partial"
    assert res.doc_url is not None
    assert res.draft_id is None
    # Doc marker persisted, draft marker not.
    ledger = store.read_delivery_ledger()
    assert f"doc:DOC123:{WEEK}" in ledger
    assert f"draft:me@example.com:{WEEK}" not in ledger


def test_deliver_pending_when_document_not_found(tmp_path):
    store = LocalStore(tmp_path)
    mcp = FakeMcp(errors={"append_to_google_doc": McpToolError("DOCUMENT_NOT_FOUND", "no doc")})
    res = deliver(_rendered(), _config(), store, WEEK, mcp=mcp)
    assert res.status == "pending"
    assert any("docs" in i for i in res.issues)


# --- WorkspaceMcpClient result parsing -----------------------------------

def _fake_result(*, is_error=False, text=None, structured=None):
    content = [SimpleNamespace(text=text)] if text is not None else []
    return SimpleNamespace(isError=is_error, content=content, structuredContent=structured)


def test_parse_result_json_text():
    client = WorkspaceMcpClient(url="http://x/mcp")
    out = client._parse_result(_fake_result(text='{"document_id": "D1", "status": "appended"}'))
    assert out == {"document_id": "D1", "status": "appended"}


def test_parse_result_structured_content_preferred():
    client = WorkspaceMcpClient(url="http://x/mcp")
    out = client._parse_result(_fake_result(structured={"draft_id": "D9"}, text="ignored"))
    assert out == {"draft_id": "D9"}


def test_parse_result_raises_structured_tool_error():
    client = WorkspaceMcpClient(url="http://x/mcp")
    result = _fake_result(is_error=True, text='{"error": {"code": "INSUFFICIENT_SCOPE", "message": "nope"}}')
    with pytest.raises(McpToolError) as exc:
        client._parse_result(result)
    assert exc.value.code == "INSUFFICIENT_SCOPE"


def test_is_configured_http_requires_url():
    assert WorkspaceMcpClient(transport="http", url=None).is_configured() is False
    assert WorkspaceMcpClient(transport="http", url="http://x/mcp").is_configured() is True
    assert WorkspaceMcpClient(transport="stdio", command="node").is_configured() is True


def test_from_config_reads_mcp_section():
    cfg = _config()
    cfg.mcp = MCPConfig(transport="http", url="http://x/mcp", auth_token="t", max_retries=2)
    client = WorkspaceMcpClient.from_config(cfg)
    assert client.url == "http://x/mcp"
    assert client.auth_token == "t"
    assert client.max_retries == 2
