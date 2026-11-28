"""Trusted feedback completion is independent of a model's closing sentence."""
import asyncio
from dataclasses import replace
import time

import httpx
import pytest

from app.integration.tools import FEEDBACK_STAGES
from test_integration import Env, _play_and_seal, select, sse, token_of


@pytest.mark.parametrize("state", ["submitted", "forged", "expired", "cancelled", "basis_mismatch"])
def test_feedback_execution_event_requires_trusted_valid_submission(tmp_path, monkeypatch, state):
    env = Env(tmp_path, monkeypatch, select("f_pain_character"), feedback_protocol="compact")
    sid = _play_and_seal(env)
    original = env.repository.get(sid, "demo-integration").model_dump_json()
    tokens = []

    def agent(_, body, service):
        token = token_of(body)
        tokens.append(token)
        if state != "forged":
            if state == "basis_mismatch":
                service.knowledge.entries = {
                    key: replace(entry, revision=entry.revision + 1)
                    for key, entry in service.knowledge.entries.items()
                }
            items = [{"stage": s, "comment": "请复核本环节得分。", "improvement": "按条目补齐记录。"} for s in FEEDBACK_STAGES]
            assert service.submit_feedback(token, items, True)["ok"]
            turn_id = token.split(".")[0]
            if state == "expired":
                with env.runtime.turns._connect() as c:
                    c.execute("UPDATE turns SET expires_at=0 WHERE turn_id=?", (turn_id,))
            elif state == "cancelled":
                env.runtime.turns.cancel(turn_id, "test_cancel")

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield sse({"type": "execution_logs", "content": '{"ok":true,"accepted_items":5}'})
                if state in {"submitted", "expired", "basis_mismatch"}:
                    raise AssertionError("Do not wait for model prose after trusted submission")
                yield sse({"type": "final_answer", "content": "反馈已提交"})

        return httpx.Response(200, headers={"conversation_id": "73"}, stream=Stream())

    env.agent = agent
    endpoint = f"/api/sessions/{sid}/feedback"
    r = env.client.post(endpoint, headers=env.headers, json={"request_id": "completion"})
    assert r.status_code == (200 if state == "submitted" else 502), r.text
    assert env.repository.get(sid, "demo-integration").model_dump_json() == original
    stored = env.runtime.feedback_store.get(sid, "demo-integration")
    if state == "submitted":
        assert r.json()["source"] == "nexent"
        assert r.json()["review_status"] == "pending_teacher_review" and r.json()["items"] == []
        assert len(stored["items"]) == 5
        trace = env.runtime.feedback_store.trace(sid, "demo-integration")
        assert trace["trusted_completion"] is True
        assert trace["platform_first_final_event_ms"] is None
        assert env.stops == ["/nb/v1/chat/stop/73"]
        assert env.client.get(endpoint, headers=env.headers).json() == r.json()
        assert env.client.post(endpoint, headers=env.headers, json={"request_id": "retry"}).json() == r.json()
        assert len(tokens) == 1
    else:
        assert stored is None


def test_trusted_completion_stop_wait_is_bounded(tmp_path):
    from app.integration.nexent_client import NexentClient
    from test_integration import settings_for

    async def handler(request):
        if request.method == "GET":
            await asyncio.sleep(10)
        return httpx.Response(200, headers={"conversation_id": "73"}, content=sse({"type": "execution_logs"}))

    client = NexentClient(settings_for(tmp_path), httpx.MockTransport(handler))
    started = time.monotonic()
    result = client.run(agent_name="feedback", query="q", trusted_completion=lambda: True)
    assert time.monotonic() - started < 0.75
    assert result.trusted_completion and result.first_final_event_ms is None
