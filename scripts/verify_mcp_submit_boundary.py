#!/usr/bin/env python3
"""Verify submit_fact_selection boundary recording in an isolated MCP process.

This script starts the trusted MCP server in a background thread (real HTTP sockets, separate
FastMCP instance) and drives it with the FastMCP client, exactly as a fake Nexent agent would.
It records:

* successful submit -> "submit_fact_selection_committed" boundary with request_id and wall clock;
* unauthorized fact -> "submit_fact_selection_rejected" boundary;
* duplicate submit -> "submit_fact_selection_not_open" boundary;
* TurnError/binding failures -> "submit_fact_selection_failed" boundary.

The evidence is written to the directory supplied on the command line. No real vendor is called.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.case_store import CaseStore
from app.integration.config import IntegrationSettings
from app.integration.knowledge import KnowledgeStore
from app.integration.tools import TrustedToolService
from app.integration.turns import TurnStore
from app.mcp_server import BearerGate, build_mcp
from app.storage import SessionRepository


MCP_TOKEN = "verify-mcp-token-000000000000"


class LiveServer:
    def __init__(self, app):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 15
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def run(out_dir: Path) -> dict:
    import shutil

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "mcp-verify-data"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()

    cases = CaseStore(ROOT / "cases")
    repository = SessionRepository(tmp / "sessions.db")
    settings = IntegrationSettings(
        mode="nexent",
        nexent_base_url="",
        nexent_api_key="unused",
        patient_agent_name="unused",
        feedback_agent_name="unused",
        connect_timeout_s=2.0,
        first_event_timeout_s=1.5,
        total_timeout_s=10.0,
        turn_ttl_s=60.0,
        mcp_service_token=MCP_TOKEN,
        integration_db_path=tmp / "integration.db",
        knowledge_dir=ROOT / "knowledge",
    )

    # Build service with its own handles, as a real separate MCP process would.
    turns = TurnStore(settings.integration_db_path)
    service = TrustedToolService(turns, repository, cases, KnowledgeStore(settings.knowledge_dir))
    mcp_app = BearerGate(build_mcp(service).http_app(path="/mcp", transport="streamable-http"), MCP_TOKEN)

    owner_id = "verify-owner"
    session_id = "verify-session"
    case_id = "APPY-001"
    case_version = 2
    request_id = "verify-q1"

    # Seed a minimal session state so the MCP service can resolve the turn.
    from app.models import SessionState
    state = SessionState(
        session_id=session_id,
        owner_id=owner_id,
        case_id=case_id,
        case_version=case_version,
        events=[],
    )
    repository.create(state)

    case = cases.get(case_id, case_version)
    candidate_fact_ids = [fact.fact_id for fact in case.facts if fact.disclosure_level == "on_inquiry"][:4]

    with LiveServer(mcp_app) as mcp_server:
        mcp_url = f"http://127.0.0.1:{mcp_server.port}/mcp"

        # Issue a patient turn from the API side.
        turn, token = turns.issue(
            kind="patient",
            owner_id=owner_id,
            session_id=session_id,
            case_id=case_id,
            case_version=case_version,
            request_id=request_id,
            state_version=state.state_version,
            question="请问既往史和手术史？",
            candidate_fact_ids=candidate_fact_ids,
            ttl_s=60.0,
            mode="nexent",
        )

        async def drive():
            transport = StreamableHttpTransport(mcp_url, headers={"Authorization": f"Bearer {MCP_TOKEN}"})
            results = {}
            async with Client(transport) as client:
                ctx = (await client.call_tool("get_turn_context", {"turn_token": token})).data
                results["context"] = {"question": ctx.get("question"), "candidate_count": len(ctx.get("candidates", []))}

                # 1. Successful submit with one authorized fact.
                sel = candidate_fact_ids[:1]
                ok = (await client.call_tool("submit_fact_selection", {"turn_token": token, "fact_ids": sel})).data
                results["first_submit"] = ok

                # 2. Duplicate submit must be rejected and recorded as not_open.
                dup = (await client.call_tool("submit_fact_selection", {"turn_token": token, "fact_ids": sel})).data
                results["duplicate_submit"] = dup

                # 3. Fresh turn, then unauthorized fact selection.
                turn2, token2 = turns.issue(
                    kind="patient",
                    owner_id=owner_id,
                    session_id=session_id,
                    case_id=case_id,
                    case_version=case_version,
                    request_id="verify-q2",
                    state_version=state.state_version,
                    question="请问诱因？",
                    candidate_fact_ids=candidate_fact_ids,
                    ttl_s=60.0,
                    mode="nexent",
                )
                bad = (await client.call_tool("submit_fact_selection", {"turn_token": token2, "fact_ids": ["not_a_candidate"]})).data
                results["unauthorized_submit"] = bad
                results["turn2_id"] = turn2.turn_id
            return results

        # FastMCP Client is async; run it in a fresh event loop on this thread.
        import asyncio
        tool_results = asyncio.run(drive())

    boundaries1 = turns.boundaries(turn.turn_id)
    boundaries2 = turns.boundaries(tool_results["turn2_id"])
    calls1 = turns.calls(turn.turn_id)
    calls2 = turns.boundaries(tool_results["turn2_id"])

    report = {
        "schema_version": 1,
        "title": "MCP submit_fact_selection boundary verification",
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": {
            "os": __import__("platform").platform(),
            "python": sys.version.split()[0],
            "timing_source": "mcp_process_wall_clock (MCP server thread, real HTTP sockets)",
            "real_provider_calls": 0,
        },
        "turns": {
            "turn1": {
                "turn_id": turn.turn_id,
                "request_id": request_id,
                "mode": "nexent",
                "status_after": turns.get(turn.turn_id).status,
                "tool_results": {
                    "first_submit_ok": tool_results["first_submit"].get("ok"),
                    "duplicate_submit_ok": tool_results["duplicate_submit"].get("ok"),
                },
                "boundaries": boundaries1,
                "calls": calls1,
            },
            "turn2": {
                "turn_id": tool_results["turn2_id"],
                "request_id": "verify-q2",
                "mode": "nexent",
                "status_after": turns.get(tool_results["turn2_id"]).status,
                "tool_results": {"unauthorized_submit_ok": tool_results["unauthorized_submit"].get("ok")},
                "boundaries": boundaries2,
                "calls": calls2,
            },
        },
        "checks": {
            "committed_boundary_present": any(b["semantic"] == "submit_fact_selection_committed" for b in boundaries1),
            "committed_request_id_matches": next((b["request_id"] for b in boundaries1 if b["semantic"] == "submit_fact_selection_committed"), None) == request_id,
            "duplicate_boundary_present": any(b["semantic"] == "submit_fact_selection_not_open" for b in boundaries1),
            "unauthorized_boundary_present": any(b["semantic"] == "submit_fact_selection_rejected" for b in boundaries2),
            "boundary_timestamps_monotonic_within_turn": all(
                boundaries1[i]["at"] <= boundaries1[i + 1]["at"]
                for i in range(len(boundaries1) - 1)
            ) if len(boundaries1) >= 2 else True,
        },
    }
    report["all_checks_pass"] = all(report["checks"].values())

    report_path = out_dir / "mcp-submit-boundary.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path, help="Directory to write evidence")
    args = parser.parse_args()
    report = run(args.out_dir)
    print(json.dumps({
        "all_checks_pass": report["all_checks_pass"],
        "checks": report["checks"],
        "report": str(args.out_dir / "mcp-submit-boundary.json"),
    }, ensure_ascii=False))
    if not report["all_checks_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
