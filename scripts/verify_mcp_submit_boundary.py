#!/usr/bin/env python3
"""Verify patient-submit observations over HTTP in a separate local MCP process with temporary stores."""
from __future__ import annotations

import json
import sys
import multiprocessing
import os
import socket
import tempfile
from contextlib import contextmanager
import time
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.case_store import CaseStore
from app.integration.turns import TurnStore
from app.integration.config import IntegrationSettings
from app.mcp_server import build_asgi_app
from app.storage import SessionRepository


MCP_TOKEN = "verify-mcp-token-000000000000"


def serve(sock, tmp):
    os.environ.update(APP_DATA_DIR=str(tmp), INTEGRATION_DB_PATH=str(tmp / "integration.db"),
                      KNOWLEDGE_DIR=str(ROOT / "knowledge"), MCP_SERVICE_TOKEN=MCP_TOKEN, NEXENT_MODE="simulator")
    app = build_asgi_app(IntegrationSettings.from_env())
    uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[sock])


@contextmanager
def live_server(tmp):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        address = sock.getsockname()
        process = multiprocessing.get_context("spawn").Process(target=serve, args=(sock, tmp))
        process.start()
        try:
            deadline = time.monotonic() + 30
            while process.is_alive():
                try:
                    with socket.create_connection(address, timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("MCP startup timed out")
                    time.sleep(0.05)
            else:
                raise RuntimeError("MCP exited during startup")
            yield address[1], process.pid
        finally:
            if process.is_alive():
                process.terminate()
            process.join(10)
            if process.is_alive():
                process.kill()
                process.join(5)
            process.close()


def run(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="patient-mcp-boundary-") as tmp:
        report = verify(Path(tmp))
    report["temporary_data_removed"] = not Path(tmp).exists()
    (out_dir / "mcp-submit-boundary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def verify(tmp: Path) -> dict:
    cases = CaseStore(ROOT / "cases")
    repository = SessionRepository(tmp / "app.db")
    turns = TurnStore(tmp / "integration.db")
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

    with live_server(tmp) as (port, child_pid):
        mcp_url = f"http://127.0.0.1:{port}/mcp"

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
                # Calling a different tool on a closed turn must not add a submit boundary.
                await client.call_tool("get_turn_context", {"turn_token": token})
                missing, missing_token = turns.issue(kind="patient", owner_id=owner_id, session_id="absent",
                    case_id=case_id, case_version=case_version, request_id="verify-missing", state_version=0,
                    candidate_fact_ids=[], ttl_s=60)
                failed = (await client.call_tool("submit_fact_selection", {"turn_token": missing_token, "fact_ids": []})).data
                results["failed_code"] = failed.get("error")
                results["missing_id"] = missing.turn_id
                results["turn2_id"] = turn2.turn_id
            return results

        # FastMCP Client is async; run it in a fresh event loop on this thread.
        import asyncio
        tool_results = asyncio.run(drive())

    boundaries1 = turns.boundaries(turn.turn_id)
    boundaries2 = turns.boundaries(tool_results["turn2_id"])
    calls1 = turns.calls(turn.turn_id)
    calls2 = turns.calls(tool_results["turn2_id"])

    report = {
        "schema_version": 1,
        "title": "MCP submit_fact_selection boundary verification",
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": {
            "os": __import__("platform").platform(),
            "python": sys.version.split()[0],
            "timing_source": "mcp_process_wall_clock",
            "driver_pid": os.getpid(), "server_pid": child_pid,
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
            "separate_process": child_pid != os.getpid(),
            "exact_first_turn_events": [b["semantic"] for b in boundaries1] == [
                "submit_fact_selection_before_commit", "submit_fact_selection_committed", "submit_fact_selection_not_open"],
            "all_events_correlated": all(b["request_id"] == request_id for b in boundaries1)
                and all(b["request_id"] == "verify-q2" for b in boundaries2),
            "binding_failure": tool_results["failed_code"] == "session_missing" and
                [(b["request_id"], b["semantic"]) for b in turns.boundaries(tool_results["missing_id"])] ==
                [("verify-missing", "submit_fact_selection_failed")],
            "submission_outcomes": tool_results["first_submit"].get("ok") is True and
                tool_results["duplicate_submit"].get("ok") is False and tool_results["unauthorized_submit"].get("ok") is False
                and turns.get(turn.turn_id).submission == {"fact_ids": candidate_fact_ids[:1]}
                and turns.get(tool_results["turn2_id"]).submission is None,
        },
    }
    report["all_checks_pass"] = all(report["checks"].values())

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
