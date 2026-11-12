"""Trusted MCP service for the Nexent patient and feedback agents.

Run:  python -m app.mcp_server   (from the backend directory) with MCP_SERVICE_TOKEN set.

Tools take only an opaque ``turn_token`` plus the model's own *choices* (which candidate facts to
use, feedback text). They never take session/owner ids, fact text or scores.
"""
from __future__ import annotations

import hmac
import os
from typing import Any

from fastmcp import FastMCP

from .case_store import CaseStore
from .integration.config import ROOT, IntegrationSettings, data_directory
from .integration.knowledge import KnowledgeStore
from .integration.tools import TrustedToolService
from .integration.turns import TurnStore
from .storage import SessionRepository


def build_mcp(service: TrustedToolService) -> FastMCP:
    mcp = FastMCP(name="ai-digital-patient-trusted-tools")

    @mcp.tool(
        name="get_turn_context",
        description=(
            "读取本回合的学生提问和系统已授权、可用于回答的候选事实。仅需 turn_token（来自回合消息）。"
            "候选之外的信息一律不得使用。"
        ),
    )
    def get_turn_context(turn_token: str) -> dict[str, Any]:
        return service.get_turn_context(turn_token)

    @mcp.tool(
        name="submit_fact_selection",
        description=(
            "提交本回合用于回答学生的事实ID列表（只能来自 get_turn_context 的 candidates，可为空）。"
            "每个回合只能成功提交一次；最终回答由系统根据所选事实生成。"
        ),
    )
    def submit_fact_selection(turn_token: str, fact_ids: list[str]) -> dict[str, Any]:
        return service.submit_fact_selection(turn_token, fact_ids)

    @mcp.tool(
        name="get_sealed_review",
        description="封存后反馈专用：读取已封存训练的评分快照、事件摘要和学生提交内容。分数只读。",
    )
    def get_sealed_review(turn_token: str) -> dict[str, Any]:
        return service.get_sealed_review(turn_token)

    @mcp.tool(
        name="search_teaching_basis",
        description="封存后反馈专用：检索教学依据条目（未经医学审核的教学草稿），返回条目ID、正文与来源。",
    )
    def search_teaching_basis(turn_token: str, query: str, stage: str | None = None) -> dict[str, Any]:
        return service.search_teaching_basis(turn_token, query, stage)

    @mcp.tool(
        name="submit_feedback",
        description=(
            "封存后反馈专用：提交分环节的评语与改进建议。每项含 stage、comment、improvement、"
            "evidence_event_ids（仅限封存前事件）、basis_ids（仅限教学依据条目）；不得包含分数。"
            "use_stage_basis=true 时只传 stage、comment、improvement，由系统绑定依据与封存评分证据。"
        ),
    )
    def submit_feedback(
        turn_token: str, items: list[dict[str, Any]], use_stage_basis: bool = False
    ) -> dict[str, Any]:
        return service.submit_feedback(turn_token, items, use_stage_basis)

    return mcp


class BearerGate:
    """ASGI wrapper: every HTTP request must carry the shared service bearer token."""

    def __init__(self, app, token: str):
        if not token or len(token) < 16:
            raise ValueError("MCP_SERVICE_TOKEN must be set to at least 16 characters")
        self.app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            supplied = b""
            for name, value in scope.get("headers", []):
                if name == b"authorization" and value[:7].lower() == b"bearer ":
                    supplied = value[7:].strip()
                    break
            if not hmac.compare_digest(supplied, self._token):
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"detail":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def build_service(settings: IntegrationSettings) -> TrustedToolService:
    return TrustedToolService(
        TurnStore(settings.integration_db_path),
        SessionRepository(data_directory() / "app.db"),
        CaseStore(ROOT / "cases", data_directory() / "teacher_cases"),
        KnowledgeStore(settings.knowledge_dir),
    )


def build_asgi_app(settings: IntegrationSettings, transport: str = "streamable-http"):
    mcp = build_mcp(build_service(settings))
    path = "/sse" if transport == "sse" else "/mcp"
    return BearerGate(mcp.http_app(path=path, transport=transport), settings.mcp_service_token)


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    settings = IntegrationSettings.from_env()
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    if transport not in {"streamable-http", "sse"}:
        raise SystemExit("MCP_TRANSPORT must be streamable-http or sse")
    uvicorn.run(
        build_asgi_app(settings, transport),
        host=os.getenv("MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("MCP_PORT", "8765")),
        log_level="info",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
